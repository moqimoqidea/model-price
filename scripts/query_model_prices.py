#!/usr/bin/env python3
"""Compare public model capabilities, catalogues, and prices without credentials.

The implementation lives in the ``model_price`` package beside this file. This
module stays a thin, stable CLI entry point so existing invocations keep working:

    python3 scripts/query_model_prices.py compare deepseek-flash --format message
    python3 scripts/query_model_prices.py delta --format message

``message`` is a complete plain-text rendering; hosts may supply concise
introductions offline before delivery. ``json`` preserves the original payload.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_price.budget import DEFAULT_MAX_CHARS
from model_price.core import now_iso, write_json
from model_price.delta import scan_providers
from model_price.descriptions import build_description_resolver
from model_price.documents import DocumentClient
from model_price.messages import comparison_message, scan_message
from model_price.paths import DEFAULT_CACHE_DIR, DEFAULT_SNAPSHOT_DIR
from model_price.privacy import safe_diagnostic
from model_price.registry import (
    build_adapters,
    catalog_payload,
    query_adapters,
    select_catalog_providers,
    select_compare_providers,
    source_status,
)
from model_price.rendering import render_saved_scan
from model_price.snapshots import (
    BaselineSelection,
    SnapshotStore,
    parse_baseline_selection,
)
from model_price.updating import update_skill_before_refresh

PROVIDER_OPTION_HELP = "query only this provider; repeat to compare selected providers"

FORMAT_HELP = "json for machines, plain-text message for a person or IM channel"

MAX_CHARS_HELP = (
    "character budget for message output; an overrun is reported without truncation"
)

SINCE_HELP = (
    "baseline to compare with: yesterday, yesterday-first, last-month, YYYY-MM-DD, or an ISO "
    "calendar timestamp; omit for the previous successful scan"
)


def baseline_argument(value: str) -> BaselineSelection:
    """Give argparse a concise error for an invalid historical comparison point."""
    try:
        return parse_baseline_selection(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(safe_diagnostic(exc)) from exc


def timezone_argument(value: str) -> ZoneInfo:
    """Use an explicit calendar zone for relative baselines and scan timestamps."""
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise argparse.ArgumentTypeError(f"unknown timezone: {value}") from exc


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
        "--timeout",
        type=int,
        default=30,
        help="network timeout in seconds for each request attempt",
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
        help="also query all overseas channels",
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
        help="also scan all overseas channels",
    )
    delta.add_argument("--since", type=baseline_argument, help=SINCE_HELP)
    delta.add_argument(
        "--timezone",
        type=timezone_argument,
        help="calendar timezone for the scan and relative baselines, e.g. Asia/Shanghai",
    )
    delta.add_argument(
        "--require-current", action="store_true",
        help="stop before scanning unless the clean checkout matches its fetched upstream",
    )
    delta.add_argument(
        "--save-payload", type=Path,
        help="also save the complete JSON payload for offline report editing",
    )
    add_message_options(delta, default_format="message")
    delta.add_argument(
        "--snapshot-dir",
        type=Path,
        default=DEFAULT_SNAPSHOT_DIR,
        help=argparse.SUPPRESS,
    )

    render = subparsers.add_parser(
        "render", help="render a saved scan without source reads or baseline writes"
    )
    render.add_argument("payload", type=Path)
    render.add_argument(
        "--introductions", type=Path,
        help="channel-scoped introduction summaries in JSON",
    )
    render.add_argument(
        "--max-chars", type=int, default=DEFAULT_MAX_CHARS, help=MAX_CHARS_HELP
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "render":
        try:
            print(render_saved_scan(
                args.payload, introductions=args.introductions, max_chars=args.max_chars,
            ), end="")
        except (OSError, ValueError) as exc:
            parser.error(safe_diagnostic(exc))
        return 0
    if args.command == "delta" and args.save_payload and args.save_payload.exists():
        parser.error(
            f"scan artifact already exists; choose a new path or render it offline: {args.save_payload}"
        )
    # A scan answers "did anything move", so it must read the sources afresh: a
    # cache hit would serve the previous scan's data back as "no change".
    refresh = args.command == "delta" or getattr(args, "refresh", False)
    if getattr(args, "require_current", False):
        skill_update = update_skill_before_refresh(refresh, require_current=True)
        if (
            not skill_update
            or skill_update.get("status") not in {"up_to_date", "updated"}
            or not skill_update.get("verified_revision")
            or skill_update["verified_revision"] != skill_update.get("to_revision")
        ):
            reason = (skill_update or {}).get("reason") or skill_update
            parser.error(f"skill version verification failed: {reason}")
    else:
        skill_update = update_skill_before_refresh(refresh)
    client = DocumentClient(args.timeout)
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
            lifecycle_client=client,
            announcement_client=client,
            baseline=args.since,
            captured_at=(
                datetime.now(args.timezone).isoformat(timespec="seconds")
                if args.timezone
                else None
            ),
        )
    else:
        adapter = adapters[args.provider]
        try:
            payload = catalog_payload(adapter, args.prefix)
        except Exception as exc:
            payload = source_status(adapter, "source_error", now_iso(), str(exc))

    if skill_update:
        payload["skill_update"] = skill_update
    if args.command == "delta" and args.save_payload:
        write_json(args.save_payload, payload, indent=2)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        render = scan_message if args.command == "delta" else comparison_message
        print(render(payload, max_chars=args.max_chars), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
