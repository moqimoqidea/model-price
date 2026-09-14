"""Google Gemini pricing, read from the rendered HTML pricing page."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from ..core import PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, normalize_model
from ..pricing import make_record, price_item, usd_price
from ..text import clean_text

GEMINI_URL = "https://ai.google.dev/gemini-api/docs/pricing"

NON_MODEL_SECTION_IDS = {"notes", "pricing-for-tools", "pricing-for-agents"}

PRICE_TYPE_BY_LABEL = {
    "input price": "input",
    "output price": "output",
    "context caching price": "cache_hit",
}

CACHE_STORAGE_RE = re.compile(
    r"\$(\d+(?:\.\d+)?)\s*/\s*1,000,000 tokens per hour"
)


def is_model_section_id(heading_id: str) -> bool:
    """A pricing section belongs to a model unless it is an overview section.

    Matching every model slug (instead of a hard-coded family prefix) keeps new
    families such as ``gemma-4`` or ``veo-3.1`` discoverable.
    """
    if not heading_id:
        return False
    if heading_id in NON_MODEL_SECTION_IDS:
        return False
    return not heading_id.startswith("pricing-for")


class GeminiPricingParser(HTMLParser):
    """Collect ``pricing-table`` blocks per ``h2`` model section and ``h3`` tier."""

    def __init__(self) -> None:
        super().__init__()
        self.model_id = ""
        self.display_name = ""
        self.tier = ""
        self.tables: list[dict[str, Any]] = []
        self.capture: str | None = None
        self.capture_text: list[str] = []
        self.in_table = False
        self.in_cell = False
        self.cell_text: list[str] = []
        self.row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in {"h2", "h3"}:
            self.capture = tag
            self.capture_text = []
            if tag == "h2":
                heading_id = str(attributes.get("id", ""))
                self.model_id = heading_id if is_model_section_id(heading_id) else ""
                self.display_name = ""
        if tag == "table" and "pricing-table" in str(attributes.get("class", "")):
            self.in_table = True
            self.rows = []
        elif self.in_table and tag == "tr":
            self.row = []
        elif self.in_table and tag in {"td", "th"}:
            self.in_cell = True
            self.cell_text = []
        elif self.in_cell and tag == "br":
            self.cell_text.append(" / ")

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.capture_text.append(data)
        if self.in_cell:
            self.cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.capture == tag:
            value = clean_text(" ".join(self.capture_text))
            if tag == "h2" and self.model_id:
                self.display_name = value
            elif tag == "h3":
                self.tier = value.lower()
            self.capture = None
        if self.in_table and tag in {"td", "th"}:
            self.row.append(clean_text("".join(self.cell_text)))
            self.in_cell = False
        elif self.in_table and tag == "tr" and self.row:
            self.rows.append(self.row)
        elif self.in_table and tag == "table":
            if self.model_id and self.rows:
                self.tables.append(
                    {
                        "model_id": self.model_id,
                        "display_name": self.display_name or self.model_id,
                        "tier": self.tier or "standard",
                        "rows": self.rows,
                    }
                )
            self.in_table = False


class GeminiAdapter(PriceSource):
    provider_id = "google"
    provider_name = "Google Gemini"
    source_url = GEMINI_URL
    source_kind = "official_html"

    def _tables(self) -> list[dict[str, Any]]:
        parser = GeminiPricingParser()
        parser.feed(self.client.get_text(GEMINI_URL))
        if not parser.tables:
            raise SourceError("official pricing tables were not found")
        return parser.tables

    def list_models(self, prefix: str = "") -> list[str]:
        models = {table["model_id"] for table in self._tables()}
        if prefix:
            key = normalize_model(prefix)
            models = {
                model for model in models if normalize_model(model).startswith(key)
            }
        return sorted(models)

    def query(self, model: str) -> list[dict[str, Any]]:
        tables = [
            table
            for table in self._tables()
            if normalize_model(table["model_id"]) == normalize_model(model)
        ]
        if not tables:
            return []
        offers = []
        for table in tables:
            headers = [cell.lower() for cell in table["rows"][0]]
            paid_index = next(
                (index for index, cell in enumerate(headers) if "paid tier" in cell),
                None,
            )
            if paid_index is None:
                continue
            prices = []
            for row in table["rows"][1:]:
                if len(row) <= paid_index:
                    continue
                label = row[0]
                kind = next(
                    (
                        value
                        for key, value in PRICE_TYPE_BY_LABEL.items()
                        if key in label.lower()
                    ),
                    None,
                )
                if not kind:
                    continue
                item = usd_price(kind, label, row[paid_index])
                if item:
                    prices.append(item)
                if kind == "cache_hit":
                    storage = CACHE_STORAGE_RE.search(row[paid_index])
                    if storage:
                        prices.append(
                            price_item(
                                "cache_storage",
                                "Context cache storage",
                                storage.group(1),
                                "USD_per_million_tokens_per_hour",
                                display=row[paid_index],
                            )
                        )
            if prices:
                offers.append(
                    {
                        "name": table["tier"],
                        "conditions": {
                            "service_tier": table["tier"],
                            "billing_tier": "paid",
                        },
                        "prices": prices,
                    }
                )
        return [
            make_record(
                self.provider_id,
                self.provider_name,
                tables[0]["model_id"],
                tables[0]["display_name"],
                "全球",
                offers,
                self.source_url,
                self.source_kind,
                now_iso(),
                currency="USD",
                delivery_mode="first_party",
                model_family=model_family(tables[0]["model_id"]),
            )
        ]
