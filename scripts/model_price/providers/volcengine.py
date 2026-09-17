"""Volcengine Ark (方舟) pricing, read from its official Markdown document.

The documentation API returns the page both as structured Slate JSON and as the
Markdown its "复制markdown" button produces. The Markdown is parsed because it is
the vendor's own table rendering: it keeps one header row per table, so the
shared table reader aligns columns without extra work.
"""

from __future__ import annotations

import json
from typing import Any

from .base import TabularTokenPricingAdapter
from ..errors import SourceError
from ..parsing import describes_request_length
from ..text import clean_text, numeric_values

VOLCENGINE_PAGE_URL = "https://docs.volcengine.com/docs/82379/1544106"
VOLCENGINE_DOC_API = (
    "https://docs.volcengine.com/api/doc/getDocDetail"
    "?DocumentID=1544106&LibraryID=82379&lang=zh"
)


def volc_offer_name(headings: list[str]) -> str:
    title = " / ".join(headings)
    if "低延迟" in title:
        return "online_low_latency"
    if "批量推理" in title:
        return "batch"
    if "TPM" in title:
        return "tpm_package"
    return "online_standard"


def price_type_from_header(header: str) -> str:
    compact = clean_text(header).replace(" ", "")
    # The condition column is headed "条件 输入长度：千 token", so it mentions 输入
    # without being an input price. Length bands are conditions, never prices.
    if describes_request_length(header):
        return "other"
    if "缓存存储" in compact:
        return "cache_storage"
    if "缓存命中" in compact and "音频" in compact and "非音频" not in compact:
        return "audio_cache_hit"
    if "缓存命中" in compact:
        return "cache_hit"
    if "输入" in compact and "音频" in compact and "非音频" not in compact:
        return "audio_input"
    if "输入" in compact:
        return "input"
    if "输出" in compact:
        return "output"
    return "other"


def amount_from_cell(value: str) -> str | None:
    if clean_text(value) in ("", "-"):
        return None
    values = numeric_values(value)
    return values[-1] if values else None


class VolcengineAdapter(TabularTokenPricingAdapter):
    provider_id = "volcengine"
    provider_name = "火山引擎方舟"
    source_url = VOLCENGINE_PAGE_URL
    source_kind = "official_markdown"
    catalog_url = VOLCENGINE_PAGE_URL
    currency = "CNY"
    region = "中国区"
    delivery_mode = "platform_hosted"
    # Context tiers repeat a model across rows with the model cell left empty.
    carry_forward_model = True
    # The Ark document states the peak/off-peak window for its Flash model in a
    # note above the table, naming the model in the note itself.
    publishes_time_bands = True

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self.source_updated_at: str | None = None
        self._document: str | None = None

    def document_text(self) -> str:
        if self._document is None:
            payload = json.loads(self.client.get_text(VOLCENGINE_DOC_API))
            try:
                result = payload["Result"]
                document = result["MDContent"]
                updated_at = result.get("UpdatedTime")
            except (KeyError, TypeError) as exc:
                raise SourceError("unexpected Volcengine document response") from exc
            if not document.strip():
                raise SourceError("Volcengine document published no Markdown")
            self.source_updated_at = updated_at
            self._document = document
        return self._document

    def price_kind(self, header: str) -> str | None:
        kind = price_type_from_header(header)
        return None if kind == "other" else kind

    def cell_amount(self, cell: str, header: str) -> str | None:
        return amount_from_cell(cell)

    def offer_name(self, headings: list[str]) -> str:
        return volc_offer_name(headings)

    def model_conditions(self, display_name: str) -> dict[str, Any]:
        return {"release_stage": "preview" if "预览版" in display_name else "stable"}

    def record_extras(self) -> dict[str, Any]:
        return {
            "source_api": VOLCENGINE_DOC_API,
            "source_updated_at": self.source_updated_at,
        }
