"""Zhipu BigModel pricing, read from the official Markdown pricing page."""

from __future__ import annotations

from .base import TabularTokenPricingAdapter

ZHIPU_URL = "https://docs.bigmodel.cn/cn/guide/start/pricing"
ZHIPU_MARKDOWN_URL = f"{ZHIPU_URL}.md"

# The page publishes several table shapes. Only the columns that name a
# per-million-token rate are read: per-request image/video rates (``单价``) and
# per-character speech rates are billed on a different unit and are skipped
# because no column here classifies them.
ZHIPU_PRICE_KINDS = (
    ("输入单价", "input"),
    ("输出单价", "output"),
    ("缓存命中", "cache_hit"),
    ("缓存存储", "cache_storage"),
)


class ZhipuAdapter(TabularTokenPricingAdapter):
    provider_id = "zhipu"
    provider_name = "智谱 BigModel"
    source_url = ZHIPU_URL
    source_kind = "official_markdown"
    currency = "CNY"
    region = "中国区"

    def document_text(self) -> str:
        return self.client.get_text(ZHIPU_MARKDOWN_URL)

    def price_kind(self, header: str) -> str | None:
        compact = header.replace(" ", "")
        return next(
            (kind for label, kind in ZHIPU_PRICE_KINDS if label in compact), None
        )

    def offer_name(self, headings: list[str]) -> str:
        # The section already travels in ``source_section``, so the offer keeps a
        # stable name instead of one derived from the Chinese section title.
        return self.default_offer_name
