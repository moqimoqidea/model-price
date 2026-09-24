"""Shared adapter for documented tables of per-model prices."""

from __future__ import annotations

import re
from typing import Any

from ..core import HttpClient, PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, normalize_model, split_trailing_parenthetical
from ..parsing import (
    headed_document_tables,
    monetary_amount,
    time_band_label,
    time_bands_for,
    token_price_kind,
)
from ..pricing import make_record, price_item
from ..text import CELL_BREAK_RE, clean_text

# Condition columns whose vendor wording maps onto a shared schema concept.
CONDITION_ALIASES = {
    "条件": "context_tier",
    "上下文": "context_tier",
}

# A model cell may carry a note in a second segment ("正式版<br>> 调整前价格");
# only the first segment names the model. The break itself is split by
# ``text.CELL_BREAK_RE``.
MODEL_CELL_NOTE_RE = re.compile(r"^[>\s]+")
# Cells that stand for "not applicable" rather than carrying a value.
PLACEHOLDER_VALUES = {"", "-", "—", "–", "不适用"}
# A table is only read when one of its columns is labelled as the model column.
MODEL_HEADERS = {"model", "model name", "模型", "模型名称"}


class TabularTokenPricingAdapter(PriceSource):
    """Parse a documented table of per-model token prices.

    The document may arrive as HTML or as Markdown. Subclasses declare where it
    lives, its currency and region, and override only the policy that differs:
    which headers are prices, how an amount is read, and how an offer is named.
    Rows are identified by their header wording and parsed prices, never by a
    hard-coded list of model names.
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

    def document_text(self) -> str:
        """Return the document to parse; override when it is fetched indirectly."""
        return self.document(self.source_url)

    def price_kind(self, header: str) -> str | None:
        """Classify a price column, or return ``None`` when it is a condition."""
        return token_price_kind(header)

    def cell_amount(self, cell: str, header: str) -> str | None:
        return monetary_amount(cell, header, self.currency)

    def offer_name(self, headings: list[str]) -> str:
        heading = headings[-1] if headings else ""
        return normalize_model(heading) or self.default_offer_name

    def row_offer_name(self, headings: list[str], conditions: dict[str, Any]) -> str:
        """Name an offer after reading row conditions such as its billing mode."""
        return self.offer_name(headings)

    def row_conditions(self, conditions: dict[str, Any]) -> dict[str, Any]:
        """Keep only the conditions that distinguish the named offer."""
        return conditions

    def model_variants(self, display_name: str) -> list[str]:
        """Expand a model cell when one priced row names several model IDs."""
        return [display_name]

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

    def price_unit(self, header: str) -> str:
        if "小时" in header or "hour" in header.lower():
            return f"{self.currency}_per_million_tokens_per_hour"
        return f"{self.currency}_per_million_tokens"

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

    def _rows(self) -> list[dict[str, Any]]:
        if self._parsed_rows is not None:
            return self._parsed_rows
        rows: list[dict[str, Any]] = []
        for headings, table in headed_document_tables(self.document_text()):
            if len(table) < 2:
                continue
            raw_headers = table[0]
            headers = [clean_text(cell) for cell in raw_headers]
            model_index = self.model_column(headers)
            price_columns = {
                index: kind
                for index, header in enumerate(headers)
                if (kind := self.price_kind(header)) is not None
            }
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
                prices = []
                for index, kind in price_columns.items():
                    amount = self.cell_amount(cells[index], headers[index])
                    if amount is not None:
                        prices.append(
                            price_item(
                                kind,
                                headers[index],
                                amount,
                                self.price_unit(headers[index]),
                                display=clean_text(cells[index]),
                            )
                        )
                shared_conditions = self.model_conditions(display_name)
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
                        shared_conditions[key] = value
                shared_conditions["billing_mode"] = "pay_as_you_go"
                if headings:
                    shared_conditions["source_section"] = headings[-1]
                offer_name = self.row_offer_name(headings, shared_conditions)
                shared_conditions = self.row_conditions(shared_conditions)
                for model_name in self.model_variants(display_name):
                    conditions = dict(shared_conditions)
                    # A trailing parenthetical often carries a real billing tier
                    # ("grok-4.6 (≥ 200k prompt tokens)"). It is dropped from the
                    # model id, so keep it as a condition instead of losing it.
                    name_without_tier, tier = split_trailing_parenthetical(model_name)
                    if tier:
                        conditions["context_tier"] = tier
                    if note:
                        conditions["model_note"] = note
                    rows.append(
                        {
                            "model_id": normalize_model(name_without_tier),
                            "display_name": model_name,
                            "offer_name": offer_name,
                            "conditions": conditions,
                            "prices": prices,
                        }
                    )
        if not rows:
            raise SourceError("official token pricing table was not found")
        self._parsed_rows = rows
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
            self.source_url,
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
