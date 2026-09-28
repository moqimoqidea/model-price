"""AWS Bedrock's published price lists, read from the offer files AWS bills from.

Bedrock's pricing page fills its tables from JavaScript, so the page itself carries
placeholders rather than amounts. The same figures are published as offer files: one
for the models AWS serves under its own offer code, one for the models billed
through AWS Marketplace. Each is self-describing — the model, the region, the
charge, the unit and the amount in one record — and neither is complete alone, so
both are read.

A rate is kept in the unit its own record states. This list prices tokens, images,
processed pages, search units, provisioned throughput and custom-model minutes side
by side, and one default unit would misprice all but one of them.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from ..core import PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, normalize_model
from ..parsing import price_unit_code
from ..pricing import make_record, per_million_tokens, price_item

BEDROCK_OFFER_BASE = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws"
BEDROCK_MODELS_URL = f"{BEDROCK_OFFER_BASE}/AmazonBedrock/current/index.json"
BEDROCK_MARKETPLACE_URL = (
    f"{BEDROCK_OFFER_BASE}/AmazonBedrockFoundationModels/current/index.json"
)
BEDROCK_PRICING_PAGE = "https://aws.amazon.com/bedrock/pricing/"

# The suffix AWS Marketplace listings carry in the model name they publish.
MARKETPLACE_SUFFIX = " (Amazon Bedrock Edition)"

# What each charge the lists name is, in the order a bill is read. The two files
# word the same charge differently — "Input tokens" in one, "Million Input Tokens
# Global" in the other, "Response tokens" for what the other calls output — and each
# wording is matched on its own terms.
BEDROCK_CHARGES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("cacheread", "cache read"), "cache_hit"),
    (("cachewrite", "cache write"), "cache_write"),
    (("input", "inputtoken", "inputtoken"), "input"),
    (("output", "outputtoken", "response"), "output"),
)

# What a charge is billed under, where the list says so in the charge's own name.
# A batch rate is half price, a provisioned-throughput reservation is bought by the
# hour, and a custom model is billed for the tuning and the storage it needs — all
# real rates, and none of them the model's ordinary one. Read as part of the
# standard offer, a batch rate would be reported as what the model costs.
BEDROCK_TIERS: tuple[tuple[str, str], ...] = (
    ("batch", "batch"),
    ("priority", "priority"),
    ("provisioned", "provisioned"),
    ("reserved", "provisioned"),
    ("custom", "custom"),
    ("storage", "custom"),
    ("tuning", "custom"),
)
BEDROCK_STANDARD_TIER = "standard"

# The units these lists publish, and the measure each one bills against. The scale
# a token rate is quoted at is the vendor's own ("1K tokens") and is applied here,
# so every token rate reaches the report in the one unit it compares in. A unit
# absent from this table keeps the vendor's own wording and its figure as published.
BEDROCK_UNITS: dict[str, tuple[str, int]] = {
    "1k tokens": ("/1k tokens", 1_000),
    "1m tokens": ("/1m tokens", 1_000_000),
    "image": ("/image", 1),
    "images processed": ("/image", 1),
    "second": ("/second", 1),
    "seconds": ("/second", 1),
    "hour": ("/hour", 1),
    "hours": ("/hour", 1),
    "1 hour": ("/hour", 1),
    "1/hour": ("/hour", 1),
    "queries": ("/query", 1),
    "requests": ("/request", 1),
    "text requests": ("/request", 1),
    "api calls": ("/request", 1),
    "per 1000 requests": ("/1k requests", 1),
    "pages processed": ("/page", 1),
    "video": ("/video", 1),
}


def bedrock_payload(document: str, offer: str) -> dict[str, Any]:
    """Read one offer file."""
    try:
        parsed = json.loads(document)
    except json.JSONDecodeError as exc:
        raise SourceError(f"AWS {offer} price list was not JSON") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("products"), dict):
        raise SourceError(f"unexpected AWS {offer} price list shape")
    return parsed


def bedrock_model_name(attributes: dict[str, Any], name_field: str) -> str:
    """The model a product prices, named by the field that file names it in.

    The two offer files were written by different teams: the first-party list names
    the model in ``model``, and every other product in it is a service charge rather
    than a model. The Marketplace list names it in ``servicename``, where the suffix
    says which of the two lists a name came from and comes off again.
    """
    name = str(attributes.get(name_field) or "").strip()
    if name.endswith(MARKETPLACE_SUFFIX):
        name = name[: -len(MARKETPLACE_SUFFIX)].strip()
    return name


def bedrock_charge_label(attributes: dict[str, Any], description: str) -> str:
    """What a rate charges, in the words the publishing file used for it."""
    stated = str(attributes.get("inferenceType") or "").strip()
    if stated:
        return stated
    parts = [part.strip() for part in description.split("|") if part.strip()]
    return parts[-1] if parts else description


def bedrock_charge_kind(label: str) -> str:
    """The kind of charge a label names, or ``other`` for one not read yet."""
    wording = label.lower().replace(" ", "").replace("-", "")
    for markers, kind in BEDROCK_CHARGES:
        if any(marker.replace(" ", "") in wording for marker in markers):
            return kind
    return "other"


def bedrock_tier(label: str) -> str:
    """The tier a charge's own name bills it at, or the standard one."""
    wording = label.lower()
    for marker, tier in BEDROCK_TIERS:
        if marker in wording:
            return tier
    return BEDROCK_STANDARD_TIER


def bedrock_rates(
    offer: dict[str, Any], *, name_field: str
) -> Iterator[dict[str, Any]]:
    """Every on-demand rate one offer file publishes, with its own unit."""
    products = offer["products"]
    for sku, terms in (offer.get("terms") or {}).get("OnDemand", {}).items():
        attributes = (products.get(sku) or {}).get("attributes") or {}
        model = bedrock_model_name(attributes, name_field)
        if not model:
            continue
        for term in terms.values():
            for rate in (term.get("priceDimensions") or {}).values():
                amount = (rate.get("pricePerUnit") or {}).get("USD")
                if amount is None:
                    continue
                label = bedrock_charge_label(
                    attributes, str(rate.get("description", ""))
                )
                yield {
                    "model": model,
                    "region": str(
                        attributes.get("location") or attributes.get("regionCode") or ""
                    ),
                    "region_code": str(attributes.get("regionCode") or ""),
                    "kind": bedrock_charge_kind(label),
                    "tier": bedrock_tier(label),
                    "label": label,
                    "amount": str(amount),
                    "unit": str(rate.get("unit") or ""),
                    "effective": str(term.get("effectiveDate") or ""),
                }


def bedrock_unit(unit: str) -> tuple[str, int]:
    """The unit phrase and token scale one published unit is billed at."""
    return BEDROCK_UNITS.get(unit.strip().lower(), (unit, 1))


def bedrock_price(rate: dict[str, Any]) -> dict[str, Any]:
    """One rate as a price, in the unit its own record states.

    A rate the list quotes per thousand tokens is restated per million, which is the
    unit the report compares token rates in, and it is labelled with that unit rather
    than the one it was scaled from: the amount and the unit beside it have to be the
    rate the reader is shown. What the vendor published stays in the label.
    """
    phrase, scale = bedrock_unit(rate["unit"])
    label = f"{rate['label']}（{rate['unit']}）"
    if scale != 1:
        return price_item(
            rate["kind"],
            label,
            per_million_tokens(rate["amount"], scale),
            "USD_per_million_tokens",
        )
    return price_item(
        rate["kind"],
        label,
        rate["amount"],
        price_unit_code(phrase, "", currency="USD", default="provider_defined"),
    )


class AWSBedrockAdapter(PriceSource):
    """Bedrock's own price lists, for first-party and Marketplace models alike."""

    provider_id = "aws-bedrock"
    provider_name = "AWS Bedrock"
    source_url = BEDROCK_MODELS_URL
    catalog_url = BEDROCK_PRICING_PAGE
    source_kind = "anonymous_api"
    currency = "USD"
    region = "全球"
    delivery_mode = "platform_hosted"

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._rates: list[dict[str, Any]] | None = None

    def _published(self) -> list[dict[str, Any]]:
        if self._rates is None:
            rates: list[dict[str, Any]] = []
            for url, offer, name_field in (
                (BEDROCK_MODELS_URL, "AmazonBedrock", "model"),
                (
                    BEDROCK_MARKETPLACE_URL,
                    "AmazonBedrockFoundationModels",
                    "servicename",
                ),
            ):
                rates.extend(
                    bedrock_rates(
                        bedrock_payload(self.document(url), offer),
                        name_field=name_field,
                    )
                )
            if not rates:
                raise SourceError("AWS Bedrock price list published no rates")
            self._rates = rates
        return self._rates

    def _by_model(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for rate in self._published():
            grouped.setdefault(normalize_model(rate["model"]), []).append(rate)
        return grouped

    def list_models(self, prefix: str = "") -> list[str]:
        key = normalize_model(prefix)
        return sorted(
            (model for model in self._by_model() if not key or model.startswith(key)),
            key=str.lower,
        )

    def query(self, model: str) -> list[dict[str, Any]]:
        rates = self._by_model().get(normalize_model(model))
        return [self._record_for(rates)] if rates else []

    def catalog_records(self) -> list[dict[str, Any]]:
        """Build every record from the two price lists already read, in one pass."""
        grouped = self._by_model()
        return [self._record_for(grouped[key]) for key in sorted(grouped)]

    def _record_for(self, rates: list[dict[str, Any]]) -> dict[str, Any]:
        """Keep each region's rates as that region's offer.

        A rate is published per region, so merging regions would quote one region's
        amount for another's; each offer carries the region it is billed in, both as
        the name it is read by and as the code the vendor prices it under.
        """
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for rate in rates:
            key = (rate["region"], rate["region_code"], rate["tier"])
            grouped.setdefault(key, []).append(rate)
        offers = []
        for region, region_code, tier in sorted(grouped):
            seen: dict[tuple[str, str, str, str], dict[str, Any]] = {}
            for row in grouped[(region, region_code, tier)]:
                # One charge can be published under several SKUs — a global and a
                # regional one, say. Stating it once is not losing a rate; stating
                # the same rate twice would read as two charges.
                key = (row["kind"], row["label"], row["unit"], row["amount"])
                seen.setdefault(key, bedrock_price(row))
            conditions: dict[str, Any] = {
                "billing_mode": "pay_as_you_go",
                "region_code": region_code,
            }
            if tier != BEDROCK_STANDARD_TIER:
                conditions["service_tier"] = tier
            offers.append(
                {
                    "name": tier if tier != BEDROCK_STANDARD_TIER else (region or "standard"),
                    "conditions": conditions,
                    "prices": list(seen.values()),
                }
            )
        display_name = rates[0]["model"]
        model_id = normalize_model(display_name)
        return make_record(
            self.provider_id,
            self.provider_name,
            model_id,
            display_name,
            self.region,
            offers,
            BEDROCK_PRICING_PAGE,
            self.source_kind,
            now_iso(),
            currency=self.currency,
            delivery_mode=self.delivery_mode,
            model_family=model_family(model_id),
        )
