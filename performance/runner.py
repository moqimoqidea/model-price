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
from .prompts import corpus, prompt
from .protocols import measure

METRICS = ("ttft_seconds", "first_content_seconds", "output_tps", "visible_output_tps",
           "end_to_end_tps", "input_tokens", "output_tokens", "cache_hit_ratio")


def plan(targets: list[Target], sizes: list[int], modes: list[str], repeats: int,
         limit: int, max_total_tokens: int) -> dict:
    requests = len(targets) * len(sizes) * (len(modes) * repeats + ("warm" in modes))
    input_estimate = len(targets) * sum(sizes) * (len(modes) * repeats + ("warm" in modes))
    output_ceiling = requests * limit + len(targets) * 16
    problems = []
    for target in targets:
        if limit > target.max_output_tokens:
            problems.append(f"{target.id}: output limit exceeds configured model limit")
        if any(size + limit > target.context_window for size in sizes):
            problems.append(f"{target.id}: estimated input plus output exceeds configured context window")
    # An input estimate cannot guarantee a monetary ceiling or exact token count.
    estimated_total = input_estimate + output_ceiling + len(targets) * 8
    if estimated_total > max_total_tokens:
        problems.append("estimated token budget exceeded; reduce matrix or set --max-total-tokens explicitly")
    return {
        "targets": [target.public() for target in targets], "input_targets": sizes,
        "modes": modes, "repeats": repeats, "max_output_tokens": limit,
        "auth_requests": len(targets), "benchmark_requests": requests,
        "input_tokens_estimate": input_estimate, "output_tokens_ceiling": output_ceiling,
        "total_tokens_estimate": estimated_total, "max_total_tokens": max_total_tokens,
        "problems": problems,
        "warning": "付费测试：请保证额度充足。输入是估算，输出是上限，缓存未保证命中；不自动重试。",
    }


def aggregate(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        if row["phase"] != "measurement":
            continue
        # Do not describe a warm miss as cached, or silently mix returned model revisions.
        key = (row["target"], row["input_target"], row["mode"], row.get("cache_observed", "unknown"),
               row.get("response_model"))
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
            "target": key[0], "input_target": key[1], "mode": key[2], "cache_observed": key[3],
            "response_model": key[4], "attempts": len(samples), "successes": len(success),
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
    payload = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
               "state": "running", "plan": experiment, "preflight": [], "samples": [], "summary": []}
    measured_tokens = 0

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

    try:
        checkpoint()
        ready = []
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
        # Interleave providers per repeat and deterministically shuffle their order to reduce time bias.
        rng = random.Random(run_id)
        for size in experiment["input_targets"]:
            content = corpus(size)
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
                        # Unique across routes too: two protocols can share a vendor's prefix cache.
                        nonce = hashlib.sha256(f"{run_id}:{target.id}:{size}:{mode}:{0 if warm else repeat}".encode()).hexdigest()
                        text = prompt(content, nonce)
                        if measured_tokens + size + experiment["max_output_tokens"] > experiment["max_total_tokens"]:
                            payload["state"] = "budget_stopped"
                            return payload
                        phase = "warmup" if repeat == 0 else "measurement"
                        progress(f"{target.id}: input≈{size}, {mode}, {phase} {repeat}")
                        row = {"target": target.id, "protocol": target.protocol, "input_target": size,
                               "mode": mode, "phase": phase, "repeat": repeat,
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
                        if result.get("error") in {"http_401", "http_402", "http_403", "http_429"}:
                            ready = [item for item in ready if item.id != target.id]
        failures = any(item["status"] != "ok" for item in payload["preflight"] + payload["samples"])
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


def display(payload: dict) -> str:
    lines = [f"性能测试：{payload['state']}"]
    for item in payload["preflight"]:
        lines.append(f"{item['target']} Hello: {item['status']}" + (f" ({item['error']})" if item.get("error") else ""))
        if item.get("missing_environment"):
            lines.append("  缺少环境变量：" + ", ".join(item["missing_environment"]) + "；export 后重试。")
    for group in payload["summary"]:
        def mean(name):
            value = group["metrics"][name]["mean"]
            return f"{value:.3f}" if value is not None else "unknown"
        lines.append(
            f"{group['target']} input≈{group['input_target']} {group['mode']} cache={group['cache_observed']} "
            f"n={group['successes']}/{group['attempts']} TTFT={mean('ttft_seconds')}s "
            f"TPS={mean('output_tps')} visible_TPS={mean('visible_output_tps')} "
            f"actual_input={mean('input_tokens')}"
        )
    return "\n".join(lines)
