"""Index changes by a channel's literal model ID, independently of availability.

A model can change its price, billing modes, and retirement notice in the same
scan. Keeping all those causes together prevents a listing state from replacing
the reason the model appeared in the report.
"""

from __future__ import annotations

from typing import Any

from .diffing import CHANGE_FIELDS
from .models import normalize_model


def lifecycle_change_kind(change: dict[str, Any]) -> str:
    """Distinguish a replacement revision from other official notice changes."""
    if change["kind"] == "detail_revised":
        return {
            "replacement": "replacement_updated",
            "notice_status": "lifecycle_status_updated",
        }.get(change.get("field"), "lifecycle_detail_updated")
    return change["kind"]


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
                "change_kinds": [],
                "changes": {},
                "lifecycle_changes": [],
            },
        )
        if kind not in model["change_kinds"]:
            model["change_kinds"].append(kind)
        return model

    for kind in CHANGE_FIELDS:
        for change in (report.get("changes") or {}).get(kind) or []:
            model_entry(change, kind)["changes"].setdefault(kind, []).append(change)
    for change in (report.get("lifecycle") or {}).get("changes") or []:
        model_entry(change["event"], lifecycle_change_kind(change))[
            "lifecycle_changes"
        ].append(change)
    return list(models.values())
