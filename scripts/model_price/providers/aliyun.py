"""Aliyun Model Studio (百炼) anonymous model catalogue API."""

from __future__ import annotations

import json
import random
import time
from typing import Any, Iterator

from ..core import PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, normalize_model
from ..parsing import time_bands_for
from ..pricing import make_record, price_item, unit_code

ALIYUN_API_NAME = (
    "zeldaHttp.dashscopeModel./zelda/api/v1/modelCenter/listFoundationModels"
)
ALIYUN_URL = (
    "https://bailian-cs.console.aliyun.com/data/api.json"
    "?action=BroadScopeAspnGateway&product=sfm_bailian"
    f"&api={ALIYUN_API_NAME}"
    "&_v=undefined"
)


def aliyun_catalog_request(client: Any, input_data: dict[str, Any]) -> dict[str, Any]:
    """Call Bailian's anonymous model-centre gateway.

    Price and description adapters share this transport so the public response
    shape is decoded in one place.  The model-centre payload carries both prices
    and the official summary/capability metadata.
    """
    params = {
        "Api": ALIYUN_API_NAME,
        "Data": {"input": input_data, "cornerstoneParam": {}},
    }
    outer = client.post_form(ALIYUN_URL, {"params": json.dumps(params)})
    try:
        data = outer["data"]["DataV2"]["data"]
        if str(data.get("code")) != "200":
            raise SourceError(f"Aliyun returned code {data.get('code')}")
        return data["data"]
    except (KeyError, TypeError) as exc:
        raise SourceError("unexpected Aliyun response shape") from exc

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

# The catalogue keys its bands in English while the Model Studio pricing page calls
# them 忙时/闲时. The vendor's own wording is what a reader can match against the
# page, so the record carries that instead of the API enum.
ALIYUN_TIME_BANDS = {"peak": "忙时", "offpeak": "闲时"}

# The JSON carries no window — it only says a band exists. Bailian states the hours
# only in prose on the Model Studio pricing page ("错峰时段为东八区 22:00 至次日
# 8:00，其余时段为忙时"), and the same rule is repeated in its announcement
# (https://www.aliyun.com/notice/118555, "空闲时段为北京时间 22:00 - 8:00､其余为
# 高峰时段"). The window is read from there; prices still come from the JSON.
ALIYUN_BAND_DOC_URL = "https://www.alibabacloud.com/help/zh/model-studio/model-pricing"

# The catalogue API pages at 50 items and, with ``queryPrice``, returns each page's
# models complete with their prices. A whole scan is therefore a handful of
# requests rather than one per model.
CATALOG_PAGE_SIZE = 50
CATALOG_PAGE_LIMIT = 100

# Bailian's gateway throttles a burst of paged requests, and a whole scan is many
# pages — hundreds of models at fifty a page. Every request after the first waits
# a random pause: long enough to stay under the limit, and jittered so the walk
# never settles into a fixed rhythm a throttle could lock onto.
CATALOG_PAGE_PAUSE_SECONDS = (1.0, 3.0)


def time_band_label(value: str) -> str:
    """Return Bailian's own wording for an API band key."""
    return ALIYUN_TIME_BANDS.get(value.lower(), value)


def pause_before_next_page() -> float:
    """Wait a random pause between two catalogue pages, and report how long."""
    seconds = random.uniform(*CATALOG_PAGE_PAUSE_SECONDS)
    time.sleep(seconds)
    return seconds


class AliyunAdapter(PriceSource):
    provider_id = "aliyun"
    provider_name = "阿里云百炼"
    source_url = ALIYUN_URL
    source_kind = "anonymous_api"

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._band_document: str | None = None

    def _band_text(self) -> str:
        """Return the pricing page's prose, which is where the window is written.

        Fetched lazily and only once per run; a page that cannot be read leaves the
        record's window empty rather than failing an otherwise good price query.
        """
        if self._band_document is None:
            try:
                self._band_document = self.client.get_text(ALIYUN_BAND_DOC_URL)
            except SourceError:
                self._band_document = ""
        return self._band_document

    def _request(self, input_data: dict[str, Any]) -> dict[str, Any]:
        return aliyun_catalog_request(self.client, input_data)

    def list_models(self, prefix: str = "") -> list[str]:
        key = normalize_model(prefix)
        models: set[str] = set()
        for item in self._catalogue():
            model = item.get("model")
            if model and (not key or normalize_model(model).startswith(key)):
                models.add(model)
        return sorted(models, key=str.lower)

    def catalog_records(self) -> list[dict[str, Any]]:
        """Read the whole catalogue with its prices, page by page."""
        records: dict[str, dict[str, Any]] = {}
        for item in self._catalogue(query_price=True):
            key = normalize_model(item.get("model", ""))
            if key:
                records.setdefault(key, self._record_for(item))
        return [records[key] for key in sorted(records)]

    def query(self, model: str) -> list[dict[str, Any]]:
        key = normalize_model(model)
        data = self._request({"queryPrice": True, "model": model})
        return [
            self._record_for(item)
            for item in self._catalogue_items(data)
            if normalize_model(item.get("model", "")) == key
        ]

    # --- catalogue paging -------------------------------------------------

    def _catalogue(self, *, query_price: bool = False) -> Iterator[dict[str, Any]]:
        """Walk the catalogue page by page, yielding one model at a time.

        Consecutive pages are spaced by a random pause (see
        ``CATALOG_PAGE_PAUSE_SECONDS``). The first request is never delayed: a
        catalogue that fits in one page should not pay for paging.
        """
        request: dict[str, Any] = {"queryPrice": True} if query_price else {}
        page = 1
        while True:
            if page > 1:
                pause_before_next_page()
            data = self._request(
                {**request, "pageNo": page, "pageSize": CATALOG_PAGE_SIZE}
            )
            items = list(self._catalogue_items(data))
            yield from items
            total = int(data.get("total", 0))
            if not items or page * CATALOG_PAGE_SIZE >= total:
                return
            page += 1
            if page > CATALOG_PAGE_LIMIT:
                raise SourceError("Aliyun pagination exceeded safety limit")

    @staticmethod
    def _catalogue_items(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Unwrap the per-model entries a response carries, grouped or not."""
        for item in data.get("list", []):
            yield from item.get("items") or [item]

    def _record_for(self, item: dict[str, Any]) -> dict[str, Any]:
        """Build one record, keeping each time band's prices its own offer."""
        grouped_prices: dict[str, list[dict[str, Any]]] = {}
        for price in item.get("prices", []):
            band = price.get("timeBand") or "standard"
            grouped_prices.setdefault(band, []).append(
                price_item(
                    ALIYUN_PRICE_TYPES.get(
                        price.get("type"), price.get("type", "other")
                    ),
                    price.get("priceName", price.get("type", "价格")),
                    str(price["price"]) if price.get("price") is not None else None,
                    unit_code(price.get("priceUnit", "")),
                    discount=price.get("discount"),
                )
            )
        offers = []
        for band, prices in grouped_prices.items():
            label = time_band_label(band)
            conditions = {} if band == "standard" else {"time_band": label}
            offers.append({"name": label, "conditions": conditions, "prices": prices})
        display_name = item.get("name", item["model"])
        return make_record(
            self.provider_id,
            self.provider_name,
            item["model"],
            display_name,
            "中国区",
            offers,
            self.source_url,
            self.source_kind,
            now_iso(),
            price_time_bands=item.get("priceTimeBands", []),
            service_sites=item.get("serviceSites", []),
            delivery_mode=(
                "platform_hosted"
                if item.get("inferenceProvider") == "aliyun-bailian"
                else "third_party_hosted"
            ),
            inference_provider=item.get("inferenceProvider"),
            access_scope=item.get("scope"),
            # The catalogue dates each model's own last update, so a later scan can
            # say whether the vendor touched it between two runs.
            source_updated_at=item.get("updateAt"),
            model_family=model_family(item["model"]),
            model_metadata={
                "summary": item.get("description") or item.get("shortDescription"),
                "capabilities": item.get("capabilities") or [],
                "features": item.get("features") or [],
                "context_window": item.get("contextWindow"),
                "max_input_tokens": item.get("maxInputTokens"),
                "max_output_tokens": item.get("maxOutputTokens"),
                "doc_url": item.get("docUrl"),
                "lifecycle": (
                    "preview"
                    if str(item.get("versionTag", "")).upper() == "PREVIEW"
                    else "active"
                ),
            },
            time_bands=time_bands_for(
                self._band_text(),
                model_id=item["model"],
                display_name=display_name,
                source_url=ALIYUN_BAND_DOC_URL,
            ),
        )
