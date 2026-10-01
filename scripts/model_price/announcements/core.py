"""Archive release discoveries independently from catalogues and retirement notices.

An official announcement proves publication, not a usable API or a billed price.
Rolling indexes cannot retract known models, and a broken reader cannot advance
this history or alter a successful price baseline.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from ..descriptions.core import description_record, unavailable_description
from ..models import normalize_model
from ..paths import ANNOUNCEMENT_SCHEMA_VERSION
from ..snapshots import BaselineSelection, SnapshotStore
from .sources import read_publications


def catalogue_ids(
    item: dict[str, Any], records: list[dict[str, Any]] | None
) -> list[str]:
    """Match literal published names on this host, without family or retired aliases."""
    keys = {normalize_model(item["model_id"]), normalize_model(item["display_name"])}
    return sorted(
        {
            record["model_id"]
            for record in records or []
            if keys
            & {
                normalize_model(record["model_id"]),
                normalize_model(record.get("display_name") or ""),
            }
        }
    )


def announcement_description(item: dict[str, Any]) -> dict[str, Any]:
    """Keep the announcement's own prose, without inventing capabilities or an ID."""
    if not item["summary"]:
        return {
            **unavailable_description(
                item["model_id"],
                item["display_name"],
                note="官方模型索引已公布该模型；当前可读取页面未给出独立能力说明。",
            ),
            "reference_url": item["source_url"],
        }
    description = description_record(
        item["model_id"],
        item["display_name"],
        item["summary"],
        item["source_url"],
        "official_model_announcement",
        source_name=item["source_name"],
        lifecycle="preview" if "preview" in item["display_name"].lower() else "unknown",
    )
    description["announcement_access"] = item["access"]
    return description


def scan_announcements(
    adapter: Any,
    client: Any,
    records: list[dict[str, Any]] | None,
    store: SnapshotStore,
    captured_at: str,
    selection: BaselineSelection,
    reference_at: datetime,
) -> dict[str, Any]:
    """Read official discoveries fresh and keep every prior rolling-index entry."""
    history_key = f"announcements-{adapter.provider_id}"
    latest = store.read(history_key)
    previous = (
        latest
        if selection.is_latest
        else store.select(history_key, selection, reference_at=reference_at)
    )
    result: dict[str, Any] = {
        "baseline_at": (previous or {}).get("captured_at"),
        "last_successful_at": (latest or {}).get("captured_at"),
        "changes": [],
        "observations": [],
    }
    discovery = read_publications(adapter.provider_id, client, reference_at)
    result.update({field: discovery[field] for field in ("source", "coverage", "note")})
    if discovery["status"] not in ("available", "source_error"):
        return {
            **result,
            "status": discovery["status"],
            "models": list(((latest or {}).get("models") or {}).values()),
        }
    known = deepcopy((previous or {}).get("models") or {})
    known.update(deepcopy((latest or {}).get("models") or {}))
    fresh: dict[str, dict[str, Any]] = {}
    for item in discovery["models"]:
        key = normalize_model(item["model_id"])
        if key not in fresh or _evidence_order(item) > _evidence_order(fresh[key]):
            fresh[key] = item
    for item in fresh.values():
        model_key = normalize_model(item["model_id"])
        prior = known.get(model_key) or {}
        # Older repeats may fill a gap, never roll a newer announcement backward.
        if prior and (
            item.get("detail_error")
            or _publication_time(item) < _publication_time(prior)
        ):
            continue
        known[model_key] = {**prior, **item}
    for item in known.values():
        ids = catalogue_ids(item, records)
        item["catalog_model_ids"] = ids
        item["catalog_status"] = (
            "unknown" if records is None else "listed" if ids else "not_listed"
        )
    if not known:
        # A valid news index can contain only non-model news. An empty discovery
        # never erases the last successful model history or claims a baseline.
        return {
            **result,
            "status": (
                discovery["status"]
                if discovery["status"] == "source_error"
                else "no_announcements"
            ),
            "error": "；".join(discovery.get("errors") or []),
            "models": [],
        }
    if previous:
        changes = compare_announcements(previous, known)
        status = "changed" if changes else "unchanged"
    else:
        changes = (
            [
                {"kind": "announcement_observed", "event": item}
                for item in known.values()
            ]
            if selection.is_latest
            else []
        )
        status = "baseline_created" if selection.is_latest else "baseline_not_found"
        if not selection.is_latest:
            result["observations"] = [
                {"kind": "announcement_observed", "event": known[key]}
                for key in fresh
                if key not in ((latest or {}).get("models") or {})
            ]
    snapshot = {
        "announcement_schema_version": ANNOUNCEMENT_SCHEMA_VERSION,
        "captured_at": captured_at,
        "provider": {"id": adapter.provider_id, "name": adapter.provider_name},
        "source": discovery["source"],
        "models": known,
    }
    if discovery["status"] == "source_error":
        return {
            **result,
            "status": "source_error",
            "error": "；".join(discovery["errors"]),
            "model_count": len(known),
            "models": list(known.values()),
            "changes": changes,
        }
    store.write(history_key, snapshot)
    return {
        **result,
        "status": status,
        "model_count": len(known),
        "models": list(known.values()),
        "changes": changes,
    }


def compare_announcements(
    previous: dict[str, Any], current: dict[str, Any]
) -> list[dict[str, Any]]:
    """Report discoveries and explicit access, announced-price, and listing changes."""
    changes = []
    for key, item in current.items():
        prior = (previous.get("models") or {}).get(key)
        if prior is None:
            changes.append({"kind": "model_announced", "event": item})
            continue
        for field, kind in (
            ("access", "access_changed"),
            ("announced_offers", "announced_price_changed"),
            ("catalog_status", "announcement_listing_changed"),
        ):
            before, after = prior.get(field), item.get(field)
            if field == "access":
                before = {
                    key: (before or {}).get(key)
                    for key in ("status", "developers", "consumers")
                }
                after = {
                    key: (after or {}).get(key)
                    for key in ("status", "developers", "consumers")
                }
            elif field == "announced_offers":
                # Announcement prose is evidence, not a price change by itself.
                # Compare each published rate while retaining its full terms.
                before = _announced_rates(before or [])
                after = _announced_rates(after or [])
            if before != after and (
                field != "catalog_status" or "unknown" not in (before, after)
            ):
                changes.append(
                    {
                        "kind": kind,
                        "field": field,
                        "before": before,
                        "after": after,
                        "event": item,
                    }
                )
    return changes


def _announced_rates(offers: list[dict[str, Any]]) -> list[list[tuple[Any, ...]]]:
    return [
        [
            (price.get("type"), price.get("amount"), price.get("unit"))
            for price in offer["prices"]
        ]
        for offer in offers
    ]


def _publication_time(item: dict[str, Any]) -> float:
    value = item.get("published_at")
    if not value:
        return float("-inf")
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment.replace(tzinfo=moment.tzinfo or timezone.utc).timestamp()


def _evidence_order(item: dict[str, Any]) -> tuple[float, bool, bool, bool]:
    """Prefer newer complete evidence over repeated navigation or failed details."""
    return (
        _publication_time(item),
        not bool(item.get("detail_error")),
        bool(item.get("summary")),
        bool((item.get("access") or {}).get("statements")),
    )
