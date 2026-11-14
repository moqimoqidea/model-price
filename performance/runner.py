"""Bound paid experiments and checkpoint every attempt, including warmups and failures."""

from __future__ import annotations

import hashlib
import json
import os
import random
import statistics
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .config import Target
from .prompts import CALIBRATION_TOKENS, DEFAULT_CHARS_PER_TOKEN, corpus, prompt
from .protocols import measure

METRICS = ("ttft_seconds", "headers_seconds", "first_content_seconds", "output_tps",
           "visible_output_tps", "end_to_end_tps", "input_tokens", "output_tokens",
           "cache_hit_ratio")
# Errors that mean "this route will keep refusing", so a paid experiment stops asking.
STOP_ERRORS = {"http_401", "http_402", "http_403", "http_429"}


def plan(targets: list[Target], sizes: list[int], modes: list[str], repeats: int,
         limit: int, max_total_tokens: int, *, calibrate: bool = True) -> dict:
    """Estimate every paid request before one is sent, including the density probes."""
    rounds = len(modes) * repeats + ("warm" in modes)
    requests = len(targets) * len(sizes) * rounds
    probes = len(targets) if calibrate else 0
    input_estimate = len(targets) * sum(sizes) * rounds
    probe_input = probes * CALIBRATION_TOKENS
    # A Hello answers at most 16 tokens and a density probe exactly 1.
    output_ceiling = requests * limit + len(targets) * 16 + probes
    problems = []
    for target in targets:
        if limit > target.max_output_tokens:
            problems.append(f"{target.id}: output limit exceeds configured model limit")
        if any(size + limit > target.context_window for size in sizes):
            problems.append(f"{target.id}: estimated input plus output exceeds configured context window")
    # An input estimate cannot guarantee a monetary ceiling or exact token count.
    estimated_total = input_estimate + probe_input + output_ceiling + len(targets) * 8
    if estimated_total > max_total_tokens:
        problems.append("estimated token budget exceeded; reduce matrix or set --max-total-tokens explicitly")
    return {
        "targets": [target.public() for target in targets], "input_targets": sizes,
        "modes": modes, "repeats": repeats, "max_output_tokens": limit,
        "max_total_tokens": max_total_tokens, "calibration": calibrate,
        "auth_requests": len(targets), "calibration_requests": probes,
        "benchmark_requests": requests,
        "input_tokens_estimate": input_estimate, "calibration_tokens_estimate": probe_input,
        "output_tokens_ceiling": output_ceiling, "total_tokens_estimate": estimated_total,
        "problems": problems,
        "warning": "付费测试：请保证额度充足。输入是估算，输出是上限，缓存未保证命中；不自动重试。",
    }


def aggregate(rows: list[dict]) -> list[dict]:
    """Summarize successful measurements, keeping hits, misses, and route identity apart."""
    groups = defaultdict(list)
    for row in rows:
        if row["phase"] != "measurement":
            continue
        # Do not describe a warm miss as cached, or silently mix returned model revisions.
        key = (row["target"], row.get("model"), row["input_target"], row["mode"],
               row.get("cache_observed", "unknown"), row.get("response_model"))
        groups[key].append(row)
    result = []
    for key, samples in groups.items():
        success = [sample for sample in samples if sample["status"] == "ok"]
        metrics = {}
        for metric in METRICS:
            values = [sample[metric] for sample in success if sample.get(metric) is not None]
            metrics[metric] = ({"n": len(values), "mean": statistics.mean(values),
                                "median": statistics.median(values), "min": min(values),
                                "max": max(values), "stdev": statistics.stdev(values) if len(values) > 1 else None}
                               if values else {"n": 0, "mean": None})
        result.append({
            "target": key[0], "model": key[1], "input_target": key[2], "mode": key[3],
            "cache_observed": key[4], "response_model": key[5],
            # A route may rotate its build mid-run; that must not read as one stable model.
            "system_fingerprints": sorted({sample["system_fingerprint"] for sample in samples
                                           if sample.get("system_fingerprint")}),
            "attempts": len(samples), "successes": len(success),
            "failures": len(samples) - len(success), "metrics": metrics,
        })
    return result


def reserve(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)


def save(path: Path, payload: dict) -> None:
    """Replace only this run's reserved file; owner-only output contains no raw API bodies."""
    fd, name = tempfile.mkstemp(prefix=".benchmark-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def execute(targets: list[Target], experiment: dict, path: Path, *, check_only=False,
            timeout=120.0, pause=1.0, probe=measure, sleep=time.sleep, run_id=None,
            progress=lambda message: None) -> dict:
    reserve(path)
    run_id = run_id or os.urandom(16).hex()
    # The plan owns the paid shape, so a caller cannot silently add probe requests.
    calibrate = bool(experiment.get("calibration", False))
    payload = {"schema_version": 2, "created_at": datetime.now(timezone.utc).isoformat(),
               "state": "running", "plan": experiment, "calibration": {},
               "preflight": [], "samples": [], "summary": []}
    measured_tokens = 0
    ready = []

    def checkpoint():
        payload["summary"] = aggregate(payload["samples"])
        save(path, payload)

    def invoke(target, text, limit, warm, input_estimate):
        nonlocal measured_tokens
        result = probe(target, text, limit, warm, timeout)
        measured_tokens += (result.get("input_tokens") if result.get("input_tokens") is not None else input_estimate)
        measured_tokens += (result.get("output_tokens") if result.get("output_tokens") is not None else limit)
        sleep(pause)
        return result

    def drop(target):
        nonlocal ready
        ready = [item for item in ready if item.id != target.id]

    try:
        checkpoint()
        for target in targets:
            record = {"target": target.id, "status": "running", "error": None}
            payload["preflight"].append(record)
            checkpoint()
            if not target.ready():
                result = {"status": "error", "error": "missing_environment" if target.missing_env else "missing_key",
                          "missing_environment": list(target.missing_environment())}
                progress(f"{target.id}: 缺少环境变量 {', '.join(result['missing_environment'])}；跳过请求")
            else:
                progress(f"{target.id}: Hello 鉴权预检")
                result = invoke(target, "Hello", 16, False, 8)
            record.update(result)
            if result["status"] == "ok":
                ready.append(target)
            checkpoint()
        if check_only:
            payload["state"] = "completed" if len(ready) == len(targets) else "partial"
            return payload
        if not ready:
            payload["state"] = "partial"
            return payload
        # One small paid probe per route replaces the planning constant with that route's own
        # reported token count, so a size requested in tokens is a size actually sent.
        densities = {}
        if calibrate:
            for target in list(ready):
                if measured_tokens + CALIBRATION_TOKENS + 1 > experiment["max_total_tokens"]:
                    payload["state"] = "budget_stopped"
                    return payload
                progress(f"{target.id}: 校准输入密度")
                nonce = hashlib.sha256(f"{run_id}:{target.id}:calibration".encode()).hexdigest()
                text = prompt(corpus(CALIBRATION_TOKENS), nonce)
                result = invoke(target, text, 1, False, CALIBRATION_TOKENS)
                tokens = result.get("input_tokens")
                density = len(text) / tokens if result["status"] == "ok" and tokens else None
                payload["calibration"][target.id] = {
                    "status": result["status"], "error": result.get("error"),
                    "probe_characters": len(text), "probe_tokens": tokens,
                    "chars_per_token": density,
                }
                if density:
                    densities[target.id] = density
                else:
                    progress(f"{target.id}: 密度校准不可用，沿用 {DEFAULT_CHARS_PER_TOKEN} 字符/token")
                checkpoint()
                if result.get("error") in STOP_ERRORS:
                    drop(target)
        # Interleave providers per repeat and deterministically shuffle their order to reduce time bias.
        rng = random.Random(run_id)
        contents: dict[tuple[str, int], str] = {}
        for size in experiment["input_targets"]:
            for mode in experiment["modes"]:
                warm = mode == "warm"
                warm_ready = set()
                rounds = ([0] if warm else []) + list(range(1, experiment["repeats"] + 1))
                for repeat in rounds:
                    ordered = list(ready)
                    rng.shuffle(ordered)
                    for target in ordered:
                        if warm and repeat > 0 and target.id not in warm_ready:
                            continue
                        content = contents.get((target.id, size))
                        if content is None:
                            content = corpus(size, densities.get(target.id, DEFAULT_CHARS_PER_TOKEN))
                            contents[(target.id, size)] = content
                        # Unique across routes too: two protocols can share a vendor's prefix cache.
                        nonce = hashlib.sha256(f"{run_id}:{target.id}:{size}:{mode}:{0 if warm else repeat}".encode()).hexdigest()
                        text = prompt(content, nonce)
                        if measured_tokens + size + experiment["max_output_tokens"] > experiment["max_total_tokens"]:
                            payload["state"] = "budget_stopped"
                            return payload
                        phase = "warmup" if repeat == 0 else "measurement"
                        progress(f"{target.id}: input≈{size}, {mode}, {phase} {repeat}")
                        row = {"target": target.id, "model": target.model, "protocol": target.protocol,
                               "input_target": size, "mode": mode, "phase": phase, "repeat": repeat,
                               "chars_per_token": densities.get(target.id, DEFAULT_CHARS_PER_TOKEN),
                               "status": "running", "error": None}
                        payload["samples"].append(row)
                        checkpoint()
                        result = invoke(target, text, 16 if phase == "warmup" else experiment["max_output_tokens"], warm, size)
                        row.update(result)
                        actual = result.get("input_tokens")
                        row["input_target_ratio"] = actual / size if actual is not None else None
                        row["input_target_within_10_percent"] = (abs(actual - size) <= size * 0.1
                                                                if actual is not None else None)
                        if phase == "warmup" and result["status"] == "ok":
                            warm_ready.add(target.id)
                        checkpoint()
                        # Stop this route on authorization/quota/rate-limit errors rather than pay repeatedly.
                        if result.get("error") in STOP_ERRORS:
                            drop(target)
        failures = any(item["status"] != "ok" for item in payload["preflight"] + payload["samples"])
        failures = failures or any(item["status"] != "ok" for item in payload["calibration"].values())
        payload["state"] = "partial" if failures else "completed"
    except KeyboardInterrupt:
        payload["state"] = "interrupted"
    finally:
        for row in payload["preflight"] + payload["samples"]:
            if row["status"] == "running":
                row.update(status="error", error="interrupted")
        payload["accounted_tokens"] = measured_tokens
        checkpoint()
    return payload


def _stat(group: dict, metric: str) -> float | None:
    return (group.get("metrics", {}).get(metric) or {}).get("mean")


def _spread(group: dict, metric: str) -> float | None:
    return (group.get("metrics", {}).get(metric) or {}).get("stdev")


def _mean(value: float | None, digits: int = 1, unit: str = "") -> str:
    return "unknown" if value is None else f"{value:.{digits}f}{unit}"


def _identity(group: dict) -> str:
    """Name the model this route actually requested; a different alias it answered with is named too."""
    requested = group.get("model")
    returned = group.get("response_model")
    label = f"model={requested}" if requested else "model=未记录"
    if returned and returned != requested:
        label += f"（响应 {returned}）"
    return label


def _timing(group: dict) -> str:
    parts = []
    ttft, ttft_sd = _stat(group, "ttft_seconds"), _spread(group, "ttft_seconds")
    parts.append(f"TTFT={_mean(ttft, 3, 's')}" + (f"±{ttft_sd:.3f}" if ttft_sd is not None else ""))
    headers = _stat(group, "headers_seconds")
    if headers is not None:
        parts.append(f"headers={headers:.3f}s")
    tps, tps_sd = _stat(group, "output_tps"), _spread(group, "output_tps")
    parts.append(f"TPS={_mean(tps)}" + (f"±{tps_sd:.1f}" if tps_sd is not None else ""))
    visible = _stat(group, "visible_output_tps")
    if visible is not None and (tps is None or round(visible, 1) != round(tps, 1)):
        parts.append(f"visible_TPS={visible:.1f}")
    parts.append(f"e2e_TPS={_mean(_stat(group, 'end_to_end_tps'))}")
    cached = _stat(group, "cache_hit_ratio")
    if cached is not None and group.get("cache_observed") == "hit":
        parts.append(f"缓存读取={cached:.1%}")
    if len(group.get("system_fingerprints") or []) > 1:
        parts.append("fingerprint=不一致")
    return "  ".join(parts)


def display(payload: dict) -> str:
    lines = [f"性能测试：{payload['state']}"]
    for item in payload["preflight"]:
        lines.append(f"{item['target']} Hello: {item['status']}" + (f" ({item['error']})" if item.get("error") else ""))
        if item.get("missing_environment"):
            lines.append("  缺少环境变量：" + ", ".join(item["missing_environment"]) + "；export 后重试。")
    for name, entry in payload.get("calibration", {}).items():
        failure = f" ({entry['error']})" if entry.get("error") else ""
        if entry["status"] == "ok" and entry.get("chars_per_token"):
            lines.append(f"{name} 输入密度: {entry['chars_per_token']:.2f} 字符/token")
        else:
            lines.append(f"{name} 输入密度校准: {entry['status']}{failure}")
    for group in payload["summary"]:
        actual = _stat(group, "input_tokens")
        size = group["input_target"]
        if actual is None:
            length = "actual=unknown"
        else:
            deviation = actual / size - 1
            mark = "" if abs(deviation) <= 0.1 else f"（偏离 {deviation:+.0%}）"
            length = f"actual={actual:.0f}{mark}"
        lines.append(f"{group['target']}｜{_identity(group)}")
        lines.append(f"  input≈{size} {length} {group['mode']} "
                     f"cache={group['cache_observed']} n={group['successes']}/{group['attempts']}")
        lines.append("  " + _timing(group))
    return "\n".join(lines)
