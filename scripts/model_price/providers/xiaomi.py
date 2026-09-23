"""Xiaomi MiMo first-party pay-as-you-go pricing.

The documentation publishes each model twice: once in CNY for mainland China
and once in USD for overseas. This adapter reads the CNY tables, because
``monetary_amount`` only accepts amounts that name the adapter's currency, which
leaves the overseas rows without a parseable price.
"""

from __future__ import annotations

import re
from typing import Any

from ..models import split_trailing_parenthetical
from ..parsing import document_update_stamp
from ..text import clean_text
from .base import TabularTokenPricingAdapter

XIAOMI_URL = "https://mimo.mi.com/docs/zh-CN/price/pay-as-you-go"


class XiaomiAdapter(TabularTokenPricingAdapter):
    provider_id = "xiaomi"
    provider_name = "小米 MiMo"
    source_url = XIAOMI_URL
    source_kind = "official_html"
    currency = "CNY"
    region = "中国区"

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._document: str | None = None
        self._source_updated_at: str | None = None

    def document_text(self) -> str:
        """Return the rendered page once, for both prices and its update date."""
        if self._document is None:
            self._document = self.document(self.source_url)
            self._source_updated_at = document_update_stamp(
                self._document, utc_offset="+08:00"
            )
        return self._document

    def record_extras(self) -> dict[str, Any]:
        # ``_rows`` calls ``document_text`` before records are built, so the date
        # has already been read from the same document as the prices.
        return {"source_updated_at": self._source_updated_at}

    def model_column(self, headers: list[str]) -> int | None:
        # Older tables name the model column after its product series. Newer
        # tables have an explicit model header after a billing-type column.
        explicit = super().model_column(headers)
        if explicit is not None:
            return explicit
        if headers and "系列" in headers[0] and self.price_kind(headers[0]) is None:
            return 0
        return None

    def row_offer_name(self, headings: list[str], conditions: dict[str, Any]) -> str:
        return "batch" if conditions.get("推理类型") == "批量推理" else super().offer_name(headings)

    def row_conditions(self, conditions: dict[str, Any]) -> dict[str, Any]:
        # The mode is represented by the offer name. Keeping it again as a
        # condition would make an unchanged real-time price look newly listed.
        return {key: value for key, value in conditions.items() if key != "推理类型"}

    def model_variants(self, display_name: str) -> list[str]:
        """Split shared prices; dated shutdown evidence comes from the notice log."""
        variants = []
        for label in re.split(r"[、，]", display_name):
            name, annotation = split_trailing_parenthetical(label)
            if annotation and "下线" in annotation:
                variants.append(name)
            else:
                variants.append(clean_text(label))
        return [variant for variant in variants if variant]
