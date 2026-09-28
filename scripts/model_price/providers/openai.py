"""OpenAI pricing, read from the official Markdown API reference.

The document prices more than text: images per picture, live sessions per minute,
transcription per minute of audio, and speech per character, each in a table of
its own. A table is filed under the section the page wrote above it rather than
under a heading — every section but the first four carries the same generic
heading — so the words the page published are what a table is read under, and the
service levels it names in its own headings are what tell two price lists for one
model apart.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from ..core import PriceSource, now_iso
from ..models import model_family, normalize_model, without_trailing_parenthetical
from ..parsing import (
    cell_rates,
    markdown_link_text,
    price_column_kinds,
    price_unit_code,
    promotion_notes,
    split_markdown_row,
)
from ..pricing import make_record, price_item, usd_price
from ..text import clean_text

OPENAI_URL = "https://developers.openai.com/api/docs/pricing"
OPENAI_MARKDOWN_URL = f"{OPENAI_URL}.md"

OUTPUT_MAPPINGS = {
    "input": "input",
    "cached input": "cache_hit",
    "cache writes": "cache_write",
    "output": "output",
}

# A service level is published as "<Tier> pricing data", so the names a document
# uses for its own tiers are read out of its headings rather than listed here: a
# tier the page adds later is a tier this reader already knows.
TIER_HEADING_RE = re.compile(r"([A-Za-z][A-Za-z-]*) pricing data", re.I)
MARKDOWN_SEPARATOR_RE = re.compile(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$")
# A sentence the page writes between two tables is not a label. The labels are
# short phrases ("Cyber models", "Standard", "Transcription models").
SENTENCE_ENDS = (".", "。", "!", "！", "?", "？")

# A table whose columns head a training charge prices fine-tuning rather than the
# model's use, and its rows name training snapshots.
TRAINING_COLUMN = "training"
# A table prices a model when one of its columns names the model.
MODEL_HEADERS = {"model", "model name"}
# The columns that state a service level per row rather than one per table.
CONTEXT_COLUMN_MARKERS = ("short context", "long context")


def tier_names(document: str) -> set[str]:
    """The service levels a document names in its own headings."""
    return {
        match.group(1).lower()
        for line in document.splitlines()
        if line.startswith("#")
        and (match := TIER_HEADING_RE.fullmatch(clean_text(line.lstrip("# "))))
    }


def openai_sections(
    document: str,
) -> Iterator[tuple[str, str, str, list[list[str]]]]:
    """Yield each table with the section and service level it is filed under.

    The page writes a table's section as a line of its own ("Image generation
    models", then "Standard"), so the last label published above a table is what
    it is read under, and a label naming a service level is that table's tier. A
    heading is not a label: it either names a tier itself, which the first four
    sections do, or says nothing about this table at all.
    """
    tiers = tier_names(document)
    section = ""
    tier = ""
    heading = ""
    lines = document.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        text = clean_text(line)
        if text.startswith("|") and index + 1 < len(lines) and MARKDOWN_SEPARATOR_RE.match(
            lines[index + 1]
        ):
            rows = [split_markdown_row(line)]
            index += 2
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                rows.append(split_markdown_row(lines[index]))
                index += 1
            yield section, tier, heading, rows
            continue
        if line.startswith("#"):
            heading = clean_text(line.lstrip("# ")).strip()
            match = TIER_HEADING_RE.fullmatch(heading)
            if match:
                tier = match.group(1).lower()
        elif (
            text
            and not text.endswith(SENTENCE_ENDS)
            # A label stands alone: prose that wraps reaches this reader as lines
            # with text on both sides, and the last line of a sentence is not the
            # name of anything.
            and not lines[index - 1].strip()
            and index + 1 < len(lines)
            and not lines[index + 1].strip()
        ):
            if text.lower() in tiers:
                tier = text.lower()
            else:
                section = text
                tier = ""
        index += 1


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
        rows: list[dict[str, Any]] = []
        for section, tier, _heading, table in openai_sections(
            self.document(OPENAI_MARKDOWN_URL)
        ):
            if len(table) < 2:
                continue
            headers = [clean_text(cell).lower() for cell in table[0]]
            model_index = next(
                (
                    index
                    for index, header in enumerate(headers)
                    if header in MODEL_HEADERS
                ),
                None,
            )
            # The tools table names a tool in its first column, and a table that
            # heads a training charge prices fine-tuning rather than a model's use.
            if model_index is None or TRAINING_COLUMN in headers:
                continue
            if any(marker in header for header in headers for marker in CONTEXT_COLUMN_MARKERS):
                rows.extend(
                    self._token_rows(table, headers, model_index, section, tier)
                )
            else:
                rows.extend(
                    self._unit_rows(table, headers, model_index, section, tier)
                )
        self._parsed_rows = rows
        return self._parsed_rows

    @staticmethod
    def _token_rows(
        table: list[list[str]],
        headers: list[str],
        model_index: int,
        section: str,
        tier: str,
    ) -> list[dict[str, Any]]:
        """Read a table that states one price per context window and charge."""
        rows = []
        for cells in table[1:]:
            cells = [*cells, *([""] * (len(headers) - len(cells)))]
            display_name = markdown_link_text(cells[model_index])
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
                    # The cyber models are published without a tier heading: an
                    # empty service level is not a level, so it is left out rather
                    # than carried as one.
                    offers.append(
                        {
                            "name": tier or normalize_model(section),
                            "conditions": {
                                **({"service_tier": tier} if tier else {}),
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

    @staticmethod
    def _unit_rows(
        table: list[list[str]],
        headers: list[str],
        model_index: int,
        section: str,
        tier: str,
    ) -> list[dict[str, Any]]:
        """Read a table whose columns each price one charge, in any unit.

        The modality, use case, or category a row is filed under is a condition,
        and a cell that publishes several amounts at once is expanded into one
        offer per amount: a text rate and an image rate of one model are different
        charges, and merging them would quote one for the other.
        """
        labels = [clean_text(cell) for cell in table[0]]
        priced = price_column_kinds(labels, table, currency="USD")
        rows = []
        for cells in table[1:]:
            cells = [*cells, *([""] * (len(headers) - len(cells)))]
            display_name = markdown_link_text(cells[model_index])
            model_id = without_trailing_parenthetical(display_name)
            if not model_id:
                continue
            conditions: dict[str, Any] = {"source_section": section}
            if tier:
                conditions["service_tier"] = tier
            for index in range(len(headers)):
                if index in priced or index == model_index:
                    continue
                value = clean_text(cells[index])
                if value and value not in ("-", "—"):
                    conditions[labels[index]] = value
            offers: dict[tuple[str, ...], dict[str, Any]] = {}
            for index, kind in priced.items():
                for rate in cell_rates(cells[index], header=labels[index], currency="USD"):
                    scope = tuple(rate.conditions)
                    offer = offers.setdefault(
                        scope, {"conditions": dict(conditions), "prices": []}
                    )
                    if scope:
                        offer["conditions"]["price_scope"] = "；".join(scope)
                    offer["prices"].append(
                        price_item(
                            kind,
                            labels[index],
                            rate.amount,
                            price_unit_code(
                                rate.unit_phrase,
                                labels[index],
                                currency="USD",
                                default="USD_per_million_tokens",
                            ),
                            display=rate.display,
                            list_amount=rate.list_amount,
                            discount=rate.discount,
                        )
                    )
            rows.append(
                {
                    "model_id": model_id,
                    "display_name": display_name,
                    "offers": [
                        {"name": tier or normalize_model(section) or "standard", **offer}
                        for offer in offers.values()
                    ],
                }
            )
        return rows

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
