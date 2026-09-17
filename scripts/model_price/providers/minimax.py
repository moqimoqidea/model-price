"""MiniMax first-party pricing, read from its Markdown pricing document."""

from __future__ import annotations

import re
from typing import Any

from ..core import PriceSource, now_iso
from ..models import model_family, normalize_model
from ..parsing import split_markdown_row
from ..pricing import make_record, price_item
from ..text import CELL_BREAK_RE, clean_text, numeric_values

MINIMAX_URL = "https://platform.minimax.cn/docs/guides/pricing-paygo.md"


class MiniMaxAdapter(PriceSource):
    provider_id = "minimax"
    provider_name = "MiniMax 原厂"
    source_url = MINIMAX_URL
    source_kind = "official_markdown"

    def _rows(self) -> list[dict[str, Any]]:
        text = self.client.get_text(MINIMAX_URL)
        language = text.split("## 语言模型", 1)[1].split("## 语音", 1)[0]
        rows: list[dict[str, Any]] = []
        tier = "standard"
        historical = False
        for line in language.splitlines():
            tab = re.search(r'<Tab title="([^"]+)"', line)
            if tab:
                tier = "priority" if tab.group(1) == "优先*" else "standard"
            if "</Tabs>" in line:
                tier = "standard"
            if '<Accordion title="历史模型">' in line:
                historical = True
            if "</Accordion>" in line:
                historical = False
            if not line.lstrip().startswith("|") or re.match(r"^\s*\|\s*:?-", line):
                continue
            cells = split_markdown_row(line)
            if not cells or "模型" in clean_text(cells[0]):
                continue
            model_parts = CELL_BREAK_RE.split(cells[0], maxsplit=1)
            model_name = clean_text(model_parts[0])
            if not model_name or not numeric_values(" ".join(cells[1:])):
                continue
            condition = clean_text(model_parts[1]) if len(model_parts) > 1 else ""
            amounts = []
            list_amounts = []
            for cell in cells[1:]:
                values = numeric_values(cell)
                amounts.append(values[-1] if values else None)
                list_amounts.append(values[0] if len(values) > 1 else None)
            prices = []
            kinds = ["input", "output", "cache_hit", "cache_write"]
            labels = ["输入", "输出", "缓存读取", "缓存写入"]
            for index, amount in enumerate(amounts[:4]):
                if amount is not None:
                    prices.append(
                        price_item(
                            kinds[index],
                            labels[index],
                            amount,
                            "CNY_per_million_tokens",
                            list_amount=list_amounts[index],
                        )
                    )
            rows.append(
                {
                    "model": model_name,
                    "tier": tier,
                    "historical": historical,
                    "condition": condition,
                    "prices": prices,
                }
            )
        return rows

    def list_models(self, prefix: str = "") -> list[str]:
        models = {row["model"] for row in self._rows()}
        if prefix:
            normalized = normalize_model(prefix)
            models = {m for m in models if normalize_model(m).startswith(normalized)}
        return sorted(models, key=str.lower)

    def query(self, model: str) -> list[dict[str, Any]]:
        matched = [
            row
            for row in self._rows()
            if normalize_model(row["model"]) == normalize_model(model)
        ]
        if not matched:
            return []
        offers = []
        for row in matched:
            conditions: dict[str, Any] = {"service_tier": row["tier"]}
            if row["condition"]:
                conditions["context_tier"] = row["condition"]
            if row["historical"]:
                conditions["status"] = "historical"
            offers.append(
                {
                    "name": row["tier"],
                    "conditions": conditions,
                    "prices": row["prices"],
                }
            )
        return [
            make_record(
                self.provider_id,
                self.provider_name,
                normalize_model(matched[0]["model"]),
                matched[0]["model"],
                "中国区",
                offers,
                self.source_url,
                self.source_kind,
                now_iso(),
                delivery_mode="first_party",
                model_family=model_family(matched[0]["model"]),
            )
        ]
