#!/usr/bin/env python3
"""Normalize a browser capture into the checked-in Tencent mirror."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_price.core import HttpClient
from model_price.descriptions.registry import DEFAULT_TENCENT_MIRROR
from model_price.descriptions.tencent_mirror import build_tencent_mirror
from model_price.providers.tencent import TencentAdapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="validate and normalize a TokenHub model-square JSON capture"
    )
    parser.add_argument(
        "capture",
        type=Path,
        help="JSON capture produced from the authenticated model square",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_TENCENT_MIRROR)
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="network timeout in seconds for each request attempt",
    )
    parser.add_argument(
        "--no-catalog-aliases",
        action="store_true",
        help="skip public catalogue lookup for API model-id aliases",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = json.loads(args.capture.read_text(encoding="utf-8"))
    catalogue = []
    if not args.no_catalog_aliases:
        catalogue = TencentAdapter(HttpClient(args.timeout))._catalog()
    mirror = build_tencent_mirror(payload, catalogue)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(mirror, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(mirror['models'])} models to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
