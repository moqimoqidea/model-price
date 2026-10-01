"""Keep Ant Ling's first-party CNY prices separate from its partner price tables.

The public page renders complete HTML tables. Its struck amounts are list prices,
while promotion periods are stated only in prose and must not acquire end dates.
"""

from __future__ import annotations

from typing import Any

from ..core import HttpClient
from ..parsing import document_update_stamp, promotion_notes
from .base import TabularPricingAdapter

ANT_LING_PRICE_URL = "https://developer.ant-ling.com/zh-CN/docs/models/price/"
ANT_LING_DEPRECATION_URL = "https://developer.ant-ling.com/zh-CN/docs/models/deprecation/"


class AntLingAdapter(TabularPricingAdapter):
    provider_id = "ant-ling"
    provider_name = "蚂蚁大模型"
    source_url = ANT_LING_PRICE_URL
    source_kind = "official_html"
    currency = "CNY"
    region = "中国区"

    def __init__(self, client: HttpClient) -> None:
        super().__init__(client)
        self._note_map: dict[str, list[str]] | None = None

    def includes_table(self, headings: list[str]) -> bool:
        # Partner quotes never become this provider's prices, even if a future
        # partner table also uses CNY or introduces a different model.
        return "第三方平台" not in headings

    def offer_name(self, headings: list[str]) -> str:
        return self.default_offer_name

    def record_extras(self) -> dict[str, Any]:
        return {"source_updated_at": document_update_stamp(self.document_text())}

    def model_extras(self, model_id: str, display_name: str) -> dict[str, Any]:
        if self._note_map is None:
            self._note_map = promotion_notes(
                self.document_text(), names=self.list_models()
            )
        notes = self._note_map.get(model_id, [])
        return {"pricing_notes": notes} if notes else {}
