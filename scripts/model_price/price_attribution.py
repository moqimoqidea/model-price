"""Compare endpoint evidence without transferring prices between hosting variants.

Catalogue amounts decide whether a movement exists. This optional evidence only
explains it: a pinned provider's rate, its discount, or the selected route changed.
Unknown history stays unknown rather than being reconstructed from today's hosts.
"""

from __future__ import annotations

from typing import Any

from .pricing import price_amount_line
from .snapshots import price_identity


def endpoint_identity(endpoint: dict[str, Any] | None) -> str | None:
    """The literal serving tag preserves variants independently of display names."""
    if endpoint and endpoint.get("provider_name") and endpoint.get("tag"):
        return str(endpoint["tag"])
    return None


def _endpoint_price(
    endpoint: dict[str, Any] | None, key: tuple[str, str]
) -> dict[str, Any] | None:
    return next(
        (
            price_amount_line(price)
            for price in (endpoint or {}).get("prices") or []
            if price_identity(price) == key
        ),
        None,
    )


def _reference_price(
    evidence: dict[str, Any], key: tuple[str, str]
) -> dict[str, Any] | None:
    return (
        _endpoint_price(evidence.get("reference_endpoint"), key)
        if evidence.get("reference_status") == "observed" else None
    )


def price_attribution(
    before: dict[str, Any],
    after: dict[str, Any],
    key: tuple[str, str],
    aggregated_pricing: bool,
) -> dict[str, Any]:
    """Explain a billed movement without adding terms to comparison identity.

    Only two observations of the same pinned endpoint can establish its base-rate
    adjustment. Comparing different hosts' reconstructed rates would recreate the
    very routing noise this evidence is meant to explain. Several simultaneous
    observed causes remain visible; an absent observation establishes no cause.
    """
    if not aggregated_pricing:
        return {}
    was = before.get("pricing_attribution") or {}
    now = after.get("pricing_attribution") or {}
    old_reference = was.get("reference_endpoint")
    new_reference = now.get("reference_endpoint")
    old_price, new_price = _reference_price(was, key), _reference_price(now, key)
    same_reference = (
        endpoint_identity(old_reference) is not None
        and endpoint_identity(old_reference) == endpoint_identity(new_reference)
        and old_price is not None and new_price is not None
        and old_price.get("unit") == new_price.get("unit")
    )
    causes = []
    reference_unchanged = None
    if (
        same_reference
        and old_price.get("list_amount") is not None
        and new_price.get("list_amount") is not None
    ):
        reference_unchanged = old_price["list_amount"] == new_price["list_amount"]
        if not reference_unchanged:
            causes.append("price_adjustment")
        if (
            old_price.get("discount") is not None
            and new_price.get("discount") is not None
            and old_price["discount"] != new_price["discount"]
        ):
            causes.append("promotion_change")
    old_selected = was.get("selected_endpoint")
    new_selected = now.get("selected_endpoint")
    if (
        endpoint_identity(old_selected) is not None
        and endpoint_identity(new_selected) is not None
    ):
        if endpoint_identity(old_selected) != endpoint_identity(new_selected):
            causes.append("provider_switch")
        else:
            old_discount = (_endpoint_price(old_selected, key) or {}).get("discount")
            new_discount = (_endpoint_price(new_selected, key) or {}).get("discount")
            if (
                old_discount is not None and new_discount is not None
                and old_discount != new_discount and "promotion_change" not in causes
            ):
                causes.append("promotion_change")
    if not causes:
        causes.append("catalog_price_fluctuation")
    current_price = next(
        (price for price in after.get("prices") or [] if price_identity(price) == key),
        {},
    )
    return {
        "cause": causes[0],
        "causes": causes,
        "provider_name": (new_selected or {}).get("provider_name"),
        "discount": current_price.get("discount"),
        "list_amount": current_price.get("list_amount"),
        "endpoint_discount": ((new_selected or {}).get("pricing") or {}).get("discount"),
        "pricing_attribution": {
            "status": now.get("status", "not_checked"),
            "source": now.get("source"),
            "error": now.get("error"),
            "observed_at": now.get("observed_at"),
            "reference_provider_name": (new_reference or {}).get("provider_name"),
            "reference_tag": (new_reference or {}).get("tag"),
            "reference_status": now.get("reference_status", "not_checked"),
            "reference_from": old_price,
            "reference_to": new_price,
            "reference_baseline_at": was.get("reference_observed_at"),
            "reference_observed_at": now.get("reference_observed_at"),
            "reference_unchanged": reference_unchanged,
        },
    }
