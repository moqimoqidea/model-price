"""Provider-independent model-description contracts and record shapes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable

from ..core import now_iso
from ..models import normalize_model
from ..text import clean_text

AVAILABLE = "available"
NOT_FOUND = "not_found"
SOURCE_ERROR = "source_error"

ACTIVE = "active"
PREVIEW = "preview"
LEGACY = "legacy"
RETIRED = "retired"
UNKNOWN = "unknown"


def description_record(
    model_id: str,
    display_name: str,
    summary: str,
    source_url: str,
    source_kind: str,
    *,
    source_name: str = "",
    capabilities: Iterable[str] = (),
    lifecycle: str = UNKNOWN,
    specifications: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable description shape emitted by every source."""
    return {
        "model_id": model_id,
        "display_name": display_name or model_id,
        "status": AVAILABLE,
        "summary": clean_text(summary),
        "capabilities": list(
            dict.fromkeys(clean_text(item) for item in capabilities if clean_text(item))
        ),
        "lifecycle": lifecycle,
        "specifications": dict(specifications or {}),
        "source": {
            "name": source_name,
            "url": source_url,
            "kind": source_kind,
            "retrieved_at": now_iso(),
        },
    }


def unavailable_description(
    model_id: str,
    display_name: str = "",
    *,
    status: str = NOT_FOUND,
    note: str = "",
    attempted_sources: Iterable[dict[str, str]] = (),
) -> dict[str, Any]:
    """Describe an honest absence instead of inventing model capabilities."""
    default = (
        "未找到该模型的官方独立介绍；它可能是旧型号、已下线，"
        "或只保留在价格目录中。"
        if status == NOT_FOUND
        else "模型介绍来源暂时无法读取；价格结果不受影响。"
    )
    return {
        "model_id": model_id,
        "display_name": display_name or model_id,
        "status": status,
        "summary": "",
        "capabilities": [],
        "lifecycle": UNKNOWN,
        "specifications": {},
        "note": note or default,
        "attempted_sources": list(attempted_sources),
    }


class DescriptionSource(ABC):
    """One official source capable of describing models it recognizes."""

    source_id: str
    source_name: str
    source_url: str
    source_kind: str

    def __init__(self, client: Any) -> None:
        self.client = client

    @abstractmethod
    def describe(
        self,
        model_id: str,
        display_name: str = "",
        *,
        record: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Return an official description, or ``None`` when there is no match."""

    def describe_many(
        self, targets: Iterable[dict[str, Any]]
    ) -> dict[str, dict[str, Any] | None]:
        """Describe several targets; sources may override for one-fetch catalogues."""
        return {
            normalize_model(target["model_id"]): self.describe(
                target["model_id"],
                target.get("display_name", ""),
                record=target.get("record"),
            )
            for target in targets
        }
