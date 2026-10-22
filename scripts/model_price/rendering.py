"""Render saved scans with scoped human summaries, without reading any sources.

Only introduction prose is editable here. Identity, billing, dates, sources, and
outcomes still come from the original scan and its ordinary message renderer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .budget import DEFAULT_MAX_CHARS
from .descriptions.core import AVAILABLE, SUMMARY_MAX_CHARS, summary_needs_condensing
from .messages import scan_message, scan_notes
from .privacy import redact_report_errors
from .reporting import changed_model_details


def read_scan(path: Path) -> dict[str, Any]:
    """Accept a scan artifact, not a query or a snapshot from another run."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("command") != "delta"
        or not isinstance(payload.get("providers"), list)
    ):
        raise ValueError("expected a saved delta payload from --save-payload")
    return payload


def read_introductions(
    path: Path, payload: dict[str, Any]
) -> dict[tuple[str, str], str]:
    """Allow prose only for an available introduction on that exact channel ID."""
    items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError("introductions must be a JSON list of provider, model_id, summary")
    descriptions = {
        (report["provider"]["id"], model["model_id"]): model["description"]
        for report in payload["providers"]
        for model in changed_model_details(report)
    }
    summaries: dict[tuple[str, str], str] = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {"provider", "model_id", "summary"}:
            raise ValueError("each introduction must contain only provider, model_id, summary")
        if not all(isinstance(value, str) and value.strip() for value in item.values()):
            raise ValueError("introduction fields must be nonempty strings")
        key = (item["provider"], item["model_id"])
        description = descriptions.get(key) or {}
        if (
            description.get("status") != AVAILABLE
            or not (description.get("source") or {}).get("url", "").startswith("https://")
        ):
            raise ValueError(f"no available official introduction for {key[0]}/{key[1]}")
        if key in summaries:
            raise ValueError(f"duplicate introduction for {key[0]}/{key[1]}")
        summary = item["summary"].strip()
        if "\t" in item["summary"] or item["summary"].splitlines() != [item["summary"]]:
            raise ValueError("an introduction must be a single plain-text paragraph")
        if summary_needs_condensing(summary):
            raise ValueError(f"introduction exceeds {SUMMARY_MAX_CHARS} characters: {key}")
        summaries[key] = summary
    return summaries


def render_saved_scan(
    path: Path, *, introductions: Path | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Reuse a single scan for successive edits; never update caches or baselines."""
    payload = read_scan(path)
    redact_report_errors(payload)
    summaries = read_introductions(introductions, payload) if introductions else {}
    return scan_message(payload, max_chars=max_chars, introductions=summaries)


def render_saved_notes(path: Path) -> str:
    """The delivery note: what the message counts but does not name."""
    payload = read_scan(path)
    redact_report_errors(payload)
    return scan_notes(payload)
