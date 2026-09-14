"""DeepSeek first-party pricing, read from the rendered pricing page."""

from __future__ import annotations

import re
from typing import Any

from ..core import PriceSource, now_iso
from ..errors import SourceError
from ..models import (
    model_family,
    model_key,
    normalize_model,
    strip_footnote_markers,
)
from ..parsing import TextTableParser
from ..pricing import make_record, price_item

DEEPSEEK_URL = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"


class DeepSeekAdapter(PriceSource):
    provider_id = "deepseek"
    provider_name = "DeepSeek 原厂"
    source_url = DEEPSEEK_URL
    source_kind = "official_document"
    model_header_prefix = "模型"

    def _table(self) -> list[list[str]]:
        parser = TextTableParser()
        parser.feed(self.client.get_text(DEEPSEEK_URL).replace("\x00", ""))
        table = next(
            (
                candidate
                for candidate in parser.tables
                if candidate
                and candidate[0]
                and normalize_model(candidate[0][0]).startswith(self.model_header_prefix)
            ),
            None,
        )
        if not table:
            raise SourceError("DeepSeek pricing table was not found")
        return table

    def _models(self, table: list[list[str]]) -> list[str]:
        # Model columns carry footnote markers such as "deepseek-flash(1)".
        return [strip_footnote_markers(name) for name in table[0][1:]]

    def list_models(self, prefix: str = "") -> list[str]:
        models = self._models(self._table())
        if prefix:
            normalized = normalize_model(prefix)
            models = [m for m in models if normalize_model(m).startswith(normalized)]
        return models

    def query(self, model: str) -> list[dict[str, Any]]:
        table = self._table()
        models = self._models(table)
        try:
            model_index = next(
                i
                for i, item in enumerate(models)
                if model_key(item) == model_key(model)
            )
        except StopIteration:
            return []
        type_map = {
            "缓存命中": "cache_hit",
            "缓存未命中": "input",
            "输出": "output",
        }
        offers: dict[str, dict[str, Any]] = {}
        current_kind = ""
        current_label = ""
        for row in table:
            joined = " ".join(row)
            detected = next(
                (value for label, value in type_map.items() if label in joined), None
            )
            if detected:
                current_kind = detected
                current_label = next(label for label in type_map if label in joined)
            band = next((b for b in ("空闲时段", "高峰时段") if b in row), None)
            if not band or not current_kind:
                continue
            amounts = [
                re.sub(r"元$", "", value)
                for value in row
                if re.fullmatch(r"\d+(?:\.\d+)?元", value)
            ]
            if len(amounts) != len(models):
                continue
            offer = offers.setdefault(
                band,
                {
                    "name": "off_peak" if band == "空闲时段" else "peak",
                    "conditions": {
                        "time_band": band,
                        "definition": (
                            "高峰：北京时间周一至周五 9:00–12:00、14:00–18:00；"
                            "其余为空闲时段"
                        ),
                    },
                    "prices": [],
                },
            )
            offer["prices"].append(
                price_item(
                    current_kind,
                    current_label,
                    amounts[model_index],
                    "CNY_per_million_tokens",
                )
            )
        return [
            make_record(
                self.provider_id,
                self.provider_name,
                models[model_index],
                models[model_index],
                "中国区",
                list(offers.values()),
                self.source_url,
                self.source_kind,
                now_iso(),
                delivery_mode="first_party",
                model_family=model_family(models[model_index]),
            )
        ]
