"""Aliyun Model Studio (百炼) anonymous model catalogue API."""

from __future__ import annotations

import json
from typing import Any

from ..core import PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, normalize_model
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


class AliyunAdapter(PriceSource):
    provider_id = "aliyun"
    provider_name = "阿里云百炼"
    source_url = ALIYUN_URL
    source_kind = "anonymous_api"

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
                conditions = {} if band == "standard" else {"time_band": band}
                offers.append(
                    {"name": band, "conditions": conditions, "prices": prices}
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
                )
            )
        return result
