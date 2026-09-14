"""Zhipu BigModel pricing, read from its anonymous configuration API."""

from __future__ import annotations

import json
import re
from typing import Any

from ..core import PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, normalize_model
from ..pricing import make_record, price_item, unit_code
from ..text import clean_text, numeric_values

ZHIPU_PAGE_URL = "https://bigmodel.cn/pricing"
ZHIPU_CONFIG_URL = "https://bigmodel.cn/api/biz/operation/query?ids=1160%2C1161"


def parse_display_price(value: str, label: str) -> dict[str, Any]:
    cleaned = clean_text(value)
    values = numeric_values(value)
    if "免费" in cleaned:
        return price_item("other", label, "0", unit_code(value), display=cleaned)
    amount = values[-1] if values else None
    list_amount = values[0] if len(values) > 1 else None
    return price_item(
        "other",
        label,
        amount,
        unit_code(value),
        display=cleaned,
        list_amount=list_amount,
    )


class ZhipuAdapter(PriceSource):
    provider_id = "zhipu"
    provider_name = "智谱 BigModel"
    source_url = ZHIPU_PAGE_URL
    source_kind = "anonymous_config_api"

    def _cards(self) -> list[dict[str, Any]]:
        response = json.loads(self.client.get_text(ZHIPU_CONFIG_URL))
        if str(response.get("code")) != "200":
            raise SourceError(f"Zhipu returned code {response.get('code')}")
        cards = []
        for entry in response.get("data", []):
            config = json.loads(entry["content"])
            for card in config.get("list", []):
                values = []
                table = card.get("table", {})
                fields = [field.get("code") for field in table.get("fieldList", [])]
                if len(fields) >= 2:
                    for row in table.get("modelList", []):
                        left = row.get(fields[0], {}).get("value", "")
                        right = row.get(fields[1], {}).get("value", "")
                        values.append((clean_text(str(left)), str(right)))
                cards.append({**card, "values": values})
            for tab in config.get("tabs", []):
                if tab.get("title") != "模型":
                    continue
                for card in tab.get("cards", []):
                    values = [
                        (
                            field.get("label", ""),
                            " / ".join(map(str, field.get("values", []))),
                        )
                        for field in card.get("fieldList", [])
                    ]
                    cards.append({**card, "values": values})
        return cards

    def list_models(self, prefix: str = "") -> list[str]:
        models = {
            normalize_model(card.get("title", ""))
            for card in self._cards()
            if card.get("title")
        }
        if prefix:
            normalized = normalize_model(prefix)
            models = {m for m in models if m.startswith(normalized)}
        return sorted(models)

    def query(self, model: str) -> list[dict[str, Any]]:
        card = next(
            (
                c
                for c in self._cards()
                if normalize_model(c.get("title", "")) == normalize_model(model)
            ),
            None,
        )
        if not card:
            return []
        type_map = {
            "输入单价": "input",
            "输入价格": "input",
            "输出单价": "output",
            "输出价格": "output",
            "缓存命中": "cache_hit",
            "缓存存储": "cache_storage",
        }
        prices = []
        notes = []
        for label, value in card.get("values", []):
            kind = next((kind for key, kind in type_map.items() if key in label), None)
            if kind:
                item = parse_display_price(value, label)
                item["type"] = kind
                prices.append(item)
            elif "Batch" in label or "定价" in label:
                notes.append(f"{label}：{clean_text(value)}")
        conditions = {}
        if card.get("tag") and re.search(r"折|限时|优惠|促销", card["tag"]):
            conditions["promotion"] = card["tag"]
        return [
            make_record(
                self.provider_id,
                self.provider_name,
                normalize_model(card["title"]),
                card["title"],
                "中国区",
                [
                    {
                        "name": "online_standard",
                        "conditions": conditions,
                        "prices": prices,
                    }
                ],
                self.source_url,
                self.source_kind,
                now_iso(),
                config_url=ZHIPU_CONFIG_URL,
                notes=notes,
                delivery_mode="first_party",
                model_family=model_family(card["title"]),
            )
        ]
