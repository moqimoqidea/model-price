"""MiniMax first-party pay-as-you-go pricing, read from its Markdown document.

One document prices every product line: language models per million tokens,
speech per ten-thousand characters, video per second, images per picture, music
per song, and the vision model per call. The shared table reader takes them all,
so this adapter declares the two things that are MiniMax's own: which column names
a model, and that a service tier is stated by the tab a table sits in rather than
by a column.
"""

from __future__ import annotations

import re
from typing import Any

from ..parsing import clean_text
from .base import TabularPricingAdapter

MINIMAX_URL = "https://platform.minimax.cn/docs/guides/pricing-paygo.md"

# The video tables head their model column "模型/接口"; every other table names it
# "模型". Both are the column a model id is published under.
MODEL_HEADERS = {"模型", "模型/接口"}
PRIORITY_TIER = "priority"
# Model cells list the IDs a price covers, separated by a slash or stacked on
# their own line ("image-01<br />image-01-live").
MODEL_LIST_RE = re.compile(r"[/\n]")
# A tab or an accordion is what states a service tier or a legacy section. Both
# become headings so that the shared reader files their tables under them.
CONTAINER_RE = re.compile(r'<Tab(?:s)?\s+title="([^"]+)"\s*>|<Accordion\s+title="([^"]+)"\s*>')


class MiniMaxAdapter(TabularPricingAdapter):
    provider_id = "minimax"
    provider_name = "MiniMax 原厂"
    source_url = MINIMAX_URL
    source_kind = "official_markdown"
    currency = "CNY"
    region = "中国区"
    delivery_mode = "first_party"

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._document: str | None = None

    def document_text(self) -> str:
        """Return the document with its containers spelled as headings."""
        if self._document is None:
            self._document = CONTAINER_RE.sub(
                lambda match: f"\n### {match.group(1) or match.group(2)}\n",
                self.document(MINIMAX_URL),
            )
        return self._document

    def model_column(self, headers: list[str]) -> int | None:
        return next(
            (
                index
                for index, header in enumerate(headers)
                if clean_text(header) in MODEL_HEADERS
            ),
            None,
        )

    def heading_conditions(self, headings: list[str]) -> dict[str, Any]:
        conditions: dict[str, Any] = {
            "service_tier": (
                PRIORITY_TIER if self._priority(headings) else "standard"
            )
        }
        if "历史模型" in headings:
            conditions["status"] = "historical"
        return conditions

    @staticmethod
    def _priority(headings: list[str]) -> bool:
        return any("优先" in heading for heading in headings)

    def offer_name(self, headings: list[str]) -> str:
        """Name an offer after the tier, or after the section that prices it."""
        if self._priority(headings):
            return PRIORITY_TIER
        if any("标准" in heading for heading in headings):
            return "standard"
        return super().offer_name(headings)

    def model_variants(self, display_name: str, note: str = "") -> list[str]:
        """Read every ID a cell lists, including the ones stacked under the first.

        A cell states either one ID per line or several separated by a slash. The
        words a cell adds after its first ID are prose unless they are a single
        word, which is all a second ID ever is.
        """
        listed = MODEL_LIST_RE.split(display_name)
        if note and " " not in note:
            listed.extend(MODEL_LIST_RE.split(note))
        return [part.strip() for part in listed if part.strip()]

    def model_note_condition(self) -> str:
        """MiniMax states the context window a rate applies to under the name."""
        return "context_tier"
