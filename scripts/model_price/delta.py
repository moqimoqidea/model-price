"""Scan whole catalogues and compare them with selected historical baselines.

This is the entry point a scheduled run uses: it takes no model name, because the
question it answers is about the catalogues themselves — which models appeared,
which disappeared, and whose prices moved. Baseline selection stays independent
of scanning so the same fresh catalogue can answer latest or point-in-time deltas.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Iterable

from .core import PriceSource, now_iso
from .announcements import announcement_description, scan_announcements
from .changes import changed_model_groups
from .descriptions import DescriptionResolver
from .diffing import (
    BASELINE_CREATED,
    BASELINE_NOT_FOUND,
    CHANGED,
    CHANGE_FIELDS,
    UNCHANGED,
    compare_snapshots,
)
from .models import normalize_model
from .lifecycle import scan_lifecycle
from .snapshots import (
    BaselineSelection,
    SnapshotStore,
    build_snapshot,
    parse_baseline_selection,
    require_moment,
)

SOURCE_ERROR = "source_error"
# An empty catalogue is far more likely to be a parser losing the document than
# a vendor withdrawing every model. A listed model with no published price is
# still a model and must survive into the baseline.
EMPTY_SCAN = "empty_scan"
STATUSES = (
    CHANGED,
    UNCHANGED,
    BASELINE_CREATED,
    BASELINE_NOT_FOUND,
    EMPTY_SCAN,
    SOURCE_ERROR,
)


def scan_providers(
    adapters: Iterable[PriceSource],
    store: SnapshotStore,
    *,
    captured_at: str | None = None,
    descriptions: DescriptionResolver | None = None,
    baseline: BaselineSelection | None = None,
    lifecycle_client: Any | None = None,
    announcement_client: Any | None = None,
) -> dict[str, Any]:
    """Scan each provider, compare it with the requested baseline, and archive it."""
    started = captured_at or now_iso()
    reference = require_moment(started, label="scan timestamp")
    selection = baseline or parse_baseline_selection(None)
    reports = [
        _scan_provider(
            adapter,
            store,
            started,
            descriptions=descriptions,
            selection=selection,
            reference_at=reference,
            lifecycle_client=lifecycle_client,
            announcement_client=announcement_client,
        )
        for adapter in adapters
    ]
    return {
        "command": "delta",
        "retrieved_at": started,
        "baseline_selection": selection.payload(),
        "summary": summarize(reports),
        "providers": reports,
    }


def scan_provider(
    adapter: PriceSource,
    store: SnapshotStore,
    captured_at: str,
    *,
    descriptions: DescriptionResolver | None = None,
    baseline: BaselineSelection | None = None,
    lifecycle_client: Any | None = None,
    announcement_client: Any | None = None,
) -> dict[str, Any]:
    """Read one provider's catalogue, then report it against the stored baseline.

    History only advances once a scan has actually produced a catalogue, so a
    source that breaks leaves every good baseline in place.
    """
    reference = require_moment(captured_at, label="scan timestamp")
    return _scan_provider(
        adapter,
        store,
        captured_at,
        descriptions=descriptions,
        selection=baseline or parse_baseline_selection(None),
        reference_at=reference,
        lifecycle_client=lifecycle_client,
        announcement_client=announcement_client,
    )


def _scan_provider(
    adapter: PriceSource,
    store: SnapshotStore,
    captured_at: str,
    *,
    descriptions: DescriptionResolver | None,
    selection: BaselineSelection,
    reference_at: datetime,
    lifecycle_client: Any | None,
    announcement_client: Any | None,
) -> dict[str, Any]:
    """Run one scan after its shared timestamp and selection are validated."""
    latest = store.read(adapter.provider_id)
    previous = (
        latest
        if selection.is_latest
        else store.select(adapter.provider_id, selection, reference_at=reference_at)
    )
    records: list[dict[str, Any]] | None = None
    try:
        records = adapter.catalog_records()
        snapshot = build_snapshot(adapter, records, captured_at)
    except Exception as exc:
        report = failed(adapter, previous, latest, captured_at, str(exc))
    else:
        report = None
    if report is None and not snapshot["models"]:
        report = failed(
            adapter,
            previous,
            latest,
            captured_at,
            "the source published no model",
            status=EMPTY_SCAN,
        )
    if report is None:
        enrich = getattr(adapter, "enrich_snapshot", None)
        attribution_error = None
        if enrich is not None and getattr(adapter, "aggregated_pricing", False):
            enriched = copy.deepcopy(snapshot)
            try:
                enrich(enriched, previous, latest)
            except Exception as exc:
                attribution_error = str(exc)
            else:
                snapshot = enriched
        store.write(adapter.provider_id, snapshot)
        report = compare_snapshots(
            previous,
            snapshot,
            no_baseline_status=(
                BASELINE_CREATED if selection.is_latest else BASELINE_NOT_FOUND
            ),
        )
        report["last_successful_at"] = (latest or {}).get("captured_at")
        if attribution_error is not None:
            report["pricing_attribution"] = {
                "status": SOURCE_ERROR, "error": attribution_error,
            }
    if lifecycle_client is not None:
        report["lifecycle"] = scan_lifecycle(
            adapter,
            lifecycle_client,
            records,
            store,
            captured_at,
            selection,
            reference_at,
        )
    if announcement_client is not None:
        report["announcements"] = scan_announcements(
            adapter,
            announcement_client,
            records if report["status"] not in (SOURCE_ERROR, EMPTY_SCAN) else None,
            store,
            captured_at,
            selection,
            reference_at,
        )
    report["catalog_url"] = getattr(adapter, "catalog_url", None) or adapter.source_url
    if getattr(adapter, "aggregated_pricing", False):
        report["aggregated_pricing"] = True
    targets = changed_model_targets(report, records or [])
    if targets:
        current_ids = {normalize_model(item["model_id"]) for item in records or []}
        retired_ids = {
            normalize_model(name)
            for name in (report.get("lifecycle") or {}).get("retired_model_ids", [])
        }
        notice_ids = {
            normalize_model(name)
            for name in (report.get("lifecycle") or {}).get("notice_model_ids", [])
        }
        report["model_availability"] = {
            target["model_id"]: listing_state(
                target["model_id"],
                current_ids,
                notice_ids,
                retired_ids,
                catalogue_read=records is not None,
            )
            for target in targets
        }
        removed_ids = {
            normalize_model(model["model_id"])
            for model in (report.get("changes") or {}).get("models_removed") or []
        }
        for target in targets:
            item = target.get("announcement")
            key = normalize_model(target["model_id"])
            if item and key not in current_ids | notice_ids | retired_ids | removed_ids:
                report["model_availability"][target["model_id"]] = (
                    "unknown" if item["catalog_status"] == "unknown" else "announced"
                )
        if descriptions is not None:
            report["model_descriptions"] = descriptions.resolve_many(targets)
        else:
            report["model_descriptions"] = [
                {
                    **target["description"],
                    "model_id": target["model_id"],
                    "provider": report["provider"],
                }
                for target in targets
                if target.get("description")
            ]
    return report


def listing_state(
    model_id: str,
    current_ids: set[str],
    notice_ids: set[str],
    retired_ids: set[str],
    *,
    catalogue_read: bool,
) -> str:
    """A future official notice wins over absence from a pricing-only catalogue."""
    key = normalize_model(model_id)
    if key in retired_ids:
        return "delisted"
    if key in notice_ids or not catalogue_read or key in current_ids:
        return "listed"
    return "delisted"


def changed_model_targets(
    report: dict[str, Any],
    records: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return each model touched by a delta once, with current metadata if any."""
    current = {
        normalize_model(record.get("model_id", "")): record for record in records
    }
    targets = []
    for model in changed_model_groups(report):
        announcement = model.get("announcement")
        target = {
            "model_id": model["model_id"],
            "display_name": model["display_name"],
            "provider_id": report["provider"]["id"],
            "provider_name": report["provider"]["name"],
            "reference_url": (
                announcement["source_url"] if announcement else report["catalog_url"]
            ),
            "record": current.get(normalize_model(model["model_id"])),
        }
        if announcement:
            target["announcement"] = announcement
            target["description"] = announcement.get(
                "description"
            ) or announcement_description(announcement)
        targets.append(target)
    return targets


def failed(
    adapter: PriceSource,
    previous: dict[str, Any] | None,
    latest: dict[str, Any] | None,
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
        "last_successful_at": (latest or {}).get("captured_at"),
        "captured_at": captured_at,
        "source": {
            "url": adapter.source_url,
            "kind": adapter.source_kind,
            "updated_at": ((latest or {}).get("source") or {}).get("updated_at"),
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
        for name, prefix in (
            ("lifecycle", "lifecycle"),
            ("announcements", "announcement"),
        ):
            if name not in report:
                continue
            evidence = report[name]
            count_key, error_key = f"{prefix}_changes", f"{prefix}_source_errors"
            summary[count_key] = summary.get(count_key, 0) + len(
                evidence.get("changes") or []
            )
            if evidence.get("status") == SOURCE_ERROR:
                summary[error_key] = summary.get(error_key, 0) + 1
        if "announcements" in report:
            summary["announcement_observations"] = summary.get(
                "announcement_observations", 0
            ) + len(report["announcements"].get("observations") or [])
    return summary
