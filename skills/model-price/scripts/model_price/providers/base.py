"""Shared parser for first-party pay-as-you-go token pricing tables."""

from __future__ import annotations

import re
from typing import Any

from ..core import HttpClient, PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, normalize_model
from ..parsing import headed_document_tables, monetary_amount, token_price_kind
from ..pricing import make_record, price_item
from ..text import clean_text


class TabularTokenPricingAdapter(PriceSource):
    """Parse a documented table of per-million-token prices.

    Subclasses only declare where the table lives, its currency, and its region.
    Rows are identified by their header wording and parsed prices, never by a
    hard-coded list of model names.
    """

    currency: str
    region: str
    offer_name = "pay_as_you_go"

    def __init__(self, client: HttpClient) -> None:
        super().__init__(client)
        self._parsed_rows: list[dict[str, Any]] | None = None

    @staticmethod
    def _model_column(headers: list[str]) -> int | None:
        for index, header in enumerate(headers):
            value = clean_text(header).lower()
            if value in {"model", "model name", "模型", "模型名称"}:
                return index
        # Some vendors label the first column after the product line
        # ("MiMo-V2.5 系列") instead of a generic "Model". The leftmost column
        # carries the model identity unless it is itself a price column.
        if headers and token_price_kind(headers[0]) is None:
            return 0
        return None

    def _rows(self) -> list[dict[str, Any]]:
        if self._parsed_rows is not None:
            return self._parsed_rows
        rows: list[dict[str, Any]] = []
        for heading, table in headed_document_tables(
            self.client.get_text(self.source_url)
        ):
            if len(table) < 2:
                continue
            headers = [clean_text(cell) for cell in table[0]]
            model_index = self._model_column(headers)
            price_columns = {
                index: kind
                for index, header in enumerate(headers)
                if (kind := token_price_kind(header)) is not None
            }
            if model_index is None or not price_columns:
                continue
            for cells in table[1:]:
                cells = [*cells, *([""] * (len(headers) - len(cells)))]
                display_name = clean_text(cells[model_index])
                if not display_name:
                    continue
                prices = []
                for index, kind in price_columns.items():
                    amount = monetary_amount(
                        cells[index], headers[index], self.currency
                    )
                    if amount is not None:
                        prices.append(
                            price_item(
                                kind,
                                headers[index],
                                amount,
                                f"{self.currency}_per_million_tokens",
                                display=clean_text(cells[index]),
                            )
                        )
                if not prices:
                    continue
                model_id = normalize_model(
                    re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", display_name)
                )
                conditions = {
                    headers[index]: clean_text(cells[index])
                    for index in range(len(headers))
                    if index != model_index
                    and index not in price_columns
                    and clean_text(cells[index])
                }
                # A trailing parenthetical often carries a real billing tier
                # ("grok-4.6 (≥ 200k prompt tokens)"). It is dropped from the
                # model id, so keep it as a condition instead of losing it.
                tier = re.search(r"[（(]([^）)]*)[）)]\s*$", display_name)
                if tier and clean_text(tier.group(1)):
                    conditions["context_tier"] = clean_text(tier.group(1))
                conditions["billing_mode"] = "pay_as_you_go"
                if heading:
                    conditions["source_section"] = heading
                rows.append(
                    {
                        "model_id": model_id,
                        "display_name": display_name,
                        "offer_name": normalize_model(heading) or self.offer_name,
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
                self.region,
                [
                    {
                        "name": row["offer_name"],
                        "conditions": row["conditions"],
                        "prices": row["prices"],
                    }
                    for row in matched
                ],
                self.source_url,
                self.source_kind,
                now_iso(),
                currency=self.currency,
                delivery_mode="first_party",
                model_family=model_family(matched[0]["model_id"]),
            )
        ]
