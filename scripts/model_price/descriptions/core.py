"""Provider-independent model-description contracts and record shapes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable

from ..core import now_iso
from ..text import clean_text

AVAILABLE = "available"
NOT_FOUND = "not_found"
SOURCE_ERROR = "source_error"

ACTIVE = "active"
PREVIEW = "preview"
LEGACY = "legacy"
RETIRED = "retired"
UNKNOWN = "unknown"

# How long the introduction's prose may be in a message. A vendor publishes an
# announcement rather than a summary — one DeepSeek page opens with a whole press
# release — and an introduction is a block of a message that already has to fit a
# channel, so what one may carry is bounded.
#
# The bound is never applied by cutting. Text that stops mid-clause reads as the
# vendor's own complete wording rather than as the first part of it, and a report
# is not improved by prose that ends where a character count said. This layer has
# no model to ask and takes no credentials, so it does not summarize either: it
# marks the summary and keeps the vendor's words whole, and whoever writes the
# message out replaces the marked one with a summary of their own.
SUMMARY_MAX_CHARS = 300


def summary_needs_condensing(summary: str) -> bool:
    """Whether a summary is longer than one message may carry as it stands."""
    return len(summary) > SUMMARY_MAX_CHARS


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
    """Build the stable description shape emitted by every source.

    Every source funnels through here, so this is the one place the length of a
    summary is judged: the checked-in Tencent mirror carries whatever the console
    exported, and it reaches a message on the same terms as a page this tool
    fetched.
    """
    prose = clean_text(summary)
    return {
        "model_id": model_id,
        "display_name": display_name or model_id,
        "status": AVAILABLE,
        "summary": prose,
        "summary_needs_condensing": summary_needs_condensing(prose),
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
        "summary_needs_condensing": False,
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
