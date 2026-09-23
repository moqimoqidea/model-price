#!/usr/bin/env python3
"""Audit each provider's current official retirement evidence without cache writes.

The report keeps literal model IDs, dated milestones, status-only notices, and
their source URLs together so a reviewer can check each claim on its own page.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_price.core import HttpClient
from model_price.lifecycle import retired_model_ids
from model_price.lifecycle_sources import read_events
from model_price.providers import ALL_PROVIDERS

PROVIDERS = {provider.provider_id: provider for provider in ALL_PROVIDERS}


def audit_provider(provider_id: str, client: Any, as_of: datetime) -> dict[str, Any]:
    """Trace one vendor through its own source reader and preserve failures."""
    provider = PROVIDERS[provider_id]
    try:
        records = provider(client).catalog_records() if provider_id == "aliyun" else []
        source_url, events = read_events(provider_id, client, records)
        if source_url is None:
            raise ValueError("provider has no official retirement reader")
        return {
            "provider": provider_id,
            "status": "verified",
            "source_url": source_url,
            "event_count": len(events),
            "events": [
                {
                    **item,
                    "confirmed_retired_as_of_scan": bool(retired_model_ids([item], as_of)),
                }
                for item in events
            ],
        }
    except Exception as exc:
        return {"provider": provider_id, "status": "source_error", "error": str(exc)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trace model withdrawal evidence from official provider pages"
    )
    parser.add_argument(
        "--provider", action="append", choices=tuple(PROVIDERS),
        help="audit one provider; repeat to select several (default: all 13)",
    )
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument(
        "--timezone", default="Asia/Shanghai",
        help="calendar timezone for date-only notices (default: Asia/Shanghai)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        as_of = datetime.now(timezone.utc).astimezone(ZoneInfo(args.timezone))
    except ZoneInfoNotFoundError as exc:
        raise SystemExit(f"unknown timezone: {args.timezone}") from exc
    client = HttpClient(args.timeout)
    selected = list(dict.fromkeys(args.provider or PROVIDERS))
    reports = [audit_provider(provider_id, client, as_of) for provider_id in selected]
    print(
        json.dumps(
            {"checked_at": as_of.isoformat(), "providers": reports},
            ensure_ascii=False,
            indent=2,
        )
    )
    return int(any(report["status"] == "source_error" for report in reports))


if __name__ == "__main__":
    raise SystemExit(main())
