"""Preserve Mistral's published API identities, units, and pricing controls.

The price page renders the standard table. Its own client publishes the batch,
priority, and regional multipliers; those alternatives stay separate offers.
Models listed without rates stay unpriced even when an older detail has figures.
"""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from ..core import HttpClient
from ..errors import SourceError
from ..mistral_catalogue import (
    MISTRAL_MODELS_URL,
    MISTRAL_PRICING_URL,
    PRICING_BUNDLE_PATH,
    STRING_LITERAL,
    literal_at,
    model_label,
    read_catalogue,
    script_url,
)
from ..models import model_matches, normalize_model
from ..parsing import CellRate
from ..pricing import is_free_amount, is_free_statement
from .base import TabularPricingAdapter

MODE_FACTOR_RE = re.compile(
    r"\{value:(" + STRING_LITERAL + r"),label:.*?factor:(\d*\.?\d+)(?=[,}])",
    re.S,
)
REGIONAL_FACTOR_RE = re.compile(r"\*\(\w+\?(\d*\.?\d+):1\)")
PRICE_ANNOTATION_RE = re.compile(r"(?:Original|Sale) price:\s*", re.I)


def pricing_factors(bundle: str) -> tuple[list[tuple[str, Decimal]], Decimal]:
    """Read only multipliers actually published by the page's pricing controls."""
    modes = [
        (literal_at(match[1]), Decimal(match[2]))
        for match in MODE_FACTOR_RE.finditer(bundle)
    ]
    region = REGIONAL_FACTOR_RE.search(bundle)
    if dict(modes).get("standard") != 1 or not region:
        raise SourceError("Mistral pricing controls published no readable billing factors")
    if (
        len({name for name, _ in modes}) != len(modes)
        or any(factor <= 0 for _, factor in modes)
    ):
        raise SourceError("Mistral pricing controls published inconsistent billing factors")
    return modes, Decimal(region[1])


def scaled_price(price: dict[str, Any], factor: Decimal) -> dict[str, Any]:
    """Apply the official control's arithmetic, keeping sale and list prices apart."""
    result = dict(price)
    if factor != 1:
        for key in ("amount", "list_amount"):
            if result.get(key) is not None:
                amount = (Decimal(result[key]) * factor).quantize(
                    Decimal("0.00001"), rounding=ROUND_HALF_UP
                )
                result[key] = format(amount.normalize(), "f")
        if not is_free_amount(price.get("amount")):
            result.pop("display", None)
    return result


class MistralAdapter(TabularPricingAdapter):
    provider_id = "mistral"
    provider_name = "Mistral AI"
    source_url = MISTRAL_PRICING_URL
    catalog_url = MISTRAL_MODELS_URL
    source_kind = "official_html"
    currency = "USD"
    region = "全球"

    def __init__(self, client: HttpClient) -> None:
        super().__init__(client)
        self._price_models: dict[str, dict[str, Any]] | None = None
        self._factors: tuple[list[tuple[str, Decimal]], Decimal] | None = None

    def model_variants(self, display_name: str, note: str = "") -> list[str]:
        key = normalize_model(model_label(display_name))
        if self._price_models is None:
            entries = read_catalogue(self.client).linked_entries(self.document_text())
            self._price_models = {}
            for entry in entries:
                label = normalize_model(entry["name"])
                if label in self._price_models:
                    raise SourceError("Mistral price page linked ambiguous model names")
                self._price_models[label] = entry
        entry = self._price_models.get(key)
        if entry is None or not entry["identifiers"]["apiNames"]:
            raise SourceError(
                "Mistral price row has no unique documented API identity: "
                + display_name
            )
        return entry["identifiers"]["apiNames"]

    def cell_rates(self, cell: str, header: str) -> list[CellRate]:
        # Accessible Original/Sale prefixes sit inside the HTML's del/ins tags.
        # They label price terms, rather than naming two independent rate scopes.
        normalized = PRICE_ANNOTATION_RE.sub("", cell).replace("~~", " ~~ ")
        rates = super().cell_rates(normalized, header)
        # The free moderation row names no meter in its mixed-unit table.
        # Keep that absence rather than inventing a per-token charge.
        if is_free_statement(cell):
            return [rate._replace(unit_phrase="provider_defined") for rate in rates]
        return rates

    def price_unit(self, header: str, cell_unit: str = "") -> str:
        if cell_unit == "provider_defined":
            return cell_unit
        return super().price_unit(header, cell_unit)

    def offer_name(self, headings: list[str]) -> str:
        return "standard"

    def heading_conditions(self, headings: list[str]) -> dict[str, Any]:
        return {"service_tier": "standard"}

    def _document_rows(self, text: str, source_url: str) -> list[dict[str, Any]]:
        rows = super()._document_rows(text, source_url)
        if not rows:
            raise SourceError("Mistral's official pricing table was not found")
        known = {row["model_id"] for row in rows}
        for entry in read_catalogue(self.client).current_entries():
            for model_id in entry["identifiers"]["apiNames"]:
                if normalize_model(model_id) not in known:
                    rows.append(
                        {
                            "model_id": model_id,
                            "display_name": entry["name"],
                            "offer_name": "standard",
                            "conditions": {},
                            "prices": [],
                            "source_url": self.catalog_url,
                        }
                    )
                    known.add(normalize_model(model_id))
        return rows

    def _record_for(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        record = super()._record_for(rows)
        entry = read_catalogue(self.client).for_api(record["model_id"])
        if entry is None:
            raise SourceError("Mistral price identity is absent from the official model data")
        record["display_name"] = entry["name"]
        if "third-party" in (entry.get("tags") or []):
            record["delivery_mode"] = "third_party_hosted"
        if not record["offers"]:
            return record
        if self._factors is None:
            controls_url = script_url(self.document_text(), PRICING_BUNDLE_PATH)
            self._factors = pricing_factors(self.document(controls_url))
        modes, regional_factor = self._factors
        record["offers"] = [
            {
                "name": mode,
                "conditions": {
                    **offer["conditions"],
                    "service_tier": mode,
                    "inference_scope": "regional" if regional else "default",
                },
                "prices": [
                    scaled_price(price, factor * (regional_factor if regional else 1))
                    for price in offer["prices"]
                ],
            }
            for regional in (False, True)
            for mode, factor in modes
            for offer in record["offers"]
        ]
        return record

    def search(self, model: str, *, exact: bool = False) -> list[dict[str, Any]]:
        if exact:
            return self.query(model)
        catalogue = read_catalogue(self.client)
        candidates: list[dict[str, Any]] = []
        for model_id in self.list_models():
            entry = catalogue.for_api(model_id)
            if model_matches(model, model_id) or (
                entry is not None and model_matches(model, entry["name"])
            ):
                candidates.extend(self.query(model_id))
        return candidates
