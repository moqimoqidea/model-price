"""OpenAI pricing, read from the official Markdown API reference."""

from __future__ import annotations

import re
from typing import Any

from ..core import PriceSource, now_iso
from ..models import model_family, normalize_model, without_trailing_parenthetical
from ..parsing import markdown_link_text, markdown_tables, promotion_notes
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

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._parsed_rows: list[dict[str, Any]] | None = None
        self._note_map: dict[str, list[str]] | None = None

    def _rows(self) -> list[dict[str, Any]]:
        if self._parsed_rows is not None:
            return self._parsed_rows
        text = self.document(OPENAI_MARKDOWN_URL)
        rows: list[dict[str, Any]] = []
        for headings, table in markdown_tables(text):
            # Any "<Tier> pricing data" heading is a service tier, so a new tier
            # name does not silently drop that tier's prices.
            tier_match = re.fullmatch(
                r"([A-Za-z][A-Za-z-]*) pricing data", headings[-1] if headings else ""
            )
            if not tier_match or len(table) < 2:
                continue
            headers = [clean_text(cell).lower() for cell in table[0]]
            for cells in table[1:]:
                cells += [""] * (len(headers) - len(cells))
                display_name = markdown_link_text(cells[0])
                model_id = without_trailing_parenthetical(display_name)
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
        self._parsed_rows = rows
        return self._parsed_rows

    def _notes(self) -> dict[str, list[str]]:
        """What the document says about each model's price, read once.

        OpenAI announces its promotions in the prose beside the tables rather than in
        a column, and names the model it means ("GPT-5.6 Sol's promotional pricing is
        available at least through November 21, 2026"). The whole catalogue is offered
        at once so that the sentence reads as naming gpt-5.6-sol and not the shorter
        ids it begins with, and the sentence is kept as written: at least through a
        date is not a date, and the price it explains is still read from the table.
        """
        if self._note_map is None:
            self._note_map = promotion_notes(
                self.document(OPENAI_MARKDOWN_URL), names=self.list_models()
            )
        return self._note_map

    def _rows_by_model(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in self._rows():
            grouped.setdefault(normalize_model(row["model_id"]), []).append(row)
        return grouped

    def _record_for(self, matched: list[dict[str, Any]]) -> dict[str, Any]:
        first = matched[0]
        # A model is priced in several tiers and the document states what it says
        # once, so the same sentence must not arrive once per tier.
        notes = list(
            dict.fromkeys(
                note
                for row in matched
                for note in self._notes().get(row["model_id"], [])
            )
        )
        return make_record(
            self.provider_id,
            self.provider_name,
            first["model_id"],
            first["display_name"],
            "全球",
            [offer for row in matched for offer in row["offers"]],
            self.source_url,
            self.source_kind,
            now_iso(),
            currency="USD",
            delivery_mode="first_party",
            model_family=model_family(first["model_id"]),
            source_api=OPENAI_MARKDOWN_URL,
            # A model the document explains no promotion for carries no note at all,
            # rather than an empty one: the report prints a note where there is one.
            **({"pricing_notes": notes} if notes else {}),
        )

    def list_models(self, prefix: str = "") -> list[str]:
        models = {row["model_id"] for row in self._rows()}
        if prefix:
            key = normalize_model(prefix)
            models = {
                model for model in models if normalize_model(model).startswith(key)
            }
        return sorted(models, key=str.lower)

    def query(self, model: str) -> list[dict[str, Any]]:
        matched = self._rows_by_model().get(normalize_model(model))
        return [self._record_for(matched)] if matched else []

    def catalog_records(self) -> list[dict[str, Any]]:
        """Build the whole catalogue from one parsed Markdown document."""
        grouped = self._rows_by_model()
        return [self._record_for(grouped[key]) for key in sorted(grouped)]
