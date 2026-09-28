"""Kling's image and video price documents, read as one channel's catalogue.

Kling publishes two price pages — image generation and editing on one, video and
the capabilities around it on the other — and bills both in its own credits,
stating the yuan a credit is worth beside every credit rate. The credit is what an
API call is deducted in, so it is the unit a price is kept in: the yuan beside it
is the same rate restated, and reading both would print one charge twice.

Each page's model column also carries the platform's own categories (通用, 数字人,
对口型, 音频生成, 图像识别), which the vendor prices exactly as it prices a model.
They stay in the catalogue named as the vendor named them, with the capability a
row charges for kept as the offer it is bought as.
"""

from __future__ import annotations

from typing import Any

from .base import TabularPricingAdapter

KLING_IMAGE_URL = "https://klingai.com/document-api/pricing/base/image.md"
KLING_VIDEO_URL = "https://klingai.com/document-api/pricing/base/video.md"

# What a row states before it states a price: the function it is bought as. The
# function names the offer, and the billing mode beside it (按秒收费) stays a
# condition of that offer.
KLING_FUNCTION_COLUMN = "功能"


class KlingAdapter(TabularPricingAdapter):
    provider_id = "kling"
    provider_name = "快手可灵"
    source_url = KLING_IMAGE_URL
    source_kind = "official_markdown"
    currency = "CNY"
    region = "中国区"

    def source_documents(self) -> tuple[tuple[str, str], ...]:
        """Read both price pages, keeping each row's own page with it."""
        return tuple(
            (url, self.document(url)) for url in (KLING_IMAGE_URL, KLING_VIDEO_URL)
        )

    def offer_name(self, headings: list[str]) -> str:
        # The section a table sits under is the product line, which already travels
        # as ``source_section``; an offer is named by the function it buys.
        return self.default_offer_name

    def row_offer_name(self, headings: list[str], conditions: dict[str, Any]) -> str:
        return str(conditions.get(KLING_FUNCTION_COLUMN) or super().offer_name(headings))

    def row_conditions(self, conditions: dict[str, Any]) -> dict[str, Any]:
        # The function is the offer's name now, and printing it as a condition as
        # well would state one fact twice.
        return {
            key: value
            for key, value in conditions.items()
            if key != KLING_FUNCTION_COLUMN
        }
