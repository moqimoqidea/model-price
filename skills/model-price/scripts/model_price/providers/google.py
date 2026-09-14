"""Google Gemini pricing, read from the official Markdown pricing page.

The page publishes a Markdown copy at ``pricing.md.txt`` whose tables keep one
header row each, so the shared Markdown reader is enough: no element or class
name has to be tracked in rendered HTML.
"""

from __future__ import annotations

import re
from typing import Any

from ..core import PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, model_matches, normalize_model
from ..parsing import markdown_link_text, markdown_tables
from ..pricing import make_record, price_item, usd_price
from ..text import clean_text

GEMINI_URL = "https://ai.google.dev/gemini-api/docs/pricing"
GEMINI_MARKDOWN_URL = f"{GEMINI_URL}.md.txt"

# Sections that describe tools, agents, or general notes rather than a model.
NON_MODEL_SECTIONS = {"notes", "pricing-for-tools", "pricing-for-agents"}

PRICE_TYPE_BY_LABEL = {
    "input price": "input",
    "output price": "output",
    "context caching price": "cache_hit",
}

# A cache row publishes the hit price and the hourly storage price in one cell,
# so the storage clause is matched whole and removed from the hit price.
CACHE_STORAGE_RE = re.compile(
    r"\$(\d+(?:\.\d+)?)\s*/\s*1,000,000 tokens per hour[^.]*(?:\.|$)"
)

# A model section lists its API ids on an italic link line, either alone
# (``*[`gemini-3.8-flash`](https://...)*``) or as a whole family
# (``*[`veo-3.1-generate-preview`](...), [`veo-3.1-fast-generate-preview`](...)*``).
MODEL_ID_LINE_RE = re.compile(r"\[`([^`]+)`\]\(")


class GeminiAdapter(PriceSource):
    provider_id = "google"
    provider_name = "Google Gemini"
    source_url = GEMINI_URL
    source_kind = "official_markdown"

    def _sections(self) -> list[dict[str, Any]]:
        """Split the page into its ``h2`` model sections with their tier tables."""
        text = self.client.get_text(GEMINI_MARKDOWN_URL)
        parts = re.split(r"^## +(.+)$", text, flags=re.M)
        sections = []
        for heading, body in zip(parts[1::2], parts[2::2]):
            display_name = markdown_link_text(heading)
            if normalize_model(display_name) in NON_MODEL_SECTIONS:
                continue
            sections.append(
                {
                    "display_name": display_name,
                    "model_ids": self._model_ids(body, display_name),
                    "tables": markdown_tables(body),
                }
            )
        if not sections:
            raise SourceError("official pricing sections were not found")
        return sections

    @staticmethod
    def _model_ids(body: str, display_name: str) -> list[str]:
        """Read the API ids the section publishes, or fall back to its heading."""
        for line in body.splitlines():
            found = MODEL_ID_LINE_RE.findall(line)
            if found:
                return found
        # Some sections head the model itself, e.g. ``## [Gemma 4](url)``.
        return [normalize_model(display_name)]

    @staticmethod
    def _offers(tables: list[tuple[list[str], list[list[str]]]]) -> list[dict[str, Any]]:
        offers = []
        for headings, rows in tables:
            if len(rows) < 2:
                continue
            tier = (headings[-1] if headings else "standard").lower()
            headers = [cell.lower() for cell in rows[0]]
            paid_index = next(
                (index for index, cell in enumerate(headers) if "paid tier" in cell),
                None,
            )
            if paid_index is None:
                continue
            prices = []
            for row in rows[1:]:
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
                cell = row[paid_index]
                storage = CACHE_STORAGE_RE.search(cell) if kind == "cache_hit" else None
                if storage:
                    cell = CACHE_STORAGE_RE.sub("", cell).strip()
                item = usd_price(kind, label, cell)
                if item:
                    prices.append(item)
                if storage:
                    prices.append(
                        price_item(
                            "cache_storage",
                            "Context cache storage",
                            storage.group(1),
                            "USD_per_million_tokens_per_hour",
                            display=clean_text(storage.group(0)),
                        )
                    )
            if prices:
                offers.append(
                    {
                        "name": normalize_model(tier) or "standard",
                        "conditions": {"service_tier": tier, "billing_tier": "paid"},
                        "prices": prices,
                    }
                )
        return offers

    def _records(self) -> list[dict[str, Any]]:
        retrieved_at = now_iso()
        return [
            make_record(
                self.provider_id,
                self.provider_name,
                section["model_ids"][0],
                section["display_name"],
                "全球",
                self._offers(section["tables"]),
                self.source_url,
                self.source_kind,
                retrieved_at,
                currency="USD",
                delivery_mode="first_party",
                model_family=model_family(section["model_ids"][0]),
                model_aliases=section["model_ids"],
                source_api=GEMINI_MARKDOWN_URL,
            )
            for section in self._sections()
        ]

    def list_models(self, prefix: str = "") -> list[str]:
        models = {
            model_id
            for section in self._sections()
            for model_id in section["model_ids"]
        }
        if prefix:
            key = normalize_model(prefix)
            models = {
                model for model in models if normalize_model(model).startswith(key)
            }
        return sorted(models, key=str.lower)

    def query(self, model: str) -> list[dict[str, Any]]:
        key = normalize_model(model)
        return [
            record
            for record in self._records()
            if any(normalize_model(alias) == key for alias in record["model_aliases"])
        ]

    def search(self, model: str, *, exact: bool = False) -> list[dict[str, Any]]:
        """Match a section once, even when several API ids resolve to it."""
        return [
            record
            for record in self._records()
            if any(
                model_matches(model, alias, exact=exact)
                for alias in record["model_aliases"]
            )
        ]
