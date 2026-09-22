"""Xiaomi MiMo first-party pay-as-you-go pricing.

The documentation publishes each model twice: once in CNY for mainland China
and once in USD for overseas. This adapter reads the CNY tables, because
``monetary_amount`` only accepts amounts that name the adapter's currency, which
leaves the overseas rows without a parseable price.
"""

from __future__ import annotations

from typing import Any

from ..parsing import document_update_stamp
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
        # The table's first column is the product line ("MiMo-V2.5 系列") rather
        # than a generic "Model" header, so it names the model unless it is itself
        # a price column.
        if headers and self.price_kind(headers[0]) is None:
            return 0
        return None
