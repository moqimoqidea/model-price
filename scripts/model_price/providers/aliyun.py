"""Qianwen model-market catalogue and per-model detail pages.

The market's public JSON keeps every model variant and price tier separate, while
the derived detail page carries the prose that explains a time-band window. This
adapter reads both without credentials and preserves those distinctions in the
shared price shape.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterator
from urllib.parse import quote

from ..core import PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, normalize_model
from ..parsing import time_bands_for
from ..pricing import make_record, price_item, unit_code

ALIYUN_MODELS_URL = "https://www.qianwenai.com/models"
ALIYUN_API_PRODUCT = "AliyunDeliveryService"
ALIYUN_API_ACTION = "ListModelSeries"
ALIYUN_API_URL = (
    "https://platform-home.qianwenai.com/data/api.json"
    f"?product={ALIYUN_API_PRODUCT}&action={ALIYUN_API_ACTION}"
)

ALIYUN_PRICE_TYPES = {
    "input_token": "input",
    "output_token": "output",
    "input_token_cache": "cache_hit",
    "input_token_cache_creation_5m": "cache_write",
    "input_token_cache_read": "cache_read_explicit",
    "input_token_batch": "batch_input",
    "output_token_batch": "batch_output",
    "input_token_batch_chat": "batch_chat_input",
    "output_token_batch_chat": "batch_chat_output",
}

# The API keys its bands in English while the market page labels them 忙时/闲时.
# Keep the market's wording so a reader can match a report to the source page.
ALIYUN_TIME_BANDS = {"peak": "忙时", "offpeak": "闲时"}

# The market accepts the whole current series catalogue in one request. Paging
# remains in place so catalogue growth does not silently cut off later models.
CATALOG_PAGE_SIZE = 200
CATALOG_PAGE_LIMIT = 100
ALIYUN_TIMEZONE = timezone(timedelta(hours=8))


def qianwen_model_url(model_id: str) -> str:
    """Return the public market detail URL for one literal model id."""
    return f"{ALIYUN_MODELS_URL}/{quote(model_id, safe='')}"


def qianwen_catalog_request(
    client: Any, input_data: dict[str, Any]
) -> dict[str, Any]:
    """Read one page from the model market's credential-free catalogue API."""
    params = {"Language": "zh-CN", **input_data}
    outer = client.post_form(
        ALIYUN_API_URL,
        {
            "product": ALIYUN_API_PRODUCT,
            "action": ALIYUN_API_ACTION,
            "params": json.dumps(
                params, ensure_ascii=False, separators=(",", ":")
            ),
        },
        idempotent=True,
    )
    try:
        if str(outer.get("code")) != "200":
            raise SourceError(f"Aliyun returned code {outer.get('code')}")
        data = outer["data"]
        if not isinstance(data.get("Data"), list) or not isinstance(
            data.get("Ext"), dict
        ):
            raise SourceError("unexpected Aliyun response shape")
        return data
    except (AttributeError, KeyError, TypeError) as exc:
        raise SourceError("unexpected Aliyun response shape") from exc


def qianwen_catalog_items(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Flatten model series into the independently priced models they contain."""
    for series in payload.get("Data", []):
        if not isinstance(series, dict):
            raise SourceError("unexpected Aliyun model-series shape")
        for item in series.get("Items") or [series]:
            if not isinstance(item, dict):
                raise SourceError("unexpected Aliyun model shape")
            yield item


def qianwen_catalogue(
    client: Any, *, query: str = "", page_size: int = CATALOG_PAGE_SIZE
) -> Iterator[dict[str, Any]]:
    """Walk every matching series while keeping transport and paging in one place."""
    for page in range(1, CATALOG_PAGE_LIMIT + 1):
        request: dict[str, Any] = {"PageNo": page, "PageSize": page_size}
        if query:
            request["Query"] = query
        data = qianwen_catalog_request(client, request)
        series = data.get("Data") or []
        try:
            total = int(data["Ext"]["totalCount"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceError("unexpected Aliyun pagination shape") from exc
        yield from qianwen_catalog_items(data)
        if not series or page * page_size >= total:
            return
    raise SourceError("Aliyun pagination exceeded safety limit")


def qianwen_lifecycle(
    item: dict[str, Any], *, at: datetime | None = None
) -> str:
    """Translate the market's preview and scheduled-withdrawal evidence."""
    name = str(item.get("Name") or "")
    if (
        str(item.get("VersionTag", "")).upper() == "PREVIEW"
        or "preview" in name.lower()
        or "预览" in name
    ):
        return "preview"
    offline = ((item.get("OfflineInfo") or {}).get("Inference") or {}).get(
        "OfflineTime"
    )
    if not offline:
        return "active"
    try:
        sunset = datetime.fromisoformat(str(offline).replace("Z", "+00:00"))
        if sunset.tzinfo is None:
            sunset = sunset.replace(tzinfo=ALIYUN_TIMEZONE)
        observed = at or datetime.now(ALIYUN_TIMEZONE)
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=ALIYUN_TIMEZONE)
        return "retired" if sunset <= observed else "legacy"
    except ValueError:
        return "legacy"


def qianwen_model_metadata(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize the introduction fields shared by price and description reads."""
    model_info = item.get("ModelInfo") or {}
    inference = item.get("InferenceMetadata") or {}

    def first_value(name: str) -> Any:
        value = item.get(name)
        return model_info.get(name) if value is None else value

    specifications = {
        key: value
        for key, value in {
            "context_window": first_value("ContextWindow"),
            "max_input_tokens": first_value("MaxInputTokens"),
            "max_output_tokens": first_value("MaxOutputTokens"),
            "input_modalities": "、".join(inference.get("RequestModality") or []),
            "output_modalities": "、".join(inference.get("ResponseModality") or []),
        }.items()
        if value not in (None, "")
    }
    offline = ((item.get("OfflineInfo") or {}).get("Inference") or {}).get(
        "OfflineTime"
    )
    if offline:
        specifications["sunset_note"] = f"{offline} 下线"
    model_id = str(item.get("Model") or "")
    return {
        "summary": item.get("Description") or item.get("ShortDescription"),
        "capabilities": [
            *(item.get("Capabilities") or []),
            *(item.get("Features") or []),
        ],
        "lifecycle": qianwen_lifecycle(item),
        "specifications": specifications,
        "source_url": qianwen_model_url(model_id) if model_id else ALIYUN_MODELS_URL,
    }


def time_band_label(value: str) -> str:
    """Return the market page's own wording for an API band key."""
    return ALIYUN_TIME_BANDS.get(value.lower(), value)


def discounted_amount(
    amount: Any, discount: Any
) -> tuple[str | None, str | None]:
    """Return the effective amount and its list amount when a discount applies."""
    if amount is None:
        return None, None
    listed = str(amount)
    if discount is None:
        return listed, None
    try:
        ratio = Decimal(str(discount))
        if ratio == 1:
            return listed, None
        current = Decimal(listed) * ratio
    except InvalidOperation:
        return listed, None
    return format(current.normalize(), "f"), listed


def is_zero_amount(value: str | None) -> bool:
    """Identify a free entitlement regardless of how its zero is formatted."""
    if value is None:
        return False
    try:
        return Decimal(value) == 0
    except InvalidOperation:
        return False


def price_groups(item: dict[str, Any]) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """Yield direct prices or each independently conditioned multi-price tier."""
    tiers = [
        tier
        for tier in item.get("MultiPrices") or []
        if any(price.get("Price") is not None for price in tier.get("Prices") or [])
    ]
    if tiers:
        for tier in tiers:
            yield str(tier.get("RangeName") or ""), tier.get("Prices") or []
        return
    yield "", item.get("Prices") or []


class AliyunAdapter(PriceSource):
    provider_id = "aliyun"
    provider_name = "阿里云百炼"
    source_url = ALIYUN_API_URL
    catalog_url = ALIYUN_MODELS_URL
    source_kind = "anonymous_api"

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._items: list[dict[str, Any]] | None = None
        self._detail_documents: dict[str, str] = {}

    def _catalogue_items(self) -> list[dict[str, Any]]:
        if self._items is None:
            self._items = list(qianwen_catalogue(self.client))
        return self._items

    def _detail_text(self, model_id: str) -> str:
        """Read one market detail page, where time-band hours are explained."""
        url = qianwen_model_url(model_id)
        if url not in self._detail_documents:
            try:
                self._detail_documents[url] = self.document(url)
            except SourceError:
                self._detail_documents[url] = ""
        return self._detail_documents[url]

    def list_models(self, prefix: str = "") -> list[str]:
        key = normalize_model(prefix)
        models = {
            str(item["Model"])
            for item in self._catalogue_items()
            if item.get("Model")
            and (not key or normalize_model(str(item["Model"])).startswith(key))
        }
        return sorted(models, key=str.lower)

    def catalog_records(self) -> list[dict[str, Any]]:
        """Build every record from the market catalogue already read in one pass."""
        records: dict[str, dict[str, Any]] = {}
        for item in self._catalogue_items():
            key = normalize_model(str(item.get("Model") or ""))
            if key:
                record = self._record_for(item)
                if key not in records or (not records[key]["offers"] and record["offers"]):
                    records[key] = record
        return [records[key] for key in sorted(records)]

    def query(self, model: str) -> list[dict[str, Any]]:
        key = normalize_model(model)
        return [
            self._record_for(item)
            for item in self._catalogue_items()
            if normalize_model(str(item.get("Model") or "")) == key
        ]

    def _record_for(self, item: dict[str, Any]) -> dict[str, Any]:
        """Keep every market tier and time band as an independent offer."""
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for tier, prices in price_groups(item):
            for price in prices:
                amount, list_amount = discounted_amount(
                    price.get("Price"), price.get("Discount")
                )
                # A zero is a free entitlement, not a monetary model price.
                if amount is None or is_zero_amount(amount):
                    continue
                band = str(price.get("TimeBand") or "standard")
                grouped.setdefault((tier, band), []).append(
                    price_item(
                        ALIYUN_PRICE_TYPES.get(
                            price.get("Type"), price.get("Type") or "other"
                        ),
                        price.get("PriceName") or price.get("Type") or "价格",
                        amount,
                        unit_code(str(price.get("PriceUnit") or "")),
                        list_amount=list_amount,
                        discount=price.get("Discount"),
                    )
                )

        offers = []
        for (tier, band), prices in grouped.items():
            label = time_band_label(band)
            conditions: dict[str, Any] = {}
            if tier:
                conditions[
                    "context_tier" if "输入" in tier else "price_tier"
                ] = tier
            if band != "standard":
                conditions["time_band"] = label
            name = "｜".join(
                part
                for part in (tier, label if band != "standard" else "")
                if part
            )
            offers.append(
                {"name": name or "standard", "conditions": conditions, "prices": prices}
            )

        model_id = str(item["Model"])
        display_name = item.get("Name") or model_id
        detail_url = qianwen_model_url(model_id)
        has_time_bands = any(
            "time_band" in offer.get("conditions", {}) for offer in offers
        )
        return make_record(
            self.provider_id,
            self.provider_name,
            model_id,
            display_name,
            "中国区",
            offers,
            detail_url,
            self.source_kind,
            now_iso(),
            delivery_mode=(
                "platform_hosted"
                if item.get("InferenceProvider") == "aliyun-bailian"
                else "third_party_hosted"
            ),
            source_updated_at=item.get("UpdateAt"),
            model_family=model_family(model_id),
            model_metadata=qianwen_model_metadata(item),
            time_bands=(
                time_bands_for(
                    self._detail_text(model_id),
                    model_id=model_id,
                    display_name=str(display_name),
                    source_url=detail_url,
                )
                if has_time_bands
                else {}
            ),
        )
