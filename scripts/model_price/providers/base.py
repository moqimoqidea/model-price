"""Shared adapter for documented tables of per-model prices."""

from __future__ import annotations

import re
from typing import Any, Iterable

from ..core import HttpClient, PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, normalize_model, split_trailing_parenthetical
from ..parsing import (
    CellRate,
    cell_rates as read_cell_rates,
    headed_document_tables,
    monetary_amount,
    price_column_kinds,
    price_kind as header_price_kind,
    price_unit_code,
    time_band_label,
    time_bands_for,
)
from ..pricing import make_record, price_item
from ..text import CELL_BREAK_RE, clean_text

# Condition columns whose vendor wording maps onto a shared schema concept.
CONDITION_ALIASES = {
    "条件": "context_tier",
    "上下文": "context_tier",
}

# Where the words a cell groups its rates under are filed. A vendor that prices one
# charge in several tiers says in the cell what each tier is for ("单图生成场景：≤ 261
# 万像素"), and that scope is what tells two rates of one model apart — merged, they
# read as one price the page never published.
PRICE_SCOPE_CONDITION = "price_scope"

# A model cell may carry a note in a second segment ("正式版<br>> 调整前价格");
# only the first segment names the model. The break itself is split by
# ``text.CELL_BREAK_RE``.
MODEL_CELL_NOTE_RE = re.compile(r"^[>\s]+")
# Cells that stand for "not applicable" rather than carrying a value.
PLACEHOLDER_VALUES = {"", "-", "—", "–", "不适用"}
# A table is only read when one of its columns is labelled as the model column.
MODEL_HEADERS = {"model", "model name", "模型", "模型名称"}


class TabularPricingAdapter(PriceSource):
    """Parse a documented table of per-model prices, billed in any unit.

    The document may arrive as HTML or as Markdown. Subclasses declare where it
    lives, its currency and region, and override only the policy that differs:
    which headers are prices, how an amount is read, and how an offer is named.
    Columns are identified by what they say and what they hold — a price column is
    one whose header names a charge, or whose cells publish amounts in this
    adapter's currency — never by a hard-coded list of model names.
    """

    currency: str
    region: str
    delivery_mode = "first_party"
    default_offer_name = "pay_as_you_go"
    # Some vendors leave the model cell empty on a continuation row, so that row
    # inherits the model above it while still carrying its own conditions.
    carry_forward_model = False
    # Vendors that bill by time of day explain the window in prose beside the
    # table. Only they get a schedule read out of the document — a platform that
    # never mentions one must not be handed another platform's window.
    publishes_time_bands = False

    def __init__(self, client: HttpClient) -> None:
        super().__init__(client)
        self._parsed_rows: list[dict[str, Any]] | None = None

    # --- policy -----------------------------------------------------------

    @property
    def default_unit(self) -> str:
        """What a column prices in when neither its header nor its cells say."""
        return f"{self.currency}_per_million_tokens"

    def document_text(self) -> str:
        """Return the document to parse; override when it is fetched indirectly."""
        return self.document(self.source_url)

    def source_documents(self) -> tuple[tuple[str, str], ...]:
        """Return every document this vendor prices from, each with its own page.

        Most vendors publish one price page. A vendor that splits its catalogue
        across two (Kling prices images and video separately) declares both here,
        and a row keeps the page it was read from so a record points at the page
        that actually published its rate.
        """
        return ((self.source_url, self.document_text()),)

    def price_kind(self, header: str) -> str | None:
        """Classify a price column, or return ``None`` when it is a condition."""
        return header_price_kind(header)

    def cell_amount(self, cell: str, header: str) -> str | None:
        return monetary_amount(cell, header, self.currency)

    def cell_rates(self, cell: str, header: str) -> list[CellRate]:
        """Every rate one cell publishes, with the vendor's own scope for it."""
        return read_cell_rates(cell, header=header, currency=self.currency)

    def price_unit(self, header: str, cell_unit: str = "") -> str:
        """The unit a column's amount is billed in."""
        return price_unit_code(
            cell_unit, header, currency=self.currency, default=self.default_unit
        )

    def offer_name(self, headings: list[str]) -> str:
        heading = headings[-1] if headings else ""
        return normalize_model(heading) or self.default_offer_name

    def row_offer_name(self, headings: list[str], conditions: dict[str, Any]) -> str:
        """Name an offer after reading row conditions such as its billing mode."""
        return self.offer_name(headings)

    def row_conditions(self, conditions: dict[str, Any]) -> dict[str, Any]:
        """Keep only the conditions that distinguish the named offer."""
        return conditions

    def model_variants(self, display_name: str, note: str = "") -> list[str]:
        """Expand a model cell when one priced row names several model IDs.

        ``note`` is the rest of the cell after its first segment. It is a note by
        default — a vendor explaining a rate there is not naming another model —
        and an adapter whose cells list several IDs opts into reading it.
        """
        return [display_name]

    def model_note_condition(self) -> str:
        """Where the words a model cell puts after its name are filed."""
        return "model_note"

    def heading_conditions(self, headings: list[str]) -> dict[str, Any]:
        """Conditions a vendor states in the section a table is filed under.

        A service tier or a legacy section is stated by the container the table
        sits in rather than by any column, so it reaches the row from here.
        """
        return {}

    def includes_table(self, headings: list[str]) -> bool:
        """Whether this section prices this hosting provider's own catalogue."""
        return True

    def model_conditions(self, display_name: str) -> dict[str, Any]:
        return {}

    def record_extras(self) -> dict[str, Any]:
        return {}

    def model_extras(self, model_id: str, display_name: str) -> dict[str, Any]:
        """Extras that need the row's identity, such as its peak/off-peak window."""
        if not self.publishes_time_bands:
            return {}
        return {
            "time_bands": time_bands_for(
                self.document_text(),
                model_id=model_id,
                display_name=display_name,
                source_url=self.source_url,
            )
        }

    # --- parsing ----------------------------------------------------------

    @staticmethod
    def condition_name(header: str) -> str:
        name = clean_text(CELL_BREAK_RE.split(header, maxsplit=1)[0])
        return CONDITION_ALIASES.get(name, name)

    def model_column(self, headers: list[str]) -> int | None:
        """Locate the column naming the model, or return ``None`` to skip the table.

        Requiring the label keeps look-up and example tables out of the catalogue:
        their first column holds a resolution or a billing category, not a model.
        """
        return next(
            (
                index
                for index, header in enumerate(headers)
                if clean_text(header).lower() in MODEL_HEADERS
            ),
            None,
        )

    @staticmethod
    def _model_cell(raw: str) -> tuple[str, str]:
        """Split a model cell into its display name and an explanatory note.

        A cell may carry a second segment after the break ("deepseek-v4-flash
        正式版<br>调整前价格，2026-08-21 起不适用"); only the first names the model.
        """
        segments = CELL_BREAK_RE.split(raw)
        display_name = clean_text(segments[0])
        if display_name in PLACEHOLDER_VALUES:
            display_name = ""
        note = clean_text(" ".join(segments[1:]))
        return display_name, MODEL_CELL_NOTE_RE.sub("", note)

    def _row_offers(
        self,
        cell_rates: Iterable[tuple[str, str, CellRate]],
        conditions: dict[str, Any],
    ) -> dict[tuple[str, ...], dict[str, Any]]:
        """Group one row's rates into the offers its cells scoped them into.

        Rates the vendor published under one scope are one offer with several
        amounts; a scope that differs makes another offer, so a table that prices
        four tiers in one cell is reported as four prices rather than one.
        """
        grouped: dict[tuple[str, ...], dict[str, Any]] = {}
        for kind, header, rate in cell_rates:
            scope = tuple(rate.conditions)
            offer = grouped.setdefault(
                scope,
                {
                    "conditions": dict(conditions),
                    "prices": [],
                },
            )
            if scope:
                offer["conditions"][PRICE_SCOPE_CONDITION] = "；".join(scope)
            offer["prices"].append(
                price_item(
                    kind,
                    header,
                    rate.amount,
                    self.price_unit(header, rate.unit_phrase),
                    display=rate.display,
                    list_amount=rate.list_amount,
                    discount=rate.discount,
                )
            )
        return grouped

    def _rows(self) -> list[dict[str, Any]]:
        if self._parsed_rows is not None:
            return self._parsed_rows
        rows: list[dict[str, Any]] = []
        for source_url, text in self.source_documents():
            rows.extend(self._document_rows(text, source_url))
        if not rows:
            raise SourceError("official pricing table was not found")
        self._parsed_rows = rows
        return rows

    def _document_rows(self, text: str, source_url: str) -> list[dict[str, Any]]:
        """Read every priced row one document publishes."""
        rows: list[dict[str, Any]] = []
        for headings, table in headed_document_tables(text):
            if len(table) < 2 or not self.includes_table(headings):
                continue
            raw_headers = table[0]
            headers = [clean_text(cell) for cell in raw_headers]
            model_index = self.model_column(headers)
            price_columns = price_column_kinds(
                headers, table, currency=self.currency, kind_of=self.price_kind
            )
            if model_index is None or not price_columns:
                continue
            carried = ("", "")
            for cells in table[1:]:
                cells = [*cells, *([""] * (len(headers) - len(cells)))]
                display_name, note = self._model_cell(cells[model_index])
                if display_name:
                    carried = (display_name, note)
                elif self.carry_forward_model:
                    display_name, note = carried
                if not display_name:
                    continue
                conditions = {
                    **self.heading_conditions(headings),
                    **self.model_conditions(display_name),
                }
                for index in range(len(headers)):
                    if index == model_index or index in price_columns:
                        continue
                    value = clean_text(cells[index])
                    if value and value not in PLACEHOLDER_VALUES:
                        # A band is filed in whichever condition column the vendor
                        # keeps, so the cell value decides that key.
                        key = (
                            "time_band"
                            if time_band_label(value)
                            else self.condition_name(raw_headers[index])
                        )
                        conditions[key] = value
                conditions["billing_mode"] = "pay_as_you_go"
                if headings:
                    conditions["source_section"] = headings[-1]
                offer_name = self.row_offer_name(headings, conditions)
                conditions = self.row_conditions(conditions)
                rates = [
                    (kind, headers[index], rate)
                    for index, kind in price_columns.items()
                    for rate in self.cell_rates(cells[index], headers[index])
                ]
                offers = self._row_offers(rates, conditions) or {
                    # A model the vendor lists without a price this tool can read
                    # stays in the catalogue with no offer: its listing and the
                    # later publication of its price are two separate facts, and
                    # dropping the row would report neither.
                    (): {"conditions": dict(conditions), "prices": []}
                }
                for scope, offer in offers.items():
                    variants = self.model_variants(display_name, note)
                    for model_name in variants:
                        scoped = dict(offer["conditions"])
                        # A trailing parenthetical often carries a real billing tier
                        # ("grok-4.6 (≥ 200k prompt tokens)"). It is dropped from the
                        # model id, so keep it as a condition instead of losing it.
                        name_without_tier, tier = split_trailing_parenthetical(
                            model_name
                        )
                        if tier:
                            scoped["context_tier"] = tier
                        # Words the adapter read as another model ID are not a
                        # condition on this one, and printing them as both would
                        # report one fact twice.
                        if note and note not in variants:
                            scoped[self.model_note_condition()] = note
                        rows.append(
                            {
                                "model_id": normalize_model(name_without_tier),
                                "display_name": model_name,
                                "offer_name": offer_name,
                                "conditions": scoped,
                                "prices": offer["prices"],
                                "source_url": source_url,
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

    def _rows_by_model(self) -> dict[str, list[dict[str, Any]]]:
        """Group the parsed rows by model, so a model is built from its own rows."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in self._rows():
            grouped.setdefault(normalize_model(row["model_id"]), []).append(row)
        return grouped

    def _record_for(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Build the record for every row that shares one model."""
        first = rows[0]
        return make_record(
            self.provider_id,
            self.provider_name,
            first["model_id"],
            first["display_name"],
            self.region,
            [
                {
                    "name": row["offer_name"],
                    "conditions": row["conditions"],
                    "prices": row["prices"],
                }
                for row in rows
                if row["prices"]
            ],
            first.get("source_url") or self.source_url,
            self.source_kind,
            now_iso(),
            currency=self.currency,
            delivery_mode=self.delivery_mode,
            model_family=model_family(first["model_id"]),
            **self.model_extras(first["model_id"], first["display_name"]),
            **self.record_extras(),
        )

    def query(self, model: str) -> list[dict[str, Any]]:
        rows = self._rows_by_model().get(normalize_model(model))
        return [self._record_for(rows)] if rows else []

    def catalog_records(self) -> list[dict[str, Any]]:
        """Build every record from the document already parsed, in a single pass."""
        grouped = self._rows_by_model()
        return [self._record_for(grouped[key]) for key in sorted(grouped)]
