"""Moonshot Kimi pricing, read from the Markdown documents its index lists."""

from __future__ import annotations

import re
from typing import Any

from ..core import HttpClient, PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, normalize_model, split_trailing_parenthetical
from ..parsing import (
    markdown_doc_tables,
    markdown_json_rows,
    monetary_amount,
    price_kind,
)
from ..pricing import make_record, price_item, unit_code
from ..text import clean_text

KIMI_INDEX_URL = "https://platform.kimi.com/docs/llms.txt"

# The index publishes the standard rate as "chat.md" and the discounted batch
# service as "batch.md", and has also served dated variants such as "chat-k3.md",
# so the whole family is matched rather than a pinned version. The other sibling
# documents (search, agents, limits) price tools and accounts rather than models.
KIMI_PRICING_DOC_RE = re.compile(
    r"https://platform\.kimi\.com/docs/pricing/(?:chat|batch)[^)\s]*\.md"
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
        index = self.document(KIMI_INDEX_URL)
        urls = KIMI_PRICING_DOC_RE.findall(index)
        return [(url, self.document(url)) for url in dict.fromkeys(urls)]

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
                    if (kind := price_kind(header)) is not None
                }
                if model_index is None or not price_columns:
                    continue
                for cells in table[1:]:
                    cells = [*cells, *("" for _ in range(len(headers) - len(cells)))]
                    # The batch document marks its rows in the name itself
                    # ("kimi-k2.7-code（Batch）"). That is a service level rather
                    # than part of the model's id: the same model sold two ways is
                    # one model with two offers, not two models.
                    display_name, tier_note = split_trailing_parenthetical(
                        clean_text(cells[model_index])
                    )
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
                    parsed.append(
                        {
                            "url": url,
                            "model_id": display_name,
                            "display_name": display_name,
                            "service_tier": normalize_model(tier_note),
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

    @staticmethod
    def _offers(row: dict[str, Any]) -> list[dict[str, Any]]:
        if not row["prices"]:
            return []
        tier = row.get("service_tier") or ""
        return [
            {
                "name": tier or "online_standard",
                "conditions": {"service_tier": tier} if tier else {},
                "prices": row["prices"],
            }
        ]

    def _record_for(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        first = rows[0]
        return make_record(
            self.provider_id,
            self.provider_name,
            first["model_id"],
            first["display_name"],
            "中国区",
            [offer for row in rows for offer in self._offers(row)],
            first["url"],
            self.source_kind,
            now_iso(),
            delivery_mode="first_party",
            model_family=model_family(first["model_id"]),
        )

    def _rows_by_model(self) -> dict[str, list[dict[str, Any]]]:
        rows: dict[str, list[dict[str, Any]]] = {}
        for row in self._model_rows():
            rows.setdefault(normalize_model(row["model_id"]), []).append(row)
        return rows

    def query(self, model: str) -> list[dict[str, Any]]:
        rows = self._rows_by_model().get(normalize_model(model))
        return [self._record_for(rows)] if rows else []

    def catalog_records(self) -> list[dict[str, Any]]:
        rows = self._rows_by_model()
        return [self._record_for(rows[key]) for key in sorted(rows)]
