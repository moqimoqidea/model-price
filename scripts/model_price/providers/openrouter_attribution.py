"""Keep a stable endpoint reference without mistaking routing for model repricing.

The models API remains the catalogue of billed rates. Only a model whose prices
moved gets an endpoint read; matching the complete published rate vector avoids
inventing a cheapest-provider route. A reference is pinned to the first uniquely
matched endpoint and never replaced merely because another host became cheaper.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Callable
from urllib.parse import quote

from ..core import PriceSource
from ..diffing import model_changes
from ..errors import SourceError
from ..models import normalize_model
from ..price_attribution import endpoint_identity
from ..pricing import PRICE_ATTRIBUTION_FIELDS, PRICE_TERM_FIELDS, billed_amount
from ..snapshots import offer_identity, price_identity

OPENROUTER_ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{model_id}/endpoints"
RateReader = Callable[[dict[str, Any]], dict[str, dict[str, Any]]]


def endpoint_prices(
    endpoint: dict[str, Any], rate_reader: RateReader
) -> list[dict[str, Any]]:
    """Normalize the endpoint's discount-off fraction to the shared paid multiplier.

    OpenRouter's public OpenAPI defines discount as a reduction of this endpoint's
    price, not a reduction against the model author's price. Recovering its own
    pre-discount rate follows that explicit rule and is labelled as calculated.
    A missing, invalid, or full discount cannot establish a pre-discount price.
    """
    prices = list(rate_reader(endpoint).values())
    try:
        discount = Decimal(str(endpoint["pricing"]["discount"]))
        if not discount.is_finite() or not 0 <= discount <= 1:
            raise ValueError("invalid endpoint discount")
    except (KeyError, InvalidOperation, TypeError, ValueError):
        discount = None
    for price in prices:
        price["provider_name"] = endpoint["provider_name"]
        price["provider_tag"] = endpoint.get("tag")
        if discount is None:
            continue
        multiplier = Decimal(1) - discount
        price["discount"] = format(multiplier.normalize(), "f")
        if multiplier > 0 and price.get("amount") is not None:
            price["list_amount"] = format(
                (Decimal(price["amount"]) / multiplier).normalize(), "f"
            )
            price["list_amount_basis"] = "endpoint_discount"
    return prices


def read_endpoints(
    document: str, model_id: str, rate_reader: RateReader
) -> list[dict[str, Any]]:
    """Read anonymous endpoint evidence only for the requested literal model."""
    try:
        payload = json.loads(document)
    except json.JSONDecodeError as exc:
        raise SourceError("OpenRouter endpoints were not JSON") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("endpoints"), list):
        raise SourceError("unexpected OpenRouter endpoints shape")
    if normalize_model(str(data.get("id", ""))) != normalize_model(model_id):
        raise SourceError("OpenRouter endpoints named a different model")
    endpoints = []
    for entry in data["endpoints"]:
        if not isinstance(entry, dict) or not entry.get("provider_name"):
            continue
        if not isinstance(entry.get("pricing"), dict):
            continue
        if (
            entry.get("model_id")
            and normalize_model(str(entry["model_id"])) != normalize_model(model_id)
        ):
            continue
        endpoint = {
            field: copy.deepcopy(entry[field])
            for field in (
                "provider_name", "tag", "name", "model_id", "context_length",
                "status", "quantization", "pricing",
            )
            if field in entry
        }
        try:
            endpoint["prices"] = endpoint_prices(endpoint, rate_reader)
        except (InvalidOperation, ValueError, TypeError):
            # One malformed endpoint is unusable evidence, not a broken catalogue.
            continue
        endpoints.append(endpoint)
    return endpoints


def _matches(offer: dict[str, Any], endpoint: dict[str, Any]) -> bool:
    """Require one available endpoint to match all charges, never just input price."""
    if endpoint.get("status") != 0:
        return False
    prices = {price_identity(price): price for price in endpoint["prices"]}
    compared = False
    for price in offer.get("prices") or []:
        found = prices.get(price_identity(price))
        # The catalogue includes zero placeholders absent from some endpoints.
        # An absent field cannot supply terms for that zero, but it does not name
        # a different route unless the endpoint actually quotes another amount.
        if found is None:
            if price.get("amount") != "0":
                return False
            continue
        if billed_amount(price) != billed_amount(found):
            return False
        compared = True
    return compared


def _standing_offer(model: dict[str, Any]) -> dict[str, Any] | None:
    """Endpoint default rates cannot explain batch, video, context, or time tiers."""
    return next(
        (
            offer for offer in model.get("offers") or []
            if offer.get("name") == "pay_as_you_go"
            and not any(
                key in (offer.get("conditions") or {})
                for key in ("service_tier", "output_modality", "context_tier", "time_band")
            )
        ),
        None,
    )


def _same_prices(before: dict[str, Any], after: dict[str, Any]) -> bool:
    def signature(offer: dict[str, Any]) -> dict[tuple[str, str], tuple[Any, Any]]:
        return {
            price_identity(price): billed_amount(price)
            for price in offer.get("prices") or []
        }
    return signature(before) == signature(after)


def _apply_terms(offer: dict[str, Any], endpoint: dict[str, Any] | None) -> None:
    """Attach terms only to a charge whose exact amount that endpoint published."""
    if not endpoint:
        return
    prices = {price_identity(price): price for price in endpoint["prices"]}
    for price in offer["prices"]:
        found = prices.get(price_identity(price))
        if found and billed_amount(price) == billed_amount(found):
            for field in (*PRICE_TERM_FIELDS, *PRICE_ATTRIBUTION_FIELDS):
                if found.get(field) is not None:
                    price[field] = found[field]


def _observe(
    source: PriceSource,
    model_id: str,
    offer: dict[str, Any],
    retained: dict[str, Any],
    captured_at: str,
    rate_reader: RateReader,
) -> dict[str, Any]:
    url = OPENROUTER_ENDPOINTS_URL.format(model_id=quote(model_id, safe="/"))
    evidence: dict[str, Any] = {
        "source": {"url": url, "kind": "anonymous_api"},
        "observed_at": captured_at,
        "selected_endpoint": None,
        "reference_endpoint": retained.get("reference_endpoint"),
        "reference_observed_at": retained.get("reference_observed_at"),
        "reference_status": "not_found",
    }
    try:
        endpoints = read_endpoints(source.document(url), model_id, rate_reader)
    except Exception as exc:
        return {
            **evidence, "status": "source_error", "error": str(exc),
            "reference_status": "source_error",
        }
    evidence["endpoints"] = endpoints
    matches = [endpoint for endpoint in endpoints if _matches(offer, endpoint)]
    evidence["status"] = (
        "matched" if len(matches) == 1 else "ambiguous" if matches else "not_found"
    )
    selected = matches[0] if len(matches) == 1 else None
    evidence["selected_endpoint"] = selected
    pinned = endpoint_identity(retained.get("reference_endpoint"))
    if pinned is None:
        pinned = endpoint_identity(selected)
    references = [
        endpoint for endpoint in endpoints
        if pinned is not None and endpoint_identity(endpoint) == pinned
    ]
    if len(references) == 1:
        reference = references[0]
        evidence["reference_endpoint"] = reference
        evidence["reference_observed_at"] = captured_at
        evidence["reference_status"] = (
            "observed" if reference.get("status") == 0 else "unavailable"
        )
    return evidence


def enrich_openrouter_snapshot(
    source: PriceSource,
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    latest: dict[str, Any] | None,
    rate_reader: RateReader,
) -> None:
    """Observe changed models and carry dated references through unchanged scans.

    Historical comparisons use their selected archive for attribution, but the
    latest archive owns the pinned reference. Both comparisons can require a fresh
    read; their model set is deduplicated before making any endpoint requests.
    """
    models = current["models"]
    targets: set[str] = set()
    for baseline in (previous, latest):
        if baseline is not None:
            targets.update(
                normalize_model(move["model_id"])
                for move in model_changes(baseline.get("models", {}), models)["price_changes"]
            )
    for key, model in models.items():
        offer = _standing_offer(model)
        if offer is None:
            continue
        old_offer = None
        for baseline in (latest, previous):
            candidate = _standing_offer((baseline or {}).get("models", {}).get(key, {}))
            if candidate and candidate.get("pricing_attribution"):
                old_offer = candidate
                break
        retained = copy.deepcopy((old_offer or {}).get("pricing_attribution") or {})
        if key in targets:
            evidence = _observe(
                source, model["model_id"], offer, retained,
                current["captured_at"], rate_reader,
            )
            offer["pricing_attribution"] = evidence
            _apply_terms(offer, evidence["selected_endpoint"])
        elif retained:
            # Retention keeps the original observation time. It is a baseline
            # reference, never a claim that endpoints were checked again today.
            if (
                old_offer and offer_identity(old_offer) == offer_identity(offer)
                and _same_prices(old_offer, offer)
            ):
                offer["pricing_attribution"] = retained
                _apply_terms(offer, retained.get("selected_endpoint"))
