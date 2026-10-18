"""Compare a fresh scan against the baseline the previous scan left behind."""

from __future__ import annotations

from typing import Any

from .models import normalize_model
from .pricing import price_amount_line
from .snapshots import offer_identity, price_identity

# What one scan can conclude about one provider.
BASELINE_CREATED = "baseline_created"
BASELINE_NOT_FOUND = "baseline_not_found"
UNCHANGED = "unchanged"
CHANGED = "changed"
EMPTY_SCAN = "empty_scan"
SOURCE_ERROR = "source_error"

# Price movements remain distinct from whole-model and billing-mode changes.
PRICE_CHANGE_FIELD = "price_changes"

# What one price line did between two baselines.  Only ``amount_changed`` is a
# price movement; the other kinds are kept so the report can name a structural
# change without calling it a price move.
PRICE_AMOUNT_CHANGED = "amount_changed"
PRICE_UNIT_CHANGED = "unit_changed"
PRICE_LINE_ADDED = "line_added"
PRICE_LINE_REMOVED = "line_removed"
PRICE_CAPTION_CHANGED = "caption_changed"

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
    reports_price_movements: bool = True,
) -> dict[str, Any]:
    """Report how ``current`` differs from ``previous``.

    ``previous`` is ``None`` on a first run or when a requested historical point
    has no match. ``no_baseline_status`` preserves that distinction where the
    comparison result is created rather than patching the report afterward.

    ``reports_price_movements`` is the channel's own policy, passed in rather than
    read from a baseline: what a scan says about a channel is decided by what that
    channel publishes today, not by which archive it happened to be compared with.
    """
    changes = (
        empty_changes()
        if previous is None
        else model_changes(
            previous.get("models", {}), current.get("models", {}),
            reports_price_movements=reports_price_movements,
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
        "api_id_count": sum(
            len({model.get("model_id"), *(model.get("model_aliases") or [])})
            for model in current.get("models", {}).values()
        ),
        "changes": changes,
    }


def channel_status(report: dict[str, Any]) -> str:
    """Count partial failures honestly and include notice-only changes.

    A provider's price comparison status is one fact; its independent notice and
    release reads are others.  A reader asking "did this channel move?" needs the
    combined answer, which is why the summary and the message both use this rule
    rather than inventing two counts of the same scan.
    """
    if report["status"] in (EMPTY_SCAN, SOURCE_ERROR):
        return report["status"]
    independent = [report.get(key) or {} for key in ("lifecycle", "announcements")]
    if any(item.get("status") == SOURCE_ERROR for item in independent):
        return SOURCE_ERROR
    if any(item.get("changes") or item.get("observations") for item in independent):
        return CHANGED
    return report["status"]


def empty_changes() -> dict[str, Any]:
    return {
        **{field: [] for field in CHANGE_FIELDS},
        "amount_moves": 0,
        "unit_changes": 0,
        "price_items_added": 0,
        "price_items_removed": 0,
        "caption_changes": 0,
        "total": 0,
    }


def model_changes(
    before: dict[str, Any], after: dict[str, Any], *,
    reports_price_movements: bool = True,
) -> dict[str, Any]:
    """Diff two snapshots' model tables.

    A model that appeared or disappeared is recorded whole, offers and prices
    included: a report that names a new model without saying what it costs leaves
    the reader to go and look it up.

    A channel that reports no price movements is compared on its listing alone.
    Its offers and amounts are archived like any other channel's, but a movement
    between them is not a change this scan reports: an aggregator rebills whatever
    endpoint a request routes to, so those movements are routing noise rather than
    a price a reader can act on. Listing a model and withdrawing it stay changes.
    """
    changes = empty_changes()
    pairs, added_keys, removed_keys = _model_pairs(before, after)
    for key in added_keys:
        changes["models_added"].append(after[key])
    for key in removed_keys:
        changes["models_removed"].append(before[key])
    if reports_price_movements:
        for after_key in sorted(pairs):
            offer_changes(before[pairs[after_key]], after[after_key], changes)
    changes.update(price_change_counts(changes["price_changes"]))
    changes["total"] = (
        len(changes["models_added"])
        + len(changes["models_removed"])
        + len(changes["offers_added"])
        + len(changes["offers_removed"])
        + changes["amount_moves"]
        + changes["price_items_added"]
        + changes["price_items_removed"]
    )
    return changes


def _model_pairs(
    before: dict[str, Any], after: dict[str, Any]
) -> tuple[dict[str, str], list[str], list[str]]:
    """Pair before/after models, following aliases so a rename is not a removal.

    Old baselines predate ``model_aliases`` and can still carry a label whose
    trailing punctuation was cleaned later.  Both sides are matched by the same
    normalizer used when a snapshot is written, so a cleaned id pairs with its
    older spelling instead of looking like a model that disappeared.

    When Mistral's records move from one row per API id to one row per model, the
    canonical id pairs with the alias the previous baseline knew, and every other
    alias of that model is consumed rather than reported as a model that vanished.
    """
    before_by_key = {normalize_model(key): key for key in before}
    after_by_key = {normalize_model(key): key for key in after}
    pairs: dict[str, str] = {}
    consumed: set[str] = set()
    for key in sorted(set(before_by_key) & set(after_by_key)):
        after_key = after_by_key[key]
        before_key = before_by_key[key]
        pairs[after_key] = before_key
        consumed.add(before_key)
    after_alias: dict[str, str] = {}
    for key, model in after.items():
        for alias in model.get("model_aliases") or []:
            after_alias[normalize_model(alias)] = key
    for key in sorted(set(after) - set(pairs)):
        for alias in after[key].get("model_aliases") or []:
            before_key = before_by_key.get(normalize_model(alias))
            if before_key is None or before_key in consumed:
                continue
            pairs[key] = before_key
            consumed.add(before_key)
            break
    removed_keys: list[str] = []
    for key in sorted(set(before) - consumed):
        target = after_alias.get(normalize_model(key))
        if target in pairs:
            continue
        if target in after and target not in pairs:
            pairs[target] = key
            consumed.add(key)
            continue
        removed_keys.append(key)
    added_keys = [key for key in sorted(set(after) - set(pairs))]
    return pairs, added_keys, removed_keys


def offer_changes(
    before: dict[str, Any], after: dict[str, Any], changes: dict[str, Any],
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
            model, before_offers[key], after_offers[key], changes["price_changes"],
        )


def price_changes(
    model: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    changes: list[dict[str, Any]],
) -> None:
    """Record what one price line did, keeping a caption apart from an amount.

    A line present on both sides is compared by its billed ``(amount, unit)``.
    A line whose caption changed but whose type, amount, and unit did not is one
    ``caption_changed`` entry, not one removal plus one addition.  Only an amount
    change is a price movement; a unit correction and a line appearing or
    disappearing are named as the different facts they are.
    """
    shared = {
        **model,
        "offer": after.get("name", ""),
        "conditions": dict(after.get("conditions") or {}),
    }
    before_prices = {price_identity(price): price for price in before.get("prices", [])}
    after_prices = {price_identity(price): price for price in after.get("prices", [])}
    for key in sorted(set(before_prices) & set(after_prices)):
        was, now = before_prices[key], after_prices[key]
        if was.get("amount") != now.get("amount"):
            kind = PRICE_AMOUNT_CHANGED
        elif was.get("unit") != now.get("unit"):
            kind = PRICE_UNIT_CHANGED
        else:
            continue
        changes.append(
            {
                **shared,
                "kind": kind,
                **_price_brief(now),
                "from": price_amount_line(was),
                "to": price_amount_line(now),
            }
        )

    removed = {
        key: before_prices[key] for key in set(before_prices) - set(after_prices)
    }
    added = {
        key: after_prices[key] for key in set(after_prices) - set(before_prices)
    }
    # A caption change has the same billed value on both sides.  Pair those
    # before reporting the leftovers as a line appearing or disappearing.
    removed_by_value: dict[tuple[Any, Any, Any], list[tuple[str, str]]] = {}
    for key in sorted(removed):
        price = removed[key]
        removed_by_value.setdefault(_price_value(price), []).append(key)
    leftover_added: list[dict[str, Any]] = []
    for key in sorted(added):
        price = added[key]
        candidates = removed_by_value.get(_price_value(price))
        if not candidates:
            leftover_added.append(price)
            continue
        old_key = candidates.pop(0)
        old = removed.pop(old_key)
        changes.append(
            {
                **shared,
                "kind": PRICE_CAPTION_CHANGED,
                **_price_brief(price),
                "from_label": old.get("label"),
                "to_label": price.get("label"),
                "from": price_amount_line(old),
                "to": price_amount_line(price),
            }
        )
    for price in leftover_added:
        changes.append(
            {
                **shared,
                "kind": PRICE_LINE_ADDED,
                **_price_brief(price),
                "from": None,
                "to": price_amount_line(price),
            }
        )
    for key in sorted(removed):
        price = removed[key]
        changes.append(
            {
                **shared,
                "kind": PRICE_LINE_REMOVED,
                **_price_brief(price),
                "from": price_amount_line(price),
                "to": None,
            }
        )


def _price_value(price: dict[str, Any]) -> tuple[Any, Any, Any]:
    """The billed identity a caption is allowed to change around."""
    return price.get("type"), price.get("unit"), price.get("amount")


def price_change_counts(changes: list[dict[str, Any]]) -> dict[str, int]:
    """Count each price kind once, for both the total and the summary."""
    counts = {
        "amount_moves": 0,
        "unit_changes": 0,
        "price_items_added": 0,
        "price_items_removed": 0,
        "caption_changes": 0,
    }
    for change in changes:
        kind = change.get("kind")
        if kind == PRICE_AMOUNT_CHANGED:
            counts["amount_moves"] += 1
        elif kind == PRICE_UNIT_CHANGED:
            counts["unit_changes"] += 1
        elif kind == PRICE_LINE_ADDED:
            counts["price_items_added"] += 1
        elif kind == PRICE_LINE_REMOVED:
            counts["price_items_removed"] += 1
        elif kind == PRICE_CAPTION_CHANGED:
            counts["caption_changes"] += 1
    return counts


def _price_brief(price: dict[str, Any]) -> dict[str, Any]:
    return {"type": price.get("type"), "label": price.get("label")}


def _model_brief(model: dict[str, Any]) -> dict[str, Any]:
    brief = {
        "model_id": model.get("model_id", ""),
        "display_name": model.get("display_name", ""),
    }
    if aliases := model.get("model_aliases"):
        brief["model_aliases"] = list(aliases)
    # The vendor's own sentence about what the price is travels with the change, so
    # the scan can print it beside the model it explains rather than only in JSON.
    # It takes no part in deciding the change: it is the same sentence before and
    # after, and it is not a price.
    if notes := model.get("pricing_notes"):
        brief["pricing_notes"] = list(notes)
    return brief


def _offer_brief(offer: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": offer.get("name", ""),
        "conditions": dict(offer.get("conditions") or {}),
        "prices": list(offer.get("prices") or []),
    }
