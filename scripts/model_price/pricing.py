"""Price and record shapes shared by every adapter.

A price always keeps the provider's own billing conditions instead of merging
across regions, time bands, context tiers, or promotions.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .text import clean_text

MILLION = 1_000_000

# Vendors quote a price per thousand, per ten thousand, or per million tokens;
# every report compares one unit, so an amount is rescaled rather than left for
# the reader to convert. The scale is named in the vendor's own label
# ("元/千tokens"), tested most specific first so 百万 is not read as 万.
TOKEN_UNIT_SCALES = (("百万", MILLION), ("万", 10_000), ("千", 1_000))


def unit_code(label: str) -> str:
    """Map a vendor unit label onto a stable unit code."""
    normalized = label.lower().replace(" ", "")
    if "百万" in normalized or "1m" in normalized or "/m" in normalized:
        if "小时" in normalized:
            return "CNY_per_million_tokens_per_hour"
        return "CNY_per_million_tokens"
    if "万字符" in normalized:
        return "CNY_per_10k_characters"
    if "每次" in normalized or "/次" in normalized:
        return "CNY_per_request"
    return label or "provider_defined"


def price_item(
    kind: str,
    label: str,
    amount: str | None,
    unit: str,
    *,
    display: str | None = None,
    list_amount: str | None = None,
    discount: Any = None,
) -> dict[str, Any]:
    """Build one price entry with its original label and unit."""
    item: dict[str, Any] = {
        "type": kind,
        "label": label,
        "amount": amount,
        "unit": unit,
    }
    if display:
        item["display"] = display
    if list_amount is not None:
        item["list_amount"] = list_amount
    if discount is not None:
        item["discount"] = discount
    return item


def make_record(
    provider_id: str,
    provider_name: str,
    model_id: str,
    display_name: str,
    region: str,
    offers: list[dict[str, Any]],
    source_url: str,
    source_kind: str,
    retrieved_at: str,
    **extra: Any,
) -> dict[str, Any]:
    """Build the provider-agnostic record every adapter returns."""
    record = {
        "provider": {"id": provider_id, "name": provider_name},
        "model_id": model_id,
        "display_name": display_name,
        "region": region,
        "currency": "CNY",
        "offers": offers,
        "source": {
            "url": source_url,
            "kind": source_kind,
            "retrieved_at": retrieved_at,
        },
    }
    record.update(extra)
    return record


def tokens_per_price_unit(label: str) -> int | None:
    """Return how many tokens a vendor's price unit covers.

    ``None`` says the label does not price tokens at all, so a charge per page or
    per call ("元/页", "元/次") is never rescaled as if it were per token.
    """
    compact = clean_text(label).lower().replace(" ", "")
    if "token" not in compact:
        return None
    return next((count for word, count in TOKEN_UNIT_SCALES if word in compact), None)


def per_million_tokens(amount: str, tokens_per_unit: int) -> str:
    """Rescale an amount quoted per ``tokens_per_unit`` onto one million tokens.

    Decimal keeps the vendor's figure exact: a per-thousand rate of 0.00005 is
    0.05 per million, not the 0.05000000000000001 a binary float would carry into
    every comparison the report prints.
    """
    try:
        scaled = Decimal(amount) * MILLION / tokens_per_unit
    except (InvalidOperation, ZeroDivisionError):
        return amount
    return format(scaled.normalize(), "f")


def usd_amount(value: str) -> str | None:
    match = re.search(r"\$\s*(\d+(?:\.\d+)?)", value)
    return match.group(1) if match else None


def usd_price(kind: str, label: str, value: str) -> dict[str, Any] | None:
    amount = usd_amount(value)
    if not amount:
        return None
    return price_item(
        kind,
        label,
        amount,
        "USD_per_million_tokens",
        display=clean_text(value),
    )
