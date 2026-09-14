"""OpenAI pricing, read from the official Markdown API reference."""

from __future__ import annotations

import re
from typing import Any

from ..core import PriceSource, now_iso
from ..models import model_family, normalize_model
from ..parsing import markdown_link_text, markdown_tables
from ..pricing import make_record, usd_price
from ..text import clean_text

OPENAI_URL = "https://developers.openai.com/api/docs/pricing"
OPENAI_MARKDOWN_URL = f"{OPENAI_URL}.md"

OUTPUT_MAPPINGS = {
    "input": "input",
    "cached input": "cache_hit",
    "cache writes": "cache_write",
    "output": "output",
}


class OpenAIAdapter(PriceSource):
    provider_id = "openai"
    provider_name = "OpenAI"
    source_url = OPENAI_URL
    source_kind = "official_markdown"

    def _rows(self) -> list[dict[str, Any]]:
        text = self.client.get_text(OPENAI_MARKDOWN_URL)
        rows: list[dict[str, Any]] = []
        for heading, table in markdown_tables(text):
            # Any "<Tier> pricing data" heading is a service tier, so a new tier
            # name does not silently drop that tier's prices.
            tier_match = re.fullmatch(r"([A-Za-z][A-Za-z-]*) pricing data", heading)
            if not tier_match or len(table) < 2:
                continue
            headers = [clean_text(cell).lower() for cell in table[0]]
            for cells in table[1:]:
                cells += [""] * (len(headers) - len(cells))
                display_name = markdown_link_text(cells[0])
                model_id = re.sub(r"\s*\([^)]*\)\s*$", "", display_name).strip()
                if not model_id:
                    continue
                offers = []
                for context in ("short", "long"):
                    prices = []
                    for suffix, kind in OUTPUT_MAPPINGS.items():
                        label = f"{context} context {suffix}"
                        if label in headers:
                            item = usd_price(kind, label, cells[headers.index(label)])
                            if item:
                                prices.append(item)
                    if prices:
                        offers.append(
                            {
                                "name": tier_match.group(1).lower(),
                                "conditions": {
                                    "service_tier": tier_match.group(1).lower(),
                                    "context_tier": context,
                                },
                                "prices": prices,
                            }
                        )
                rows.append(
                    {
                        "model_id": model_id,
                        "display_name": display_name,
                        "offers": offers,
                    }
                )
        return rows

    def list_models(self, prefix: str = "") -> list[str]:
        models = {row["model_id"] for row in self._rows()}
        if prefix:
            key = normalize_model(prefix)
            models = {
                model for model in models if normalize_model(model).startswith(key)
            }
        return sorted(models, key=str.lower)

    def query(self, model: str) -> list[dict[str, Any]]:
        matched = [
            row
            for row in self._rows()
            if normalize_model(row["model_id"]) == normalize_model(model)
        ]
        if not matched:
            return []
        offers = [offer for row in matched for offer in row["offers"]]
        return [
            make_record(
                self.provider_id,
                self.provider_name,
                matched[0]["model_id"],
                matched[0]["display_name"],
                "全球",
                offers,
                self.source_url,
                self.source_kind,
                now_iso(),
                currency="USD",
                delivery_mode="first_party",
                model_family=model_family(matched[0]["model_id"]),
                source_api=OPENAI_MARKDOWN_URL,
            )
        ]
