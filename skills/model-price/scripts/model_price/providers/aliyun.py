"""Aliyun Model Studio (百炼) anonymous model catalogue API."""

from __future__ import annotations

import json
from typing import Any

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


def time_band_label(value: str) -> str:
    """Return Bailian's own wording for an API band key."""
    return ALIYUN_TIME_BANDS.get(value.lower(), value)


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
        params = {
            "Api": ALIYUN_API_NAME,
            "Data": {"input": input_data, "cornerstoneParam": {}},
        }
        outer = self.client.post_form(ALIYUN_URL, {"params": json.dumps(params)})
        try:
            data = outer["data"]["DataV2"]["data"]
            if str(data.get("code")) != "200":
                raise SourceError(f"Aliyun returned code {data.get('code')}")
            return data["data"]
        except (KeyError, TypeError) as exc:
            raise SourceError("unexpected Aliyun response shape") from exc

    def list_models(self, prefix: str = "") -> list[str]:
        models: set[str] = set()
        page = 1
        total = None
        while total is None or (page - 1) * 50 < total:
            data = self._request({"pageNo": page, "pageSize": 50})
            total = int(data.get("total", 0))
            for item in data.get("list", []):
                model = item.get("model")
                if model and (
                    not prefix
                    or normalize_model(model).startswith(normalize_model(prefix))
                ):
                    models.add(model)
            page += 1
            if page > 100:
                raise SourceError("Aliyun pagination exceeded safety limit")
        return sorted(models, key=str.lower)

    def query(self, model: str) -> list[dict[str, Any]]:
        retrieved_at = now_iso()
        data = self._request({"queryPrice": True, "model": model})
        candidates: list[dict[str, Any]] = []
        for item in data.get("list", []):
            candidates.extend(item.get("items") or [item])
        result = []
        for item in candidates:
            if normalize_model(item.get("model", "")) != normalize_model(model):
                continue
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
                offers.append(
                    {"name": label, "conditions": conditions, "prices": prices}
                )
            result.append(
                make_record(
                    self.provider_id,
                    self.provider_name,
                    item["model"],
                    item.get("name", item["model"]),
                    "中国区",
                    offers,
                    self.source_url,
                    self.source_kind,
                    retrieved_at,
                    price_time_bands=item.get("priceTimeBands", []),
                    service_sites=item.get("serviceSites", []),
                    delivery_mode=(
                        "platform_hosted"
                        if item.get("inferenceProvider") == "aliyun-bailian"
                        else "third_party_hosted"
                    ),
                    inference_provider=item.get("inferenceProvider"),
                    access_scope=item.get("scope"),
                    model_family=model_family(item["model"]),
                    time_bands=time_bands_for(
                        self._band_text(),
                        model_id=item["model"],
                        display_name=item.get("name", item["model"]),
                        source_url=ALIYUN_BAND_DOC_URL,
                    ),
                )
            )
        return result
