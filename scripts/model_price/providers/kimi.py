"""Moonshot Kimi pricing, read from the Markdown documents its index lists."""

from __future__ import annotations

import re
from typing import Any

from ..core import HttpClient, PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, normalize_model
from ..parsing import (
    markdown_doc_tables,
    markdown_json_rows,
    monetary_amount,
    token_price_kind,
)
from ..pricing import make_record, price_item, unit_code
from ..text import clean_text

KIMI_INDEX_URL = "https://platform.kimi.com/docs/llms.txt"

# The index publishes the chat-model pricing as "chat.md" and has also served
# dated variants such as "chat-k3.md", so match the whole family rather than
# pinning a version. The sibling documents (batch, tools, limits) are not
# per-model token tables and must stay out of the catalogue.
KIMI_PRICING_DOC_RE = re.compile(
    r"https://platform\.kimi\.com/docs/pricing/chat[^)\s]*\.md"
)

# Older copies of the official Markdown exposed the JSON rows without their JSX
# column declarations. Keep that shape readable, while current documents are
# always mapped by the headers they publish.
KIMI_LEGACY_HEADERS = (
    "模型",
    "计费单位",
    "输入价格（缓存命中）",
    "输入价格（缓存未命中）",
    "输出价格",
    "上下文窗口",
)
KIMI_MODEL_HEADERS = {"model", "model name", "模型", "模型名称"}
KIMI_UNIT_HEADERS = {"billing unit", "计费单位"}
KIMI_PRICE_LABELS = {
    "cache_hit": "输入（缓存命中）",
    "input": "输入（缓存未命中）",
    "output": "输出",
}


class KimiAdapter(PriceSource):
    provider_id = "kimi"
    provider_name = "月之暗面 Kimi"
    source_url = KIMI_INDEX_URL
    source_kind = "official_markdown"

    def __init__(self, client: HttpClient) -> None:
        super().__init__(client)
        self._parsed_rows: list[dict[str, Any]] | None = None

    def _documents(self) -> list[tuple[str, str]]:
        index = self.client.get_text(KIMI_INDEX_URL)
        urls = KIMI_PRICING_DOC_RE.findall(index)
        return [(url, self.client.get_text(url)) for url in dict.fromkeys(urls)]

    @staticmethod
    def _tables(document: str) -> list[list[list[str]]]:
        tables = markdown_doc_tables(document)
        if tables:
            return tables
        rows = markdown_json_rows(document)
        return [[list(KIMI_LEGACY_HEADERS), *rows]] if rows else []

    def _model_rows(self) -> list[dict[str, Any]]:
        """Read model prices by the meaning of each published column header."""
        if self._parsed_rows is not None:
            return self._parsed_rows
        parsed: list[dict[str, Any]] = []
        for url, document in self._documents():
            for table in self._tables(document):
                headers = [clean_text(header) for header in table[0]]
                model_index = next(
                    (
                        index
                        for index, header in enumerate(headers)
                        if header.lower() in KIMI_MODEL_HEADERS
                    ),
                    None,
                )
                unit_index = next(
                    (
                        index
                        for index, header in enumerate(headers)
                        if header.lower() in KIMI_UNIT_HEADERS
                    ),
                    None,
                )
                price_columns = {
                    index: kind
                    for index, header in enumerate(headers)
                    if (kind := token_price_kind(header)) is not None
                }
                if model_index is None or not price_columns:
                    continue
                for cells in table[1:]:
                    cells = [*cells, *("" for _ in range(len(headers) - len(cells)))]
                    display_name = clean_text(cells[model_index])
                    if not display_name:
                        continue
                    unit = (
                        unit_code(cells[unit_index])
                        if unit_index is not None
                        else "CNY_per_million_tokens"
                    )
                    prices = []
                    for index, kind in price_columns.items():
                        amount = monetary_amount(cells[index], headers[index], "CNY")
                        if amount is None:
                            continue
                        prices.append(
                            price_item(
                                kind,
                                KIMI_PRICE_LABELS.get(kind, headers[index]),
                                amount,
                                unit,
                            )
                        )
                    if prices:
                        parsed.append(
                            {
                                "url": url,
                                "model_id": display_name,
                                "display_name": display_name,
                                "prices": prices,
                            }
                        )
        if not parsed:
            raise SourceError("official Kimi token pricing table was not found")
        self._parsed_rows = parsed
        return parsed

    def list_models(self, prefix: str = "") -> list[str]:
        models = {row["model_id"] for row in self._model_rows()}
        if prefix:
            normalized = normalize_model(prefix)
            models = {m for m in models if normalize_model(m).startswith(normalized)}
        return sorted(models, key=str.lower)

    def _record_for(self, row: dict[str, Any]) -> dict[str, Any]:
        return make_record(
            self.provider_id,
            self.provider_name,
            row["model_id"],
            row["display_name"],
            "中国区",
            [
                {
                    "name": "online_standard",
                    "conditions": {},
                    "prices": row["prices"],
                }
            ],
            row["url"],
            self.source_kind,
            now_iso(),
            delivery_mode="first_party",
            model_family=model_family(row["model_id"]),
        )

    def _rows_by_model(self) -> dict[str, dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for row in self._model_rows():
            rows.setdefault(normalize_model(row["model_id"]), row)
        return rows

    def query(self, model: str) -> list[dict[str, Any]]:
        row = self._rows_by_model().get(normalize_model(model))
        return [self._record_for(row)] if row else []

    def catalog_records(self) -> list[dict[str, Any]]:
        rows = self._rows_by_model()
        return [self._record_for(rows[key]) for key in sorted(rows)]
