#!/usr/bin/env python3
"""Compare public model capabilities, catalogues, and prices without credentials.

The implementation lives in the ``model_price`` package beside this file. This
module stays a thin, stable CLI entry point so existing invocations keep working:

    python3 scripts/query_model_prices.py compare deepseek-flash --format message
    python3 scripts/query_model_prices.py delta --format message

``message`` is a plain-text message a DingTalk channel can carry as it stands;
``json`` is the same payload for anything that parses rather than reads it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_price.budget import DEFAULT_MAX_CHARS
from model_price.core import HttpClient, now_iso
from model_price.delta import scan_providers
from model_price.descriptions import build_description_resolver
from model_price.messages import comparison_message, scan_message
from model_price.paths import DEFAULT_CACHE_DIR, DEFAULT_SNAPSHOT_DIR
from model_price.registry import (
    build_adapters,
    catalog_payload,
    query_adapters,
    select_catalog_providers,
    select_compare_providers,
    source_status,
)
from model_price.snapshots import (
    BaselineSelection,
    SnapshotStore,
    parse_baseline_selection,
)
from model_price.updating import update_skill_before_refresh

PROVIDER_OPTION_HELP = "query only this provider; repeat to compare selected providers"

FORMAT_HELP = "json for machines, message for a DingTalk channel"

MAX_CHARS_HELP = (
    "character budget for message output; an overrun is reported without truncation"
)

SINCE_HELP = (
    "baseline to compare with: yesterday, last-month, YYYY-MM-DD, or an ISO "
    "calendar timestamp; omit for the previous successful scan"
)


def baseline_argument(value: str) -> BaselineSelection:
    """Give argparse a concise error for an invalid historical comparison point."""
    try:
        return parse_baseline_selection(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def add_message_options(
    parser: argparse.ArgumentParser, *, default_format: str = "json"
) -> None:
    """Add the output switches shared by every subcommand that renders a message.

    ``--max-chars`` is stated once per subcommand that can render one, because the
    budget belongs to the format rather than to the command: a comparison sent to
    a channel and a scan sent to a channel face the same limit.
    """
    parser.add_argument(
        "--format",
        choices=("json", "message"),
        default=default_format,
        help=FORMAT_HELP,
    )
    parser.add_argument(
        "--max-chars", type=int, default=DEFAULT_MAX_CHARS, help=MAX_CHARS_HELP
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare official model capabilities, catalogues, service variants, "
            "and prices with sources"
        )
    )
    parser.add_argument(
        "--timeout", type=int, default=30, help="HTTP timeout in seconds"
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help=argparse.SUPPRESS
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    compare = subparsers.add_parser(
        "compare", help="explain a model family and compare it across providers"
    )
    compare.add_argument("model")
    compare.add_argument(
        "--exact", action="store_true", help="disable model-family matching"
    )
    compare.add_argument(
        "--provider",
        action="append",
        dest="providers",
        help=PROVIDER_OPTION_HELP,
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
    add_message_options(compare)

    provider = subparsers.add_parser(
        "provider", help="query a model family from one provider"
    )
    provider.add_argument("provider")
    provider.add_argument("model")
    provider.add_argument(
        "--exact", action="store_true", help="disable model-family matching"
    )
    provider.add_argument("--refresh", action="store_true", help="ignore fresh cache")
    add_message_options(provider)

    listing = subparsers.add_parser("list", help="list model IDs from one provider")
    listing.add_argument("provider")
    listing.add_argument("--prefix", default="")
    listing.add_argument("--refresh", action="store_true", help="ignore fresh cache")
    listing.add_argument("--format", choices=("json",), default="json")

    delta = subparsers.add_parser(
        "delta",
        help="scan whole catalogues against the latest or a historical baseline",
    )
    delta.add_argument(
        "--provider",
        action="append",
        dest="providers",
        help=PROVIDER_OPTION_HELP,
    )
    delta.add_argument(
        "--include-overseas",
        action="store_true",
        help="also scan OpenAI, Anthropic, Google, and xAI",
    )
    delta.add_argument("--since", type=baseline_argument, help=SINCE_HELP)
    add_message_options(delta, default_format="message")
    delta.add_argument(
        "--snapshot-dir",
        type=Path,
        default=DEFAULT_SNAPSHOT_DIR,
        help=argparse.SUPPRESS,
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    # A scan answers "did anything move", so it must read the sources afresh: a
    # cache hit would serve the previous scan's data back as "no change".
    refresh = args.command == "delta" or getattr(args, "refresh", False)
    skill_update = update_skill_before_refresh(refresh)
    client = HttpClient(args.timeout)
    adapters = build_adapters(client, cache_dir=args.cache_dir, refresh=refresh)
    descriptions = build_description_resolver(
        client, cache_dir=args.cache_dir, refresh=refresh
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
        payload = query_adapters(
            selected,
            args.model,
            exact=args.exact,
            descriptions=descriptions,
        )
    elif args.command == "provider":
        payload = query_adapters(
            [adapters[args.provider]],
            args.model,
            exact=args.exact,
            descriptions=descriptions,
        )
    elif args.command == "delta":
        payload = scan_providers(
            select_catalog_providers(
                adapters,
                requested=args.providers,
                include_overseas=args.include_overseas,
            ),
            SnapshotStore(args.snapshot_dir),
            descriptions=descriptions,
            baseline=args.since,
        )
    else:
        adapter = adapters[args.provider]
        try:
            payload = catalog_payload(adapter, args.prefix)
        except Exception as exc:
            payload = source_status(adapter, "source_error", now_iso(), str(exc))

    if skill_update:
        payload["skill_update"] = skill_update
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        render = scan_message if args.command == "delta" else comparison_message
        print(render(payload, max_chars=args.max_chars), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
