"""Anthropic pricing, read from the official Markdown documentation."""

from __future__ import annotations

import re
from typing import Any

from ..core import PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, normalize_model, without_trailing_parenthetical
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

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._parsed_rows: list[dict[str, Any]] | None = None

    def _rows(self) -> list[dict[str, Any]]:
        if self._parsed_rows is not None:
            return self._parsed_rows
        text = self.document(ANTHROPIC_MARKDOWN_URL)
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
            model_id = normalize_model(without_trailing_parenthetical(display_name))
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
                    model_id = normalize_model(without_trailing_parenthetical(name))
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
        self._parsed_rows = rows
        return self._parsed_rows

    def _rows_by_model(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in self._rows():
            grouped.setdefault(normalize_model(row["model_id"]), []).append(row)
        return grouped

    def _record_for(self, matched: list[dict[str, Any]]) -> dict[str, Any]:
        first = matched[0]
        return make_record(
            self.provider_id,
            self.provider_name,
            first["model_id"],
            first["display_name"],
            "全球",
            [row["offer"] for row in matched],
            self.source_url,
            self.source_kind,
            now_iso(),
            currency="USD",
            delivery_mode="first_party",
            model_family=model_family(first["model_id"]),
            source_api=ANTHROPIC_MARKDOWN_URL,
        )

    def list_models(self, prefix: str = "") -> list[str]:
        models = {row["model_id"] for row in self._rows()}
        if prefix:
            key = normalize_model(prefix)
            models = {
                model for model in models if normalize_model(model).startswith(key)
            }
        return sorted(models)

    def query(self, model: str) -> list[dict[str, Any]]:
        matched = self._rows_by_model().get(normalize_model(model))
        return [self._record_for(matched)] if matched else []

    def catalog_records(self) -> list[dict[str, Any]]:
        """Build the whole catalogue from one parsed Markdown document."""
        grouped = self._rows_by_model()
        return [self._record_for(grouped[key]) for key in sorted(grouped)]
