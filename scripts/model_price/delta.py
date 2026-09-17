"""Scan every provider's whole catalogue and report what changed since last time.

This is the entry point a scheduled run uses: it takes no model name, because the
question it answers is about the catalogues themselves — which models appeared,
which disappeared, and whose prices moved.
"""

from __future__ import annotations

from typing import Any, Iterable

from .core import PriceSource, now_iso
from .descriptions import DescriptionResolver
from .diffing import (
    BASELINE_CREATED,
    CHANGED,
    CHANGE_FIELDS,
    UNCHANGED,
    compare_snapshots,
)
from .models import normalize_model
from .snapshots import SnapshotStore, build_snapshot

SOURCE_ERROR = "source_error"
# A scan that prices nothing is far more likely to be a parser losing the document
# than a vendor withdrawing its whole catalogue. It is reported, and it does not
# replace the baseline: overwriting a full catalogue with an empty one would make
# the next scan read as every model having been withdrawn.
EMPTY_SCAN = "empty_scan"

STATUSES = (CHANGED, UNCHANGED, BASELINE_CREATED, EMPTY_SCAN, SOURCE_ERROR)


def scan_providers(
    adapters: Iterable[PriceSource],
    store: SnapshotStore,
    *,
    captured_at: str | None = None,
    descriptions: DescriptionResolver | None = None,
) -> dict[str, Any]:
    """Scan each provider, compare it with its baseline, and move the baseline on."""
    started = captured_at or now_iso()
    reports = [
        scan_provider(adapter, store, started, descriptions=descriptions)
        for adapter in adapters
    ]
    return {
        "command": "delta",
        "retrieved_at": started,
        "summary": summarize(reports),
        "providers": reports,
    }


def scan_provider(
    adapter: PriceSource,
    store: SnapshotStore,
    captured_at: str,
    *,
    descriptions: DescriptionResolver | None = None,
) -> dict[str, Any]:
    """Read one provider's catalogue, then report it against the stored baseline.

    The baseline only moves once a scan has actually produced a catalogue, so a
    source that breaks leaves the last good baseline in place and the change is
    still visible on the next run.
    """
    previous = store.read(adapter.provider_id)
    try:
        records = adapter.catalog_records()
        snapshot = build_snapshot(adapter, records, captured_at)
    except Exception as exc:
        return failed(adapter, previous, captured_at, str(exc))
    if not snapshot["models"]:
        return failed(
            adapter,
            previous,
            captured_at,
            "the source published no priced model",
            status=EMPTY_SCAN,
        )
    store.write(adapter.provider_id, snapshot)
    report = compare_snapshots(previous, snapshot)
    if descriptions is not None and report["status"] == CHANGED:
        report["model_descriptions"] = descriptions.resolve_many(
            changed_model_targets(report, records, adapter.provider_id)
        )
    return report


def changed_model_targets(
    report: dict[str, Any],
    records: Iterable[dict[str, Any]],
    provider_id: str,
) -> list[dict[str, Any]]:
    """Return each model touched by a delta once, with current metadata if any."""
    current = {
        normalize_model(record.get("model_id", "")): record for record in records
    }
    targets: dict[str, dict[str, Any]] = {}
    for field in CHANGE_FIELDS:
        for change in (report.get("changes") or {}).get(field, []):
            model_id = change.get("model_id", "")
            if not model_id:
                continue
            record = current.get(normalize_model(model_id))
            targets.setdefault(
                normalize_model(model_id),
                {
                    "model_id": model_id,
                    "display_name": change.get("display_name") or model_id,
                    "provider_id": provider_id,
                    "record": record,
                },
            )
    return list(targets.values())


def failed(
    adapter: PriceSource,
    previous: dict[str, Any] | None,
    captured_at: str,
    error: str,
    *,
    status: str = SOURCE_ERROR,
) -> dict[str, Any]:
    """Report a provider that produced no catalogue, keeping its last baseline."""
    return {
        "provider": {"id": adapter.provider_id, "name": adapter.provider_name},
        "status": status,
        "error": error,
        "baseline_at": (previous or {}).get("captured_at"),
        "captured_at": captured_at,
        "source": {
            "url": adapter.source_url,
            "kind": adapter.source_kind,
            "updated_at": ((previous or {}).get("source") or {}).get("updated_at"),
        },
    }


def summarize(reports: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Count what the run found, per status and per kind of change."""
    summary: dict[str, Any] = {
        "providers": 0,
        **{status: 0 for status in STATUSES},
        **{field: 0 for field in CHANGE_FIELDS},
    }
    for report in reports:
        summary["providers"] += 1
        summary[report["status"]] = summary.get(report["status"], 0) + 1
        for field in CHANGE_FIELDS:
            summary[field] += len((report.get("changes") or {}).get(field, []))
    return summary
