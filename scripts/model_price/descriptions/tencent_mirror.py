"""Validation and normalization for the authenticated Tencent model mirror."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from ..models import normalize_model
from ..text import clean_text
from .core import ACTIVE, LEGACY, PREVIEW, RETIRED, UNKNOWN

TENCENT_MODELS_URL = "https://console.cloud.tencent.com/tokenhub/models?regionId=1"
VALID_LIFECYCLES = {ACTIVE, PREVIEW, LEGACY, RETIRED, UNKNOWN}
GENERIC_CATALOGUE_NAMES = {"AI 配音", "语音合成", "语音识别", "音乐生成"}


def _clean_catalogue_name(value: str) -> str:
    """Remove delivery and scheduled-retirement decorations from table labels."""
    value = re.sub(r"\s*[（(]\d{4}-\d{2}-\d{2}\s*下线[）)]\s*$", "", value)
    value = re.sub(r"\s*原厂直供\s*$", "", value)
    return clean_text(value)


def _merge_cards(cards: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse delivery-specific duplicate cards into one model-level record."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for raw in cards:
        model_id = clean_text(str(raw.get("model_id") or ""))
        if not model_id:
            continue
        key = normalize_model(model_id)
        item = deepcopy(raw)
        item["model_id"] = model_id
        item["display_name"] = clean_text(
            str(item.get("display_name") or model_id)
        )
        item["summary"] = clean_text(str(item.get("summary") or ""))
        item["capabilities"] = list(
            dict.fromkeys(
                clean_text(str(value))
                for value in item.get("capabilities") or []
                if clean_text(str(value))
            )
        )
        item["lifecycle"] = item.get("lifecycle") or UNKNOWN
        item["specifications"] = dict(item.get("specifications") or {})
        if key not in merged:
            merged[key] = item
            order.append(key)
            continue

        current = merged[key]
        current["capabilities"] = list(
            dict.fromkeys([*current["capabilities"], *item["capabilities"]])
        )
        badges = {
            value
            for value in (
                current["specifications"].get("badge"),
                item["specifications"].get("badge"),
            )
            if value
        }
        if "原厂直供" in badges:
            current["specifications"]["official_direct_available"] = True
        # Platform boilerplate is delivery-specific, while the ordinary card
        # normally carries the fuller capability/use-case introduction.
        current_is_direct = current["specifications"].get("badge") == "原厂直供"
        item_is_direct = item["specifications"].get("badge") == "原厂直供"
        if current_is_direct and not item_is_direct:
            current["summary"] = item["summary"]
        current["specifications"].pop("badge", None)
    return [merged[key] for key in order]


def _apply_sunset_lifecycle(
    models: Iterable[dict[str, Any]], captured_at: Any
) -> None:
    """Distinguish a scheduled legacy model from one already retired."""
    try:
        captured = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
        if captured.tzinfo is None:
            return
        captured = captured.astimezone(ZoneInfo("Asia/Shanghai"))
    except (TypeError, ValueError):
        return
    for item in models:
        note = str((item.get("specifications") or {}).get("sunset_note") or "")
        match = re.search(r"(\d{1,2})月(\d{1,2})日下线", note)
        if not match:
            continue
        try:
            sunset = date(captured.year, int(match.group(1)), int(match.group(2)))
        except ValueError:
            continue
        item["lifecycle"] = RETIRED if sunset <= captured.date() else LEGACY


def build_tencent_mirror(
    payload: dict[str, Any],
    catalogue: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Normalize captured cards and add public catalogue model-id aliases."""
    models = _merge_cards(payload.get("models") or [])
    _apply_sunset_lifecycle(models, payload.get("captured_at"))
    by_name = {normalize_model(item["display_name"]): item for item in models}
    by_id = {normalize_model(item["model_id"]): item for item in models}
    for row in catalogue:
        alias = clean_text(str(row.get("model_id") or ""))
        display_name = _clean_catalogue_name(str(row.get("display_name") or ""))
        if not alias:
            continue
        item = None
        if display_name and display_name not in GENERIC_CATALOGUE_NAMES:
            item = by_name.get(normalize_model(display_name))
        item = item or by_id.get(normalize_model(alias))
        if item is None:
            continue
        # A compatibility API id can intentionally point at a newer display
        # card while an older card with the same label is still visible. The
        # resolver also checks the record display name, so do not create an
        # ambiguous alias in that case.
        alias_owner = by_id.get(normalize_model(alias))
        if alias_owner is not None and alias_owner is not item:
            continue
        aliases = item.setdefault("aliases", [])
        if normalize_model(alias) != normalize_model(item["model_id"]):
            aliases.append(alias)
        item["aliases"] = list(dict.fromkeys(aliases))

    result = {
        "schema_version": 1,
        "captured_at": payload.get("captured_at"),
        "source_url": payload.get("source_url") or TENCENT_MODELS_URL,
        "models": models,
    }
    validate_tencent_mirror(result)
    return result


def validate_tencent_mirror(payload: dict[str, Any]) -> None:
    """Raise ``ValueError`` when a checked-in mirror is unsafe to consume."""
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    captured_at = payload.get("captured_at")
    try:
        parsed_at = datetime.fromisoformat(str(captured_at).replace("Z", "+00:00"))
        if parsed_at.tzinfo is None:
            raise ValueError("timestamp has no offset")
    except (TypeError, ValueError):
        errors.append("captured_at must be an ISO-8601 timestamp with offset")
    if payload.get("source_url") != TENCENT_MODELS_URL:
        errors.append("source_url must identify the TokenHub model square")
    models = payload.get("models")
    if not isinstance(models, list) or not models:
        errors.append("models must be a non-empty list")
        models = []

    claimed: dict[str, str] = {}
    for index, item in enumerate(models):
        if not isinstance(item, dict):
            errors.append(f"models[{index}] must be an object")
            continue
        label = clean_text(str(item.get("model_id") or ""))
        prefix = f"models[{index}] ({label or 'unnamed'})"
        for field in ("model_id", "display_name", "summary"):
            if not clean_text(str(item.get(field) or "")):
                errors.append(f"{prefix}: {field} is required")
        if item.get("lifecycle") not in VALID_LIFECYCLES:
            errors.append(f"{prefix}: invalid lifecycle")
        if not isinstance(item.get("capabilities"), list):
            errors.append(f"{prefix}: capabilities must be a list")
        if not isinstance(item.get("specifications"), dict):
            errors.append(f"{prefix}: specifications must be an object")
        aliases = item.get("aliases") or []
        if not isinstance(aliases, list):
            errors.append(f"{prefix}: aliases must be a list")
            aliases = []
        for value in [label, *aliases]:
            key = normalize_model(str(value))
            if not key:
                continue
            owner = claimed.setdefault(key, label)
            if owner != label:
                errors.append(f"{prefix}: alias {value!r} is already owned by {owner}")
    if errors:
        raise ValueError("; ".join(errors))
