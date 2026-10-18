"""Index changes by a channel's literal model ID, independently of availability.

A model can change its price, billing modes, and retirement notice in the same
scan. Keeping all those causes together prevents a listing state from replacing
the reason the model appeared in the report.
"""

from __future__ import annotations

from typing import Any

from .diffing import (
    CHANGE_FIELDS,
    PRICE_AMOUNT_CHANGED,
    PRICE_CAPTION_CHANGED,
    PRICE_CHANGE_FIELD,
    PRICE_LINE_ADDED,
    PRICE_LINE_REMOVED,
    PRICE_UNIT_CHANGED,
)
from .models import normalize_model


def lifecycle_change_kind(change: dict[str, Any]) -> str:
    """Distinguish a replacement revision from other official notice changes."""
    if change["kind"] == "detail_revised":
        return {
            "replacement": "replacement_updated",
            "notice_status": "lifecycle_status_updated",
        }.get(change.get("field"), "lifecycle_detail_updated")
    return change["kind"]


def announcement_facts(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep first observations visible without inventing a historical comparison."""
    discovery = report.get("announcements") or {}
    return [*(discovery.get("changes") or []), *(discovery.get("observations") or [])]


def changed_model_groups(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep every change to each model on this provider, without alias merging."""
    models: dict[str, dict[str, Any]] = {}

    def model_entry(item: dict[str, Any], kind: str) -> dict[str, Any]:
        model_id = item["model_id"]
        model = models.setdefault(
            normalize_model(model_id),
            {
                "model_id": model_id,
                "display_name": item.get("display_name") or model_id,
                "model_aliases": list(item.get("model_aliases") or []),
                "change_kinds": [],
                "changes": {},
                "lifecycle_changes": [],
                "announcement_changes": [],
            },
        )
        if kind not in model["change_kinds"]:
            model["change_kinds"].append(kind)
        for alias in item.get("model_aliases") or []:
            if alias not in model["model_aliases"]:
                model["model_aliases"].append(alias)
        return model

    for kind in CHANGE_FIELDS:
        if kind == PRICE_CHANGE_FIELD:
            continue
        for change in (report.get("changes") or {}).get(kind) or []:
            model_entry(change, kind)["changes"].setdefault(kind, []).append(change)
    for change in (report.get("changes") or {}).get(PRICE_CHANGE_FIELD) or []:
        price_kind = change.get("kind")
        if price_kind == PRICE_AMOUNT_CHANGED:
            model_entry(change, PRICE_CHANGE_FIELD)["changes"].setdefault(
                PRICE_CHANGE_FIELD, []
            ).append(change)
        elif price_kind == PRICE_UNIT_CHANGED:
            # A unit correction with the same amount is an archival fact, not a
            # reason to call the channel changed; the JSON still carries it.
            continue
        elif price_kind in (PRICE_LINE_ADDED, PRICE_LINE_REMOVED):
            model_entry(change, "offer_items_changed")["changes"].setdefault(
                "offer_items_changed", []
            ).append(change)
        # A caption change has no amount consequence and no separate cause in a
        # message; it remains in JSON for a reader who asks what the page renamed.
        elif price_kind == PRICE_CAPTION_CHANGED:
            continue
    for change in (report.get("lifecycle") or {}).get("changes") or []:
        model_entry(change["event"], lifecycle_change_kind(change))[
            "lifecycle_changes"
        ].append(change)
    for change in announcement_facts(report):
        item = change["event"]
        # Exact spelling matches may supply a catalogue ID. The announcement's
        # own name remains in the event, and no family alias joins two variants.
        ids = item.get("catalog_model_ids") or []
        canonical = item.get("catalog_model_id")
        target = {
            **item,
            "model_id": canonical or (ids[0] if len(ids) == 1 else item["model_id"]),
        }
        model_entry(target, change["kind"])["announcement_changes"].append(change)
    for item in (report.get("announcements") or {}).get("models") or []:
        ids = item.get("catalog_model_ids") or []
        canonical = item.get("catalog_model_id")
        key = normalize_model(
            canonical or (ids[0] if len(ids) == 1 else item["model_id"])
        )
        if key in models:
            models[key]["announcement"] = item
    return list(models.values())
