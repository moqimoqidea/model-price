"""Moonshot Kimi pricing, read from the Markdown documents its index lists."""

from __future__ import annotations

import re
from typing import Any

from ..core import PriceSource, now_iso
from ..models import model_family, normalize_model
from ..parsing import markdown_json_rows
from ..pricing import make_record, price_item

KIMI_INDEX_URL = "https://platform.kimi.com/docs/llms.txt"

# The index publishes the chat-model pricing as "chat.md" and has also served
# dated variants such as "chat-k3.md", so match the whole family rather than
# pinning a version. The sibling documents (batch, tools, limits) are not
# per-model token tables and must stay out of the catalogue.
KIMI_PRICING_DOC_RE = re.compile(
    r"https://platform\.kimi\.com/docs/pricing/chat[^)\s]*\.md"
)

# A model row is ``[model, unit, cache hit, cache miss, output, context]``.
KIMI_ROW_WIDTH = 5


class KimiAdapter(PriceSource):
    provider_id = "kimi"
    provider_name = "月之暗面 Kimi"
    source_url = KIMI_INDEX_URL
    source_kind = "official_markdown"

    def _documents(self) -> list[tuple[str, str]]:
        index = self.client.get_text(KIMI_INDEX_URL)
        urls = KIMI_PRICING_DOC_RE.findall(index)
        return [(url, self.client.get_text(url)) for url in dict.fromkeys(urls)]

    def _model_rows(self) -> list[tuple[str, list[str]]]:
        """Return ``(document url, row)`` for rows shaped like a model price."""
        return [
            (url, row)
            for url, document in self._documents()
            for row in markdown_json_rows(document)
            if len(row) >= KIMI_ROW_WIDTH
        ]

    def list_models(self, prefix: str = "") -> list[str]:
        models = {row[0] for _, row in self._model_rows()}
        if prefix:
            normalized = normalize_model(prefix)
            models = {m for m in models if normalize_model(m).startswith(normalized)}
        return sorted(models, key=str.lower)

    def query(self, model: str) -> list[dict[str, Any]]:
        for url, row in self._model_rows():
            if normalize_model(row[0]) != normalize_model(model):
                continue
            amounts = [re.sub(r"^[¥￥]", "", value) for value in row[2:5]]
            prices = [
                price_item(
                    "cache_hit",
                    "输入（缓存命中）",
                    amounts[0],
                    "CNY_per_million_tokens",
                ),
                price_item(
                    "input",
                    "输入（缓存未命中）",
                    amounts[1],
                    "CNY_per_million_tokens",
                ),
                price_item("output", "输出", amounts[2], "CNY_per_million_tokens"),
            ]
            return [
                make_record(
                    self.provider_id,
                    self.provider_name,
                    row[0],
                    row[0],
                    "中国区",
                    [
                        {
                            "name": "online_standard",
                            "conditions": {},
                            "prices": prices,
                        }
                    ],
                    url,
                    self.source_kind,
                    now_iso(),
                    delivery_mode="first_party",
                    model_family=model_family(row[0]),
                )
            ]
        return []
