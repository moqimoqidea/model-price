"""Compare a fresh scan against the baseline the previous scan left behind."""

from __future__ import annotations

from typing import Any

from .snapshots import offer_identity, price_identity

# What one scan can conclude about one provider.
BASELINE_CREATED = "baseline_created"
BASELINE_NOT_FOUND = "baseline_not_found"
UNCHANGED = "unchanged"
CHANGED = "changed"

# The one change a report lays out as a table; the rest read as bullet lists.
PRICE_CHANGE_FIELD = "price_changes"

CHANGE_FIELDS = (
    "models_added",
    "models_removed",
    "offers_added",
    "offers_removed",
    PRICE_CHANGE_FIELD,
)


def compare_snapshots(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    no_baseline_status: str = BASELINE_CREATED,
) -> dict[str, Any]:
    """Report how ``current`` differs from ``previous``.

    ``previous`` is ``None`` on a first run or when a requested historical point
    has no match. ``no_baseline_status`` preserves that distinction where the
    comparison result is created rather than patching the report afterward.
    """
    changes = (
        empty_changes()
        if previous is None
        else model_changes(
            previous.get("models", {}), current.get("models", {})
        )
    )
    status = UNCHANGED
    if previous is None:
        status = no_baseline_status
    elif changes["total"]:
        status = CHANGED
    return {
        "provider": current["provider"],
        "status": status,
        "baseline_at": previous.get("captured_at") if previous else None,
        "captured_at": current["captured_at"],
        "source": current["source"],
        "model_count": len(current.get("models", {})),
        "changes": changes,
    }


def empty_changes() -> dict[str, Any]:
    return {**{field: [] for field in CHANGE_FIELDS}, "total": 0}


def model_changes(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    """Diff two snapshots' model tables.

    A model that appeared or disappeared is recorded whole, offers and prices
    included: a report that names a new model without saying what it costs leaves
    the reader to go and look it up.
    """
    changes = empty_changes()
    for key in sorted(set(after) - set(before)):
        changes["models_added"].append(after[key])
    for key in sorted(set(before) - set(after)):
        changes["models_removed"].append(before[key])
    for key in sorted(set(before) & set(after)):
        offer_changes(before[key], after[key], changes)
    changes["total"] = sum(len(changes[field]) for field in CHANGE_FIELDS)
    return changes


def offer_changes(
    before: dict[str, Any], after: dict[str, Any], changes: dict[str, Any]
) -> None:
    """Diff one model's offers, then the prices inside the offers both scans have."""
    model = _model_brief(after)
    before_offers = {offer_identity(offer): offer for offer in before.get("offers", [])}
    after_offers = {offer_identity(offer): offer for offer in after.get("offers", [])}
    for key in sorted(set(after_offers) - set(before_offers)):
        changes["offers_added"].append(
            {**model, "offer": _offer_brief(after_offers[key])}
        )
    for key in sorted(set(before_offers) - set(after_offers)):
        changes["offers_removed"].append(
            {**model, "offer": _offer_brief(before_offers[key])}
        )
    for key in sorted(set(before_offers) & set(after_offers)):
        price_changes(
            model, before_offers[key], after_offers[key], changes["price_changes"]
        )


def price_changes(
    model: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    changes: list[dict[str, Any]],
) -> None:
    """Record a price's appearance, disappearance, or new amount.

    Only the amount and its unit decide a change; a caption that merely moved
    between columns is not a price movement.
    """
    shared = {
        **model,
        "offer": after.get("name", ""),
        "conditions": dict(after.get("conditions") or {}),
    }
    before_prices = {price_identity(price): price for price in before.get("prices", [])}
    after_prices = {price_identity(price): price for price in after.get("prices", [])}
    for key in sorted(set(after_prices) - set(before_prices)):
        changes.append(
            {**shared, **_price_brief(after_prices[key]), "from": None, "to": _amount(after_prices[key])}
        )
    for key in sorted(set(before_prices) - set(after_prices)):
        changes.append(
            {**shared, **_price_brief(before_prices[key]), "from": _amount(before_prices[key]), "to": None}
        )
    for key in sorted(set(before_prices) & set(after_prices)):
        was, now = before_prices[key], after_prices[key]
        if _amount(was) != _amount(now):
            changes.append(
                {**shared, **_price_brief(now), "from": _amount(was), "to": _amount(now)}
            )


def _amount(price: dict[str, Any]) -> dict[str, Any]:
    return {"amount": price.get("amount"), "unit": price.get("unit")}


def _price_brief(price: dict[str, Any]) -> dict[str, Any]:
    return {"type": price.get("type"), "label": price.get("label")}


def _model_brief(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_id": model.get("model_id", ""),
        "display_name": model.get("display_name", ""),
    }


def _offer_brief(offer: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": offer.get("name", ""),
        "conditions": dict(offer.get("conditions") or {}),
    }
