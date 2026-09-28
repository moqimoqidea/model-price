"""OpenRouter's aggregated catalogue, read from its own machine-readable API.

OpenRouter resells other vendors' models and publishes the machine-readable
catalogue this tool prefers: the models API for text, image, embedding, speech,
transcription, rerank, and decision models, and the video API for the per-second
rates the models API reports as zero. One document each, read once per run.

Two rate shapes reach this adapter that no price table has. ``pricing.overrides``
states a rate that applies only above a prompt-token threshold, or only inside a
daily window, while the top-level prices are the default-condition rates — so both
are kept as offers of their own rather than dropped. And a catalogue entry whose
every published rate is zero bills nothing at all: OpenRouter publishes that for a
model it is testing and for a free variant alike, so the entry keeps its place in
the catalogue with that state named and no price recorded, rather than a price of
zero that would read as a movement the day the real rate is published.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from ..core import PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, normalize_model
from ..pricing import is_free_amount, make_record, per_million_tokens, price_item, unit_code

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models?output_modalities=all"
OPENROUTER_VIDEOS_URL = "https://openrouter.ai/api/v1/videos/models"
OPENROUTER_SITE_URL = "https://openrouter.ai/models"

# The suffix that names a billing mode of one model rather than another catalogue
# entry: OpenRouter files the batch tier under its own id, and its own
# ``canonical_slug`` is the base model's, so those rates belong on the model they
# are a tier of. ``:free`` is not one of these — the API and the vendor's own
# display name both publish it as a catalogue entry of its own.
BATCH_SUFFIX = ":batch"

# What each key of the API's ``pricing`` object charges, with the vendor's own
# field name as the label so a report reads against the JSON it came from. Every
# field is a rate per token except ``web_search``, a charge per search, and
# ``image``, whose meaning is decided by whether ``image_token`` is published too.
#
# The API's field comments call ``image`` and ``image_output`` a cost "per image",
# but its own model pages publish them multiplied by a million and labelled
# ``/M tokens``: a picture is billed by the image tokens it costs. A model that
# publishes ``image_token`` beside ``image`` is the exception — there the two
# answer different questions (0.01 per picture beside 0.0000096 per image token),
# and the picture rate is what ``image`` is.
OPENROUTER_TOKEN_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("prompt", "input", "输入"),
    ("input_cache_read", "cache_hit", "缓存读取"),
    ("input_cache_write", "cache_write", "缓存写入"),
    ("input_cache_write_1h", "cache_write_1h", "缓存写入（1 小时）"),
    ("input_audio_cache", "audio_cache_hit", "音频缓存读取"),
    ("internal_reasoning", "internal_reasoning", "内部推理"),
    ("completion", "output", "输出"),
    ("image_token", "input_image", "图像输入"),
    ("image_output", "output_image", "图像输出"),
    ("audio", "input_audio", "音频输入"),
    ("audio_output", "output_audio", "音频输出"),
)
OPENROUTER_IMAGE_FIELD = ("image", "input_image", "图像输入（按张）")
OPENROUTER_REQUEST_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("web_search", "web_search", "联网搜索"),
)

# The two keys of the pricing object that publish no rate of their own.
OPENROUTER_NON_RATE_FIELDS = frozenset({"overrides", "discount"})

# What the video API's SKU keys bill, longest prefix first. A key is the quantity
# followed by the choices it is billed under ("duration_seconds_1080p"), and those
# choices stay with the price as its scope. A ``cents_`` figure is published in
# cents, so its factor converts it into the dollars every other amount is in — a
# unit conversion rather than a rate derived from two of them.
OPENROUTER_VIDEO_SKUS: tuple[tuple[str, str, str, str], ...] = (
    # (SKU prefix, price type, factor applied to the figure, unit phrase)
    (
        "cents_per_second_video_continuation",
        "output_video_continuation",
        "0.01",
        "/second",
    ),
    ("cents_per_second_output", "output_video", "0.01", "/second"),
    ("cents_per_video_output_second", "output_video", "0.01", "/second"),
    ("cents_per_megapixel_second", "output_video", "0.01", "/megapixel-second"),
    ("cents_per_image_input", "input_image", "0.01", "/image"),
    ("minimum_cents_per_generation", "minimum_generation", "0.01", "/request"),
    ("image_to_video_duration_seconds", "output_video", "1", "/second"),
    ("text_to_video_duration_seconds", "output_video", "1", "/second"),
    ("duration_seconds", "output_video", "1", "/second"),
    # A video token is the vendor's own measure: priced as if it were a language
    # token it would compare two things the vendor bills apart.
    ("video_tokens", "output_video_token", "1000000", "/million-video-tokens"),
)

# A catalogue entry that publishes no rate at all. Both states are the vendor's own
# doing and neither is an amount, so neither is recorded as one.
FREE_ENTRY = "free"
VARIABLE_ENTRY = "varies"


def _payload(document: str, what: str) -> list[dict[str, Any]]:
    """Read the ``data`` array of one OpenRouter document."""
    try:
        parsed = json.loads(document)
    except json.JSONDecodeError as exc:
        raise SourceError(f"OpenRouter {what} was not JSON") from exc
    entries = parsed.get("data") if isinstance(parsed, dict) else None
    if not isinstance(entries, list):
        raise SourceError(f"unexpected OpenRouter {what} shape")
    return [entry for entry in entries if isinstance(entry, dict) and entry.get("id")]


def openrouter_entries(client: Any) -> list[dict[str, Any]]:
    """Every model the models API publishes, the vendor's alias entries left out.

    An id beginning with ``~`` is the alias entry the API documents for "latest"
    resolution: it names the target it resolves to and repeats that target's
    prices under a second id, so listing it would report one model twice.
    """
    return [
        entry
        for entry in _payload(client.get_text(OPENROUTER_MODELS_URL), "catalogue")
        if not str(entry["id"]).startswith("~")
    ]


def openrouter_video_entries(client: Any) -> dict[str, dict[str, Any]]:
    """The video API's entries, keyed by the id the models API also lists."""
    return {
        str(entry["id"]): entry
        for entry in _payload(client.get_text(OPENROUTER_VIDEOS_URL), "video list")
    }


def openrouter_model_id(model_id: str) -> str:
    """The model a catalogue id belongs to, with a billing-mode suffix removed."""
    return model_id[: -len(BATCH_SUFFIX)] if model_id.endswith(BATCH_SUFFIX) else model_id


def openrouter_variant(model_id: str) -> str:
    """The billing mode an id names, as the offer that carries it, or ``""``."""
    return "batch" if model_id.endswith(BATCH_SUFFIX) else ""


def _figure(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _scaled(value: Any, factor: str) -> str | None:
    """One published figure with a unit's factor applied, or ``None``.

    A negative figure is not an amount at all — the API uses one to say a router's
    charge depends on the model it picks — so it is never scaled into a price.
    """
    number = _figure(value)
    if number is None or number < 0:
        return None
    return format((number * Decimal(factor)).normalize(), "f")


def openrouter_rate_fields(
    entry: dict[str, Any],
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Split an entry's published fields into its per-token and per-picture rates.

    ``image`` is a per-picture rate exactly when the entry also publishes
    ``image_token``; without that field it is one more token rate, which is how the
    models publishing it alone are billed.
    """
    per_token = list(OPENROUTER_TOKEN_FIELDS)
    per_picture = [OPENROUTER_IMAGE_FIELD]
    if "image_token" not in (entry.get("pricing") or {}):
        per_token.extend(per_picture)
        per_picture = []
    return per_token, per_picture


def openrouter_rates(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every rate one entry publishes, keyed by the vendor's own field name."""
    pricing = entry.get("pricing") or {}
    per_token, per_picture = openrouter_rate_fields(entry)
    rates: dict[str, dict[str, Any]] = {}
    for field, kind, label in per_token:
        amount = _scaled(pricing.get(field), "1")
        if amount is None:
            continue
        rates[field] = price_item(
            kind, label, per_million_tokens(amount, 1), "USD_per_million_tokens"
        )
    for field, kind, label in per_picture:
        amount = _scaled(pricing.get(field), "1")
        if amount is not None:
            rates[field] = price_item(kind, label, amount, "USD_per_image")
    for field, kind, label in OPENROUTER_REQUEST_FIELDS:
        amount = _scaled(pricing.get(field), "1")
        if amount is not None:
            rates[field] = price_item(kind, label, amount, "USD_per_request")
    return rates


def openrouter_rate_order(entry: dict[str, Any]) -> list[str]:
    """The field names one entry publishes, in the order a bill is read."""
    per_token, per_picture = openrouter_rate_fields(entry)
    return [
        field
        for field, _, _ in (*per_token, *per_picture, *OPENROUTER_REQUEST_FIELDS)
        if field in (entry.get("pricing") or {})
    ]


def ordered_rates(
    entry: dict[str, Any], rates: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Lay an entry's rates out in the vendor's own field order."""
    return [rates[field] for field in openrouter_rate_order(entry) if field in rates]


def openrouter_charges(rates: dict[str, dict[str, Any]]) -> bool:
    """Whether any published rate bills something rather than nothing."""
    return any(not is_free_amount(price.get("amount")) for price in rates.values())


def openrouter_pricing_state(
    entry: dict[str, Any], *, billed_elsewhere: bool = False
) -> str | None:
    """Name the state of an entry that publishes no rate, or return ``None``.

    A negative figure is the vendor's own marker for a charge that depends on the
    model a router picks, which is not an amount at all; every figure being zero is
    the vendor publishing no charge. An entry whose rates this tool reads from the
    vendor's other document (a video model, priced per second there) has a price,
    so its zeroes state nothing.
    """
    if billed_elsewhere:
        return None
    figures = [
        number
        for field, value in (entry.get("pricing") or {}).items()
        if field not in OPENROUTER_NON_RATE_FIELDS
        for number in [_figure(value)]
        if number is not None
    ]
    if not figures or any(number > 0 for number in figures):
        return None
    return VARIABLE_ENTRY if any(number < 0 for number in figures) else FREE_ENTRY


def _clock(value: Any) -> str:
    """One ``utc_start``/``utc_end`` clock as the HH:MM the vendor's field means."""
    try:
        stamp = int(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{stamp // 100:02d}:{stamp % 100:02d}"


def openrouter_window(override: dict[str, Any]) -> str:
    """The daily window an override applies inside, or ``""`` when it states none."""
    if "utc_start" not in override or "utc_end" not in override:
        return ""
    window = f"UTC {_clock(override['utc_start'])}–{_clock(override['utc_end'])}"
    days = override.get("utc_days")
    if isinstance(days, list) and days:
        window += f"（{','.join(str(day) for day in days)}）"
    return window


def openrouter_override_conditions(override: dict[str, Any]) -> dict[str, Any]:
    """The condition an override applies under, in the vendor's own field names."""
    conditions: dict[str, Any] = {}
    if "min_prompt_tokens" in override:
        conditions["context_tier"] = f"min_prompt_tokens > {override['min_prompt_tokens']}"
    window = openrouter_window(override)
    if window:
        conditions["time_band"] = window
    return conditions


def openrouter_override_rates(
    entry: dict[str, Any],
    override: dict[str, Any],
    base: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """The rates an override bills, inheriting the ones it does not restate.

    The API states that a key absent from an override inherits the base price, so
    this is the base rate set with the override's own figures in their place — the
    vendor's documented rule, rather than a rate derived from two others.
    """
    rates = dict(base)
    for field, kind, label in OPENROUTER_TOKEN_FIELDS:
        if field not in override:
            continue
        amount = _scaled(override[field], "1")
        if amount is None:
            continue
        rates[field] = price_item(
            kind, label, per_million_tokens(amount, 1), "USD_per_million_tokens"
        )
    return ordered_rates(entry, rates)


def openrouter_window_overrides(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """The overrides that state a daily window, in the order the vendor lists them.

    The API tiles the whole day with them: exactly one applies to any instant, and
    the top-level prices are whichever one is running now. They are therefore the
    offers, and the top-level rates are not offered beside them — the same rate
    under two names would move every time the clock crossed a window boundary.
    """
    return [
        override
        for override in (entry.get("pricing") or {}).get("overrides") or []
        if isinstance(override, dict) and openrouter_window(override)
    ]


def openrouter_video_prices(entry: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every rate the video API publishes for one model, with its own scope."""
    prices: list[dict[str, Any]] = []
    for sku, value in ((entry or {}).get("pricing_skus") or {}).items():
        matched = next(
            (
                sku_rule
                for sku_rule in OPENROUTER_VIDEO_SKUS
                if sku == sku_rule[0] or sku.startswith(f"{sku_rule[0]}_")
            ),
            None,
        )
        if matched is None:
            # An SKU this tool cannot read keeps the vendor's own key as its unit
            # rather than being labelled with one the vendor never published.
            amount = _scaled(value, "1")
            if amount is not None:
                prices.append(price_item("other", sku, amount, sku))
            continue
        prefix, kind, factor, phrase = matched
        amount = _scaled(value, factor)
        if amount is None:
            continue
        scope = sku[len(prefix) :].lstrip("_")
        prices.append(
            price_item(
                kind,
                f"{prefix}（{scope}）" if scope else prefix,
                amount,
                unit_code(phrase, "USD"),
            )
        )
    return prices


class OpenRouterAdapter(PriceSource):
    """OpenRouter's own catalogue, in every modality it publishes."""

    provider_id = "openrouter"
    provider_name = "OpenRouter"
    source_url = OPENROUTER_MODELS_URL
    catalog_url = OPENROUTER_SITE_URL
    source_kind = "anonymous_api"
    currency = "USD"
    region = "全球"
    delivery_mode = "third_party_hosted"

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._entries: list[dict[str, Any]] | None = None
        self._videos: dict[str, dict[str, Any]] | None = None

    def _catalogue(self) -> list[dict[str, Any]]:
        if self._entries is None:
            self._entries = openrouter_entries(self.client)
        return self._entries

    def _video_catalogue(self) -> dict[str, dict[str, Any]]:
        if self._videos is None:
            self._videos = openrouter_video_entries(self.client)
        return self._videos

    def _entries_by_model(self) -> dict[str, list[dict[str, Any]]]:
        """Group both documents by model, so a tier is built onto the model it prices.

        The two documents describe the same catalogue, and a model the video
        document lists before the models document does is still part of it: reading
        only the first would leave it out of the catalogue entirely rather than
        report it with the rates the video document publishes.
        """
        grouped: dict[str, list[dict[str, Any]]] = {}
        for entry in self._catalogue():
            key = normalize_model(openrouter_model_id(str(entry["id"])))
            grouped.setdefault(key, []).append(entry)
        for entry in self._video_catalogue().values():
            key = normalize_model(openrouter_model_id(str(entry["id"])))
            if key not in grouped:
                grouped[key] = [entry]
        return grouped

    def _offers(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """Every way one entry can be bought, from the rates it publishes."""
        video_prices = openrouter_video_prices(
            self._video_catalogue().get(openrouter_model_id(str(entry["id"])))
        )
        rates = openrouter_rates(entry)
        if not openrouter_charges(rates):
            # The models API reports no per-token rate for a video or image model,
            # so where the vendor prices it elsewhere those zeroes are placeholders
            # rather than a charge of nothing.
            return (
                [self._video_offer(entry, video_prices)] if video_prices else []
            )
        variant = openrouter_variant(str(entry["id"]))
        conditions: dict[str, Any] = {"billing_mode": "pay_as_you_go"}
        if variant:
            conditions["service_tier"] = variant
        windows = openrouter_window_overrides(entry)
        offers: list[dict[str, Any]] = []
        if not windows:
            offers.append(
                {
                    "name": variant or "pay_as_you_go",
                    "conditions": conditions,
                    "prices": ordered_rates(entry, rates),
                }
            )
        for override in (entry.get("pricing") or {}).get("overrides") or []:
            if not isinstance(override, dict):
                continue
            prices = openrouter_override_rates(entry, override, rates)
            if not prices:
                continue
            scoped = {**conditions, **openrouter_override_conditions(override)}
            offers.append(
                {
                    "name": str(scoped.get("time_band") or scoped.get("context_tier") or variant or "pay_as_you_go"),
                    "conditions": scoped,
                    "prices": prices,
                }
            )
        if video_prices:
            offers.append(self._video_offer(entry, video_prices))
        return offers

    @staticmethod
    def _video_offer(
        entry: dict[str, Any], video_prices: list[dict[str, Any]]
    ) -> dict[str, Any]:
        variant = openrouter_variant(str(entry["id"]))
        return {
            "name": f"{variant}｜video" if variant else "video",
            "conditions": {
                "billing_mode": "pay_as_you_go",
                "output_modality": "video",
            },
            "prices": video_prices,
        }

    def _record_for(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        """Build one model from every catalogue entry that prices it."""
        head = next(
            (entry for entry in entries if not openrouter_variant(str(entry["id"]))),
            entries[0],
        )
        model_id = openrouter_model_id(str(head["id"]))
        architecture = head.get("architecture") or {}
        offers: list[dict[str, Any]] = []
        for entry in entries:
            offers.extend(self._offers(entry))
        return make_record(
            self.provider_id,
            self.provider_name,
            model_id,
            str(head.get("name") or model_id),
            self.region,
            offers,
            f"{OPENROUTER_SITE_URL}/{head['id']}",
            self.source_kind,
            now_iso(),
            currency=self.currency,
            delivery_mode=self.delivery_mode,
            model_family=model_family(model_id),
            output_modalities=list(architecture.get("output_modalities") or []),
            **self._record_extras(head, offers),
        )

    @staticmethod
    def _record_extras(
        entry: dict[str, Any], offers: list[dict[str, Any]]
    ) -> dict[str, Any]:
        pricing = entry.get("pricing") or {}
        extras: dict[str, Any] = {}
        state = openrouter_pricing_state(
            entry, billed_elsewhere=bool(offers)
        )
        if state is not None:
            extras["pricing_state"] = state
        overrides = [
            override
            for override in pricing.get("overrides") or []
            if isinstance(override, dict) and openrouter_window(override)
        ]
        if overrides:
            extras["time_bands"] = {
                "window": "；".join(
                    dict.fromkeys(openrouter_window(override) for override in overrides)
                ),
                "statements": [
                    "OpenRouter pricing.overrides："
                    + json.dumps(override, ensure_ascii=False, sort_keys=True)
                    for override in overrides
                ],
                "source_url": OPENROUTER_MODELS_URL,
            }
        if entry.get("expiration_date"):
            extras["expiration_date"] = entry["expiration_date"]
        if (entry.get("top_provider") or {}).get("max_completion_tokens"):
            extras["max_output_tokens"] = entry["top_provider"]["max_completion_tokens"]
        return extras

    def list_models(self, prefix: str = "") -> list[str]:
        key = normalize_model(prefix)
        return sorted(
            (
                model
                for model in self._entries_by_model()
                if not key or model.startswith(key)
            ),
            key=str.lower,
        )

    def query(self, model: str) -> list[dict[str, Any]]:
        """Read one model, including a query that names its batch id."""
        key = normalize_model(openrouter_model_id(model))
        entries = self._entries_by_model().get(key)
        return [self._record_for(entries)] if entries else []

    def catalog_records(self) -> list[dict[str, Any]]:
        """Build every record from the two documents already read, in one pass."""
        grouped = self._entries_by_model()
        return [self._record_for(grouped[key]) for key in sorted(grouped)]
