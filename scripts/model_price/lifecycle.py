"""Archive official retirement milestones separately from price catalogues.

An announcement, an end of new purchases, a redirect, and a service shutdown
are different events. The notice history is independent of price history so a
failed notice read cannot erase a valid price scan or a previously known date.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable

from .lifecycle_sources import read_events
from .paths import LIFECYCLE_SCHEMA_VERSION, SNAPSHOT_SCHEMA_VERSION
from .snapshots import BaselineSelection, SnapshotStore, require_moment

MILESTONES = ("eom_at", "redirect_at", "eos_at")
DATE_FIELDS = ("announced_at", *MILESTONES)


def scan_lifecycle(
    adapter: Any,
    client: Any,
    records: list[dict[str, Any]] | None,
    store: SnapshotStore,
    captured_at: str,
    selection: BaselineSelection,
    reference_at: datetime,
) -> dict[str, Any]:
    """Read the vendor notice now and compare it with retained notice history."""
    provider_id = adapter.provider_id
    key = f"lifecycle-{provider_id}"
    archived = store.read(key)
    if archived and archived.get("lifecycle_schema_version") not in (
        1, LIFECYCLE_SCHEMA_VERSION
    ):
        archived = None
    latest = archived
    if latest and latest.get("lifecycle_schema_version") != LIFECYCLE_SCHEMA_VERSION:
        latest = None
    previous = (
        latest
        if selection.is_latest
        else store.select(key, selection, reference_at=reference_at)
    )
    if (
        previous
        and previous.get("lifecycle_schema_version") != LIFECYCLE_SCHEMA_VERSION
    ):
        previous = None
    if provider_id == "aliyun" and records is None:
        return _failed(
            archived, "price catalogue unavailable; no independent model-market list", reference_at
        )
    try:
        source_url, fresh = read_events(provider_id, client, records or [])
        if source_url is None:
            return {
                "status": "no_public_schedule",
                "source": None,
                "changes": [],
                "baseline_at": None,
            }
        # Indexes may drop older notices. Retain their published dates instead of
        # interpreting an omitted link as a vendor withdrawing the announcement.
        # A schema bump starts a new comparison baseline, but the older archive
        # still carries notices that a rolling index may no longer link to.
        known = dict((archived or {}).get("models") or {})
        seen: set[str] = set()
        for item in fresh:
            model_key = f"{item['scope']}|{item['model_id']}"
            prior = known.get(model_key) or {}
            if model_key in seen:
                # Newer notices occur first in the official indexes. Older
                # repeats can fill a gap, but cannot roll a revision backward.
                known[model_key] = {
                    field: (
                        prior.get(field)
                        if prior.get(field) is not None
                        else item.get(field)
                    )
                    for field in item
                }
            else:
                known[model_key] = {
                    field: (
                        item.get(field)
                        if item.get(field) is not None
                        else prior.get(field)
                    )
                    for field in item
                }
                seen.add(model_key)
        snapshot = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "lifecycle_schema_version": LIFECYCLE_SCHEMA_VERSION,
            "provider": {"id": provider_id, "name": adapter.provider_name},
            "captured_at": captured_at,
            "source": {"url": source_url, "kind": "official_retirement_notice"},
            "models": known,
        }
        changes = (
            [] if previous is None else compare_events(previous, snapshot, reference_at)
        )
        store.write(key, snapshot)
        status = (
            "baseline_created"
            if previous is None and selection.is_latest
            else (
                "baseline_not_found"
                if previous is None
                else "changed" if changes else "unchanged"
            )
        )
        return {
            "status": status,
            "source": snapshot["source"],
            "baseline_at": (previous or {}).get("captured_at"),
            "last_successful_at": (latest or {}).get("captured_at"),
            "event_count": len(known),
            "notice_model_ids": notice_model_ids(known.values()),
            "retired_model_ids": retired_model_ids(known.values(), reference_at),
            "changes": changes,
        }
    except Exception as exc:
        return _failed(archived, str(exc), reference_at)


def _failed(
    latest: dict[str, Any] | None, error: str, reference_at: datetime
) -> dict[str, Any]:
    return {
        "status": "source_error",
        "error": error,
        "source": (latest or {}).get("source"),
        "baseline_at": (latest or {}).get("captured_at"),
        "last_successful_at": (latest or {}).get("captured_at"),
        "notice_model_ids": notice_model_ids(
            ((latest or {}).get("models") or {}).values()
        ),
        "retired_model_ids": retired_model_ids(
            ((latest or {}).get("models") or {}).values(), reference_at
        ),
        "changes": [],
    }


def compare_events(
    previous: dict[str, Any], current: dict[str, Any], reference_at: datetime
) -> list[dict[str, Any]]:
    """Report newly published facts, revised dates, and crossed milestones."""
    changes: list[dict[str, Any]] = []
    before = previous.get("models") or {}
    after = current.get("models") or {}
    old_time = require_moment(previous["captured_at"], label="lifecycle baseline")
    for key in sorted(after):
        item = after[key]
        prior = before.get(key)
        if prior is None:
            changes.append({"kind": "new_notice", "event": item})
            continue
        for field in DATE_FIELDS:
            old, new = prior.get(field), item.get(field)
            if old != new:
                changes.append(
                    {
                        "kind": "date_revised",
                        "milestone": field,
                        "before": old,
                        "after": new,
                        "event": item,
                    }
                )
        for field in MILESTONES:
            when = item.get(field)
            if when and reached_between(when, old_time, reference_at):
                changes.append(
                    {"kind": "milestone_reached", "milestone": field, "event": item}
                )
        for field in ("replacement", "end_behavior", "eos_earliest", "notice_status"):
            if prior.get(field) != item.get(field):
                changes.append(
                    {
                        "kind": "detail_revised",
                        "field": field,
                        "before": prior.get(field),
                        "after": item.get(field),
                        "event": item,
                    }
                )
    return changes


def reached_between(value: str, previous: datetime, current: datetime) -> bool:
    """A date-only promise changes on its local calendar day, without fake time."""
    if len(value) == 10:
        target = date.fromisoformat(value)
        zone = current.tzinfo or timezone.utc
        return (
            previous.astimezone(zone).date() < target <= current.astimezone(zone).date()
        )
    target_time = require_moment(value, label="retirement milestone")
    return previous < target_time <= current


def reached_by(value: str, current: datetime) -> bool:
    """Respect a vendor's date-only precision when checking current service state."""
    if len(value) == 10:
        return date.fromisoformat(value) <= current.date()
    return require_moment(value, label="retirement milestone") <= current


def notice_model_ids(events: Iterable[dict[str, Any]]) -> list[str]:
    """Keep literal IDs for explicit notices, including undated legacy notices."""
    return sorted({item["model_id"] for item in events})


def retired_model_ids(
    events: Iterable[dict[str, Any]], reference_at: datetime
) -> list[str]:
    """Return IDs with an asserted shutdown or automatic replacement already due."""
    retired: set[str] = set()
    for item in events:
        if item.get("notice_status") == "retired":
            retired.add(item["model_id"])
            continue
        eos = item.get("eos_at")
        redirect = item.get("redirect_at")
        if eos and not item.get("eos_earliest") and reached_by(eos, reference_at):
            retired.add(item["model_id"])
        elif (
            redirect
            and item.get("end_behavior") == "redirect"
            and reached_by(redirect, reference_at)
        ):
            retired.add(item["model_id"])
    return sorted(retired)
