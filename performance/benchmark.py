"""Expose a separate opt-in benchmark CLI without changing the credential-free catalogue CLI."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from performance.config import load_targets, token_count
from performance.runner import display, execute, plan

ROOT = Path(__file__).resolve().parents[1]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Opt-in paid TTFT/TPS comparison; plan is offline.")
    result.add_argument("command", choices=("env", "plan", "check", "run", "report"))
    result.add_argument("--config", type=Path, default=ROOT / "performance/targets.example.json")
    result.add_argument("--target", action="append", help="literal target id; repeat to select several")
    result.add_argument("--input-tokens", default="10K", help="approximate sizes, e.g. 10K,50K,500K,800K; K=1000")
    result.add_argument("--repeats", type=int, default=3, help="1 for smoke tests; normally 3–5")
    result.add_argument("--modes", default="cold,warm")
    result.add_argument("--thinking", choices=("on", "off", "provider-default"), help="verified provider mapping only; default from target config")
    result.add_argument("--max-output-tokens", type=token_count, default=256)
    result.add_argument("--max-total-tokens", type=token_count, default=500000, help="estimated run budget, not a currency cap")
    result.add_argument("--timeout", type=float, default=120, help="socket timeout and between-event deadline, seconds")
    result.add_argument("--pause", type=float, default=1, help="seconds between paid calls")
    result.add_argument("--output", type=Path, help="new JSON file; defaults to ignored cache/performance/")
    result.add_argument("--input", type=Path, help="saved JSON for offline report")
    return result


def main(argv=None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    try:
        if args.command == "report":
            if not args.input:
                cli.error("report requires --input")
            print(display(json.loads(args.input.read_text(encoding="utf-8"))))
            return 0
        if not 1 <= args.repeats <= 5:
            cli.error("--repeats must be 1–5 (recommended 3–5)")
        if not math.isfinite(args.timeout) or args.timeout <= 0 or not math.isfinite(args.pause) or args.pause < 0:
            cli.error("--timeout must be positive and --pause nonnegative; both must be finite")
        sizes = list(dict.fromkeys(token_count(item) for item in args.input_tokens.split(",")))
        modes = list(dict.fromkeys(args.modes.split(",")))
        if not modes or set(modes) - {"cold", "warm"}:
            cli.error("--modes must contain cold and/or warm")
        targets = load_targets(args.config, args.target)
        if args.command == "env":
            for target in targets:
                missing = target.missing_environment()
                print(f"{target.id}: " + ("缺少环境变量 " + ", ".join(missing) + "；不会发送请求。"
                                        if missing else "环境变量已就绪（未验证授权）。"))
            print("仅检查当前进程环境，不联网。变量写在 ~/.zshrc 时，请先加载配置，或用 zsh -ic 启动本命令。")
            return 0 if all(target.ready() for target in targets) else 1
        if args.thinking:
            targets = [replace(target, thinking_mode=args.thinking) for target in targets]
        experiment = plan(targets, sizes, modes, args.repeats, args.max_output_tokens, args.max_total_tokens)
        if args.command == "plan":
            print(json.dumps(experiment, ensure_ascii=False, indent=2))
            return 0
        print(experiment["warning"], file=sys.stderr)
        if args.command == "run" and experiment["problems"]:
            for problem in experiment["problems"]:
                print(problem, file=sys.stderr)
            return 2
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        output = args.output or ROOT / "cache/performance" / f"{stamp}-{args.command}.json"
        payload = execute(targets, experiment, output, check_only=args.command == "check",
                          timeout=args.timeout, pause=args.pause,
                          progress=lambda value: print(value, file=sys.stderr, flush=True))
        print(display(payload))
        print(f"JSON: {output}")
        return 0 if payload["state"] == "completed" else 1
    except (OSError, ValueError, TypeError):
        # Configs, exception strings and URLs can carry private values. Never echo them.
        print("配置或文件操作失败。检查 JSON 字段、HTTPS 端点、目标名称及输出文件是否已存在。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
