"""Anthropic pricing, read from the official Markdown documentation."""

from __future__ import annotations

import re
from typing import Any

from ..core import PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, normalize_model
from ..parsing import markdown_link_text, markdown_tables
from ..pricing import make_record, usd_price

ANTHROPIC_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
ANTHROPIC_MARKDOWN_URL = f"{ANTHROPIC_URL}.md"

STANDARD_KINDS = [
    "input",
    "cache_write_5m",
    "cache_write_1h",
    "cache_hit",
    "output",
]
STANDARD_LABELS = [
    "Base input",
    "5m cache write",
    "1h cache write",
    "Cache hit",
    "Output",
]
TIER_HEADINGS = {
    "Batch processing": "batch",
    "Fast mode pricing": "fast",
}


class AnthropicAdapter(PriceSource):
    provider_id = "anthropic"
    provider_name = "Anthropic"
    source_url = ANTHROPIC_URL
    source_kind = "official_markdown"

    def _rows(self) -> list[dict[str, Any]]:
        text = self.client.get_text(ANTHROPIC_MARKDOWN_URL)
        tables = markdown_tables(text)
        table = next(
            (rows for headings, rows in tables if headings[-1:] == ["Model pricing"]),
            [],
        )
        if len(table) < 2:
            raise SourceError("official model pricing table was not found")
        rows = []
        for cells in table[1:]:
            cells += [""] * (6 - len(cells))
            display_name = markdown_link_text(cells[0])
            model_id = normalize_model(re.sub(r"\s*\([^)]*\)\s*$", "", display_name))
            prices = [
                item
                for item in (
                    usd_price(kind, label, value)
                    for kind, label, value in zip(
                        STANDARD_KINDS, STANDARD_LABELS, cells[1:6]
                    )
                )
                if item
            ]
            # Rows are identified by pricing content, never by a name prefix, so a
            # renamed or newly branded model family is picked up without a code fix.
            if not model_id or not prices:
                continue
            conditions: dict[str, Any] = {"service_tier": "standard"}
            status = re.search(
                r"\(([^)]*(?:retired|limited availability)[^)]*)\)", display_name, re.I
            )
            if status:
                conditions["status"] = status.group(1)
            rows.append(
                {
                    "model_id": model_id,
                    "display_name": display_name,
                    "offer": {
                        "name": "standard",
                        "conditions": conditions,
                        "prices": prices,
                    },
                }
            )
        for headings, price_table in tables:
            service_tier = TIER_HEADINGS.get(headings[-1] if headings else "")
            if not service_tier:
                continue
            for cells in price_table[1:]:
                cells += [""] * (3 - len(cells))
                for name in markdown_link_text(cells[0]).split(" / "):
                    model_id = normalize_model(
                        re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
                    )
                    prices = [
                        item
                        for item in (
                            usd_price("input", "Input", cells[1]),
                            usd_price("output", "Output", cells[2]),
                        )
                        if item
                    ]
                    if not model_id or not prices:
                        continue
                    rows.append(
                        {
                            "model_id": model_id,
                            "display_name": name,
                            "offer": {
                                "name": service_tier,
                                "conditions": {"service_tier": service_tier},
                                "prices": prices,
                            },
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
        return sorted(models)

    def query(self, model: str) -> list[dict[str, Any]]:
        matched = [
            row
            for row in self._rows()
            if normalize_model(row["model_id"]) == normalize_model(model)
        ]
        if not matched:
            return []
        return [
            make_record(
                self.provider_id,
                self.provider_name,
                matched[0]["model_id"],
                matched[0]["display_name"],
                "全球",
                [row["offer"] for row in matched],
                self.source_url,
                self.source_kind,
                now_iso(),
                currency="USD",
                delivery_mode="first_party",
                model_family=model_family(matched[0]["model_id"]),
                source_api=ANTHROPIC_MARKDOWN_URL,
            )
        ]
