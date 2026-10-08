"""Preserve authenticated Tencent evidence without conflating API model identities."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, datetime
from typing import Any, Iterable
from urllib.parse import quote
from zoneinfo import ZoneInfo

from ..models import normalize_model
from ..text import clean_text
from .core import ACTIVE, LEGACY, PREVIEW, RETIRED, UNKNOWN

TENCENT_MODELS_URL = "https://console.cloud.tencent.com/tokenhub/models?regionId=1"
VALID_LIFECYCLES = {ACTIVE, PREVIEW, LEGACY, RETIRED, UNKNOWN}
TENCENT_MIRROR_SCHEMA_VERSION = 2
GENERIC_CATALOGUE_NAMES = {"AI 配音", "语音合成", "语音识别", "音乐生成"}

MODEL_SPEC_FIELDS = {
    "ContextLength": "context_window",
    "MaxInputToken": "max_input_tokens",
    "MaxOutputToken": "max_output_tokens",
    "TPM": "tokens_per_minute",
    "QPM": "requests_per_minute",
    "Concurrency": "concurrency",
    "InputDescription": "input_constraints",
}
CONSOLE_LIFECYCLES = {
    "online": ACTIVE,
    "pre-offline": LEGACY,
    "discontinued": LEGACY,
    "offline": RETIRED,
}
PRIVATE_CONSOLE_FIELDS = {
    "csrfcode", "owneruin", "uin", "apikey", "api_key", "cookie", "secretkey",
}
PRIVATE_CONSOLE_PATTERN = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}|[?&](?:csrfCode|ownerUin|uin)="
)


def _has_session_data(value: Any) -> bool:
    """Reject console session secrets while admitting official placeholder examples."""
    if isinstance(value, dict):
        return any(
            str(key).lower() in PRIVATE_CONSOLE_FIELDS or _has_session_data(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_session_data(item) for item in value)
    return isinstance(value, str) and bool(
        PRIVATE_CONSOLE_PATTERN.search(value)
    )


def _console_cards(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep each console API ID and its own specification/capability evidence."""
    if _has_session_data(payload):
        raise ValueError("console capture must not contain credentials or account data")
    cards = payload["model_cards"]
    if not isinstance(cards, list) or not cards:
        raise ValueError("model_cards must be a non-empty list")
    if any(not isinstance(card, dict) for card in cards):
        raise ValueError("model_cards must contain objects")
    if payload.get("catalogue_total") != len(cards):
        raise ValueError("model_cards must cover the console's catalogue_total")
    capability_sets = payload.get("capability_sets")
    if not isinstance(capability_sets, dict):
        raise ValueError("capability_sets must be keyed by literal console model ID")
    configs: dict[str, list[dict[str, Any]]] = {}
    captured_configs = payload.get("generation_configs") or []
    if not isinstance(captured_configs, list) or any(
        not isinstance(config, dict) or not config.get("ModelID")
        for config in captured_configs
    ):
        raise ValueError("generation_configs must contain objects with ModelID")
    for config in captured_configs:
        model_id = str(config.get("ModelID") or "")
        key = normalize_model(model_id)
        if config not in configs.setdefault(key, []):
            configs[key].append(deepcopy(config))
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards:
        model_id = clean_text(str(card.get("ModelId") or ""))
        if not model_id or normalize_model(model_id) in seen:
            raise ValueError("console cards require distinct literal ModelId values")
        seen.add(normalize_model(model_id))
        if model_id not in capability_sets:
            raise ValueError(f"capabilities were not captured for {model_id}")
        capabilities = capability_sets[model_id]
        if not isinstance(capabilities, list) or any(
            not isinstance(item, dict) for item in capabilities
        ):
            raise ValueError(f"capabilities must be a list for {model_id}")
        specifications: dict[str, Any] = {"brand": card.get("Brand") or ""}
        if card.get("ReleaseAt"):
            specifications["released_at"] = str(card["ReleaseAt"]).split("T")[0]
        raw_spec = dict(card.get("ModelSpec") or {})
        for field, name in MODEL_SPEC_FIELDS.items():
            if raw_spec.get(field) not in (None, ""):
                specifications[name] = clean_text(str(raw_spec[field]))
        for field, name in (
            ("OfflineAt", "offline_at"), ("DiscontinuedAt", "discontinued_at")
        ):
            if card.get(field):
                specifications[name] = card[field]
        if card.get("Provider"):
            specifications["badge"] = card["Provider"]
        labels = list(card.get("Tags") or [])
        modalities: dict[str, list[str]] = {"input": [], "output": []}
        protocols: list[str] = []
        for capability in capabilities:
            name = str(capability.get("CapabilityName") or "")
            label = clean_text(str(capability.get("DisplayName") or name))
            value = str(capability.get("CapabilityValue") or "").strip().lower()
            if name.endswith("API") and value not in ("", "false"):
                protocols.append(label)
            if value != "true":
                continue
            direction = next(
                (item for item in modalities if name.startswith(item.title())), ""
            )
            if direction:
                suffix = "输入" if direction == "input" else "输出"
                modalities[direction].append(label.removesuffix(suffix))
            else:
                labels.append(label)
        for direction, values in modalities.items():
            if values:
                specifications[f"{direction}_modalities"] = "、".join(
                    dict.fromkeys(values)
                )
        if protocols:
            specifications["api_protocols"] = "、".join(dict.fromkeys(protocols))
        metadata = {
            "captured_at": payload.get("captured_at"),
            "short_summary": card.get("Summary"),
            "model_type": card.get("ModelType"),
            "model_series": card.get("ModelSeries"),
            "provider": card.get("Provider"),
            "status": card.get("Status"),
            "release_at": card.get("ReleaseAt"),
            "updated_at": card.get("UpdatedAt"),
            "model_spec": deepcopy(raw_spec),
            "capabilities": deepcopy(capabilities),
            "api_info": deepcopy(card.get("ModelAPIInfo") or {}),
            "charging_info": deepcopy(card.get("ModelChargingInfo") or []),
            "generation_configs": configs.get(normalize_model(model_id), []),
        }
        models.append({
            "model_id": model_id,
            "display_name": clean_text(str(card.get("DisplayName") or model_id)),
            "summary": clean_text(str(card.get("Description") or "")),
            "capabilities": list(dict.fromkeys(labels)),
            "lifecycle": CONSOLE_LIFECYCLES.get(card.get("Status"), UNKNOWN),
            "specifications": specifications,
            "aliases": list(card.get("ExtraModelIds") or []),
            "source_url": (
                "https://console.cloud.tencent.com/tokenhub/models/detail?modelId="
                + quote(model_id, safe="") + "&regionId=1"
            ),
            "console_metadata": metadata,
        })
    return models


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
        offline_at = (item.get("specifications") or {}).get("offline_at")
        if offline_at:
            try:
                offline = datetime.fromisoformat(str(offline_at).replace("Z", "+00:00"))
                if offline.tzinfo is not None:
                    item["lifecycle"] = RETIRED if offline <= captured else LEGACY
                    continue
            except ValueError:
                pass
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
    models = _merge_cards(
        _console_cards(payload) if "model_cards" in payload else payload.get("models") or []
    )
    _apply_sunset_lifecycle(models, payload.get("captured_at"))
    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in models:
        by_name.setdefault(normalize_model(item["display_name"]), []).append(item)
    by_id = {normalize_model(item["model_id"]): item for item in models}
    for row in catalogue:
        alias = clean_text(str(row.get("model_id") or ""))
        display_name = _clean_catalogue_name(str(row.get("display_name") or ""))
        if not alias:
            continue
        item = by_id.get(normalize_model(alias))
        if item is None and display_name and display_name not in GENERIC_CATALOGUE_NAMES:
            matches = by_name.get(normalize_model(display_name), [])
            if len(matches) > 1:
                delivery_mode = row.get("delivery_mode")
                if "原厂直供" in str(row.get("display_name") or ""):
                    delivery_mode = "upstream_direct"
                if delivery_mode in ("upstream_direct", "self_deployed"):
                    matches = [
                        match for match in matches
                        if (match["specifications"].get("badge") == "原厂直供")
                        == (delivery_mode == "upstream_direct")
                    ]
            if len(matches) == 1:
                item = matches[0]
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
        "schema_version": TENCENT_MIRROR_SCHEMA_VERSION,
        "captured_at": payload.get("captured_at"),
        "source_url": payload.get("source_url") or TENCENT_MODELS_URL,
        "models": models,
    }
    validate_tencent_mirror(result)
    return result


def validate_tencent_mirror(payload: dict[str, Any]) -> None:
    """Raise ``ValueError`` when a checked-in mirror is unsafe to consume."""
    if not isinstance(payload, dict):
        raise ValueError("mirror must be an object")
    errors: list[str] = []
    if _has_session_data(payload):
        errors.append("credentials or account data are forbidden")
    if payload.get("schema_version") not in (1, TENCENT_MIRROR_SCHEMA_VERSION):
        errors.append("schema_version must be 1 or 2")
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
        if "console_metadata" in item and not isinstance(item["console_metadata"], dict):
            errors.append(f"{prefix}: console_metadata must be an object")
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
