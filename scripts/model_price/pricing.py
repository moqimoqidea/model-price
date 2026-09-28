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

# A vendor that charges nothing says so in words instead of printing a zero. That
# statement is still the model's price — a free model costs nothing — so it is
# recorded as an amount rather than dropped. Dropping it would leave the model
# indistinguishable from one whose price has not been published yet, and the
# promotion ending would be invisible.
FREE_AMOUNT = "0"

# The whole cell has to be the statement. A cell that names a free allowance
# beside a rate ("首张免费 / 第 2 张起：0.02", "1 GB 内存储免费") prices something
# else and is read on its own terms.
FREE_STATEMENT_RE = re.compile(
    r"^(?:免费|限时免费|不收费|free|free of charge|no charge)[.。!！]?$", re.I
)


def is_free_statement(value: str) -> bool:
    """Say whether a cell states a charge of nothing, and states nothing else."""
    return bool(FREE_STATEMENT_RE.match(clean_text(value)))


def is_free_amount(value: Any) -> bool:
    """Identify a price of nothing, however the vendor's zero was formatted."""
    if value is None:
        return False
    try:
        return Decimal(str(value)) == 0
    except (InvalidOperation, ValueError):
        return False


# A catalogue can publish a base service beside faster and discounted variants.
# The order in the source is useful, but snapshots have to be deterministic, so
# their ordering needs an explicit business meaning instead of an alphabetical
# accident (``batch`` sorts before ``standard``).
PRIMARY_OFFER_NAMES = frozenset(
    {
        "standard",
        "online_standard",
        "pay_as_you_go",
        "text_api_pricing",
        "在线推理",
        "模型国内定价",
        "标准",
    }
)
PREMIUM_OFFER_NAMES = frozenset({"fast", "priority", "online_low_latency"})
FLEX_OFFER_NAMES = frozenset({"flex"})
DISCOUNT_OFFER_NAMES = frozenset({"batch", "批量推理"})

# HTML footnote contents are annotations, not part of an amount's display text.
# Removing only the tags would turn ``$0.20 / MTok<sup>2</sup>`` into an apparent
# price of ``$0.20 / MTok 2``.
SUPERSCRIPT_RE = re.compile(r"<sup\b[^>]*>.*?</sup\s*>", re.I | re.S)

# Price kinds are ordered the way a bill is read: request input, cache activity,
# then output. Cache writes retain the duration order Anthropic publishes.
PRICE_TYPE_ORDER = {
    "input": (0, 0),
    "cache_write_5m": (1, 0),
    "cache_write_1h": (1, 1),
    "cache_write": (1, 2),
    "cache_hit": (1, 3),
    "cache_read_explicit": (1, 4),
    "audio_cache_hit": (1, 5),
    "cache_storage": (1, 6),
    "output": (2, 0),
}

# Vendors quote a price per thousand, per ten thousand, or per million tokens;
# every report compares one unit, so an amount is rescaled rather than left for
# the reader to convert. The scale is named in the vendor's own label
# ("元/千tokens"), tested most specific first so 百万 is not read as 万.
TOKEN_UNIT_SCALES = (("百万", MILLION), ("万", 10_000), ("千", 1_000))

# What a charge is billed against, read off the vendor's own wording. A unit is
# what a price is compared and archived by, so two spellings of one unit have to
# produce one code: a vendor rewording 每张 as 元/张 is not a price movement.
#
# A measure is recognised in the phrase the vendor published, most specific
# first — "百万 token/小时" is storage rather than a token rate, "万字符" is not a
# character rate, and "1k requests" is not a request rate. The phrase is reduced
# to one shape before it is matched: 每 and "per" both become "/", and spaces and
# separators come out. A measure a vendor names by quantity ("百万 token",
# "1M tokens") is therefore matched without the separator, while one it counts in
# ("元/秒", "每张") keeps it — "秒" alone would also match a duration column.
UNIT_MEASURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "million_tokens_per_hour",
        (
            "百万token/小时", "百万tokens/小时", "1mtoken/hour",
            "1mtokens/hour", "mtokens/hour", "mtok/hour",
        ),
    ),
    (
        "million_tokens",
        (
            "百万token", "1mtoken", "1mtokens", "mtoken", "mtok",
            "milliontokens",
        ),
    ),
    ("thousand_tokens", ("千token", "1ktoken", "1ktokens", "thousandtokens")),
    ("10k_tokens", ("万token", "10ktoken", "10ktokens")),
    (
        "million_characters",
        ("百万字符", "1mcharacter", "1mcharacters", "1mchar", "1mchars"),
    ),
    ("10k_characters", ("万字符", "10kcharacter", "10kcharacters")),
    ("thousand_characters", ("千字符", "1kcharacter", "1kcharacters")),
    ("character", ("字符", "character", "characters", "char", "chars")),
    ("image", ("/张", "/幅", "/image", "/images", "/图")),
    ("frame", ("/帧", "/frame", "/frames")),
    ("second", ("/秒", "/second", "/seconds", "/sec", "/secs")),
    ("minute", ("/分钟", "/minute", "/minutes", "/min", "/mins")),
    ("hour", ("/小时", "/hour", "/hours", "/hr", "/hrs")),
    (
        "thousand_requests",
        (
            "/千次", "/1000次", "/1krequest", "/1krequests", "/1kcall",
            "/1kcalls", "/1kprompt", "/1kprompts", "/1kquery", "/1kqueries",
            "/1000request", "/1000requests", "/1000call", "/1000calls",
        ),
    ),
    ("10k_requests", ("/万次", "/10000次", "/10krequest", "/10krequests")),
    (
        "request",
        (
            "/次", "/request", "/requests", "/call", "/calls",
            "/prompt", "/prompts", "/query", "/queries", "/search",
        ),
    ),
    ("video", ("/视频", "/video", "/videos")),
    ("item", ("/个", "/item", "/items")),
    ("song", ("/首", "/song", "/songs")),
    ("page", ("/页", "/page", "/pages")),
)

_UNIT_SEPARATOR = "_per_"

# A vendor may bill in its own credit rather than in money ("480p：2 积分/次").
# The amount is still that model's price, and the credit is the unit it is in, so
# it keeps the vendor's own wording instead of being coded as a currency it is
# not — converting it would print a rate the page never published.
CREDIT_WORD_PATTERN = r"积分|credits?"
CREDIT_WORDS = ("积分", "credit")


def is_credit_unit(value: Any) -> bool:
    """Say whether a unit phrase is the vendor's own credit rather than money."""
    lowered = clean_text(str(value or "")).lower()
    return any(word in lowered for word in CREDIT_WORDS)


# A price per month or per year is a commitment rather than a rate for using a
# model: reserved throughput and capacity are sold that way, and a unit naming a
# period prices how long a subscription runs, not what one request costs.
COMMITMENT_MARKERS = ("月", "年", "month", "year", "annum")


def is_commitment_unit(value: Any) -> bool:
    """Say whether a unit phrase prices a commitment rather than a use."""
    lowered = clean_text(str(value or "")).lower()
    return any(marker in lowered for marker in COMMITMENT_MARKERS)


def unit_measure(label: Any) -> str | None:
    """Return what a vendor's unit phrase bills against, or ``None``.

    ``None`` says the label names no unit this tool can read — an internal credit
    ("积分"), an amount of storage, or a wording not yet published. The label is
    then kept as written rather than mapped onto a unit the vendor never used.
    """
    value = clean_text(str(label or "")).lower()
    value = re.sub(r"\bper\b", "/", value)
    value = value.replace("每", "/").replace("／", "/")
    value = re.sub(r"[\s,，]", "", value)
    if is_commitment_unit(value):
        return None
    for measure, markers in UNIT_MEASURES:
        if any(marker in value for marker in markers):
            return measure
    return None


def unit_code(label: Any, currency: str = "CNY") -> str:
    """Map a vendor unit phrase onto the stable code a price is archived by."""
    written = clean_text(str(label or ""))
    if is_credit_unit(written):
        return written or "provider_defined"
    measure = unit_measure(written)
    if measure:
        return f"{currency}{_UNIT_SEPARATOR}{measure}"
    return written or "provider_defined"


def unit_parts(code: Any) -> tuple[str, str] | None:
    """Split a unit code back into its currency and its measure.

    The wording those two are written in belongs to whoever prints a price; this
    only says how the code is built, so the parser knows a code when it sees one
    — including a vendor label that happens to contain the separator.
    """
    currency, separator, measure = str(code or "").partition(_UNIT_SEPARATOR)
    if not separator or not currency or not measure:
        return None
    return currency, measure


def _billing_key(value: Any) -> str:
    """Normalize a billing label for policy lookup, not model identity."""
    return clean_text(str(value or "")).lower().replace("-", "_").replace(" ", "_")


def offer_priority(offer: dict[str, Any]) -> int:
    """Rank a model's base offer ahead of premium and discounted alternatives.

    A missing ``service_tier`` means the vendor did not present the row as an
    alternative service level. Explicit names still win: a Chinese batch row is
    discounted even when its adapter has no separate service-tier condition.
    """
    conditions = offer.get("conditions") or {}
    tier = _billing_key(conditions.get("service_tier"))
    names = {_billing_key(offer.get("name")), tier}
    names.discard("")
    if names & PRIMARY_OFFER_NAMES:
        return 0
    if names & PREMIUM_OFFER_NAMES:
        return 1
    if names & FLEX_OFFER_NAMES:
        return 2
    if names & DISCOUNT_OFFER_NAMES:
        return 3
    return 0 if not tier else 1


def is_standard_offer(offer: dict[str, Any]) -> bool:
    """Select the base offer for the scan's default price presentation."""
    return offer_priority(offer) == 0


def primary_offer(offers: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose the offer a short model digest should show, independent of order."""
    if not offers:
        return None
    return min(
        enumerate(offers),
        key=lambda item: (offer_priority(item[1]), item[0]),
    )[1]


def price_sort_key(price: dict[str, Any]) -> tuple[int, int, str, str]:
    """Return a stable input-to-cache-to-output ordering for a price list."""
    kind = _billing_key(price.get("type"))
    order = PRICE_TYPE_ORDER.get(kind)
    if order is None:
        if kind.startswith("input") or kind.endswith("_input"):
            order = (0, 99)
        elif "cache" in kind:
            order = (1, 99)
        elif kind.startswith("output") or kind.endswith("_output"):
            order = (2, 99)
        else:
            order = (3, 99)
    return (*order, kind, str(price.get("label", "")))


# What a vendor publishes beside an amount, saying what the amount is besides how
# much it is: the rate it reduces, the multiplier it reduces it by, and the last day
# it applies. All three are shown with the amount and archived with it, so a rate
# that is limited in time or in scope is never read as the model's ordinary price.
# They sit apart from an offer's ``conditions``, which say how a charge is billed
# rather than what one amount is against another.
PRICE_TERM_FIELDS = ("list_amount", "discount", "effective_until")

# What a vendor writes when it prints a reduction as a 折 rather than as a rate.
# Chinese counts these in tenths and writes a decimal one either way: 7.5折 and
# 75折 are both three quarters, 8折 is four fifths, and 10折 is no reduction at
# all. Reading 75折 as 7.5 would overstate the charge tenfold.
FOLDS_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*折")
# The same reduction with the word the vendor introduced it by ("限时75折"),
# which is wording rather than an amount and comes off with it.
FOLDS_PHRASE_RE = re.compile(r"[^\s，,。;；、`]{0,4}(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*折")


def discount_multiplier(value: str) -> str | None:
    """Read the multiplier a vendor's own 折 wording states, or ``None``."""
    match = FOLDS_RE.search(clean_text(value))
    if not match:
        return None
    written = match.group(1)
    if "." in written or len(written) == 1:
        # 7.5折 and 8折 both count tenths of the standing rate.
        multiplier = Decimal(written) / 10
    else:
        # A whole number of two or more digits counts hundredths: 85折 is 0.85 and
        # 75折 is 0.75. Ten of them is the standing rate itself, undiscounted.
        whole = Decimal(written)
        multiplier = Decimal(1) if whole == 10 else whole / 100
    if multiplier <= 0 or multiplier > 1:
        return None
    return format(multiplier.normalize(), "f")


def without_discount_terms(value: str) -> str:
    """Remove a promotion's own wording, leaving the amounts beside it.

    A cell that publishes a rate as "原价 37.00`限时75折`" states two things: the
    rate and how far it is reduced. Reading the rate needs the second one out of
    the way, and the multiplier is kept separately where it is read.
    """
    return clean_text(FOLDS_PHRASE_RE.sub(" ", value)).replace("`", " ")


def discounted_amount(
    amount: Any, discount: Any
) -> tuple[str | None, str | None]:
    """Return the amount a multiplier bills and the amount it reduces.

    A vendor quotes ``Discount`` as the multiplier a promotion applies, so ``1``
    is no promotion at all. A multiplier of ``0`` is not a price of nothing — no
    vendor discounts a paid model to zero — and is read as the field being unset,
    so an untouched rate is never silently deleted.
    """
    if amount is None:
        return None, None
    listed = str(amount)
    if discount is None:
        return listed, None
    try:
        ratio = Decimal(str(discount))
        if ratio == 1 or ratio == 0:
            return listed, None
        current = Decimal(listed) * ratio
    except InvalidOperation:
        return listed, None
    return format(current.normalize(), "f"), listed



def price_item(
    kind: str,
    label: str,
    amount: str | None,
    unit: str,
    *,
    display: str | None = None,
    list_amount: str | None = None,
    discount: Any = None,
    effective_until: str | None = None,
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
    if effective_until is not None:
        item["effective_until"] = effective_until
    return item


def free_price(
    kind: str, label: str, unit: str, *, display: str | None = None
) -> dict[str, Any]:
    """Build the price of a charge a vendor publishes as free.

    The unit stays the one the vendor would bill in, and ``display`` keeps the
    vendor's own wording ("免费", "限时免费", "Free of charge") so a promotion that
    ends is readable in the report that records the change.
    """
    return price_item(kind, label, FREE_AMOUNT, unit, display=display)


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
    """Read one USD price cell, whether it quotes an amount or a free charge."""
    if is_free_statement(value):
        return free_price(
            kind, label, "USD_per_million_tokens", display=clean_text(value)
        )
    amount = usd_amount(value)
    if not amount:
        return None
    return price_item(
        kind,
        label,
        amount,
        "USD_per_million_tokens",
        display=clean_text(SUPERSCRIPT_RE.sub("", value)),
    )
