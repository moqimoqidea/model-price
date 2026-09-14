#!/usr/bin/env python3
"""Query public model catalogs and prices, with sources, without credentials.

The implementation lives in the ``model_price`` package beside this file. This
module stays a thin, stable CLI entry point so existing invocations keep working:

    python3 scripts/query_model_prices.py compare deepseek-flash --format markdown
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_price.core import HttpClient, now_iso
from model_price.paths import DEFAULT_CACHE_DIR
from model_price.registry import (
    build_adapters,
    catalog_payload,
    query_adapters,
    select_compare_providers,
    source_status,
)
from model_price.reporting import emit
from model_price.updating import update_skill_before_refresh


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query official model catalogs and prices with sources"
    )
    parser.add_argument(
        "--timeout", type=int, default=30, help="HTTP timeout in seconds"
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help=argparse.SUPPRESS
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    compare = subparsers.add_parser(
        "compare", help="compare a model family across all providers"
    )
    compare.add_argument("model")
    compare.add_argument(
        "--exact", action="store_true", help="disable model-family matching"
    )
    compare.add_argument(
        "--provider",
        action="append",
        dest="providers",
        help="query only this provider; repeat to compare selected providers",
    )
    compare.add_argument(
        "--include-overseas",
        action="store_true",
        help="also query OpenAI, Anthropic, Google, and xAI",
    )
    compare.add_argument(
        "--refresh",
        action="store_true",
        help="ignore fresh caches for selected providers",
    )
    compare.add_argument("--format", choices=("json", "markdown"), default="json")

    provider = subparsers.add_parser(
        "provider", help="query a model family from one provider"
    )
    provider.add_argument("provider")
    provider.add_argument("model")
    provider.add_argument(
        "--exact", action="store_true", help="disable model-family matching"
    )
    provider.add_argument("--refresh", action="store_true", help="ignore fresh cache")
    provider.add_argument("--format", choices=("json", "markdown"), default="json")

    listing = subparsers.add_parser("list", help="list model IDs from one provider")
    listing.add_argument("provider")
    listing.add_argument("--prefix", default="")
    listing.add_argument("--refresh", action="store_true", help="ignore fresh cache")
    listing.add_argument("--format", choices=("json",), default="json")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    skill_update = update_skill_before_refresh(args.refresh)
    adapters = build_adapters(
        HttpClient(args.timeout), cache_dir=args.cache_dir, refresh=args.refresh
    )
    selected_provider = getattr(args, "provider", None)
    if selected_provider is not None and selected_provider not in adapters:
        parser.error(
            f"unknown provider: {selected_provider}; choose from {', '.join(adapters)}"
        )
    requested = getattr(args, "providers", None)
    invalid = [
        provider_id for provider_id in requested or [] if provider_id not in adapters
    ]
    if invalid:
        parser.error(
            f"unknown provider: {', '.join(invalid)}; choose from {', '.join(adapters)}"
        )

    if args.command == "compare":
        selected = select_compare_providers(
            adapters,
            args.model,
            requested=args.providers,
            include_overseas=args.include_overseas,
        )
        payload = query_adapters(selected, args.model, exact=args.exact)
    elif args.command == "provider":
        payload = query_adapters(
            [adapters[args.provider]], args.model, exact=args.exact
        )
    else:
        adapter = adapters[args.provider]
        try:
            payload = catalog_payload(adapter, args.prefix)
        except Exception as exc:
            payload = source_status(adapter, "source_error", now_iso(), str(exc))

    if skill_update:
        payload["skill_update"] = skill_update
    emit(payload, args.format)
    return 0


if __name__ == "__main__":
    sys.exit(main())
