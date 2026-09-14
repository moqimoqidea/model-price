"""Render query payloads as JSON or as a Markdown comparison report."""

from __future__ import annotations

import json
import re
from typing import Any

DELIVERY_LABELS = {
    "platform_hosted": "平台托管",
    "self_deployed": "自部署",
    "upstream_direct": "原厂直供",
    "third_party_hosted": "第三方托管",
    "first_party": "原厂",
}

UNIT_LABELS = {
    "CNY_per_million_tokens": "元/百万 tokens",
    "CNY_per_million_tokens_per_hour": "元/百万 tokens/小时",
    "CNY_per_10k_characters": "元/万字符",
    "CNY_per_request": "元/次",
    "USD_per_million_tokens": "美元/百万 tokens",
    "USD_per_million_tokens_per_hour": "美元/百万 tokens/小时",
}

SOURCE_STATUS_LABELS = {
    "available": "已找到",
    "not_found": "未找到匹配模型",
    "source_error": "来源解析失败",
}

SKILL_UPDATE_LABELS = {
    "updated": "已快进到远端版本",
    "up_to_date": "当前 skill 已是远端版本",
    "update_skipped": "发现远端变化，但未自动更新",
    "check_failed": "远端更新检查失败",
}

CORE_PRICE_TYPES = {"input", "output", "cache_hit", "cache_write", "cache_storage"}


def price_lookup(offer: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next(
        (item for item in offer.get("prices", []) if item.get("type") == kind), None
    )


def format_price(item: dict[str, Any] | None) -> str:
    if not item:
        return "—"
    display = item.get("display")
    amount = item.get("amount")
    unit = item.get("unit")
    if display and (
        "免费" in display
        or len(re.findall(r"\$\s*\d", display)) > 1
        or re.search(r"\b(?:through|starting)\b", display, re.I)
    ):
        return display
    if amount is None:
        return display or "—"
    current = f"{amount} {UNIT_LABELS.get(unit, unit)}"
    if item.get("list_amount") is not None:
        current += f"（原价 {item['list_amount']}）"
    return current


def format_other_prices(offer: dict[str, Any]) -> str:
    items = []
    for item in offer.get("prices", []):
        if item.get("type") in CORE_PRICE_TYPES:
            continue
        value = format_price(item)
        discount = (
            f"，discount={item['discount']}" if item.get("discount") is not None else ""
        )
        items.append(f"{item.get('label', item.get('type', '价格'))}: {value}{discount}")
    return "；".join(items) or "—"


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# 模型价格查询：`{payload.get('query', '')}`",
        "",
        f"抓取时间：{payload.get('retrieved_at', '')}",
        "",
    ]
    results = payload.get("results", [])
    if results:
        lines.extend(
            [
                "| 厂商 | 模型/版本 | 服务方式 | 地域 | 计费条件 | 输入 | 输出 | 缓存命中 | 缓存写入/存储 | 其他价格 | 来源 |",
                "|---|---|---|---|---|---:|---:|---:|---:|---|---|",
            ]
        )
        rows_written = 0
        for record in results:
            for offer in record.get("offers", []):
                conditions = offer.get("conditions", {})
                details = [offer.get("name", "标准")]
                details.extend(f"{k}={v}" for k, v in conditions.items())
                condition_text = "；".join(filter(None, details))
                lines.append(
                    "| {provider} | {model} | {delivery} | {region} | {conditions} | {input} | {output} | {cache} | {storage} | {other} | [官方来源]({source}) |".format(
                        provider=record["provider"]["name"],
                        model=f"{record.get('display_name', record['model_id'])} (`{record['model_id']}`)",
                        delivery=DELIVERY_LABELS.get(
                            record.get("delivery_mode"),
                            record.get("delivery_mode", "—"),
                        ),
                        region=record["region"],
                        conditions=condition_text.replace("|", "\\|"),
                        input=format_price(price_lookup(offer, "input")),
                        output=format_price(price_lookup(offer, "output")),
                        cache=format_price(price_lookup(offer, "cache_hit")),
                        storage=format_price(
                            price_lookup(offer, "cache_write")
                            or price_lookup(offer, "cache_storage")
                        ),
                        other=format_other_prices(offer).replace("|", "\\|"),
                        source=record["source"]["url"],
                    )
                )
                rows_written += 1
        if not rows_written:
            lines.append(
                "匹配到的模型存在，但官方文档未给出本工具可解析的价格"
                "（例如按秒/按次计费或仅在免费档提供）。"
            )
    else:
        lines.append("没有来源确认提供匹配的模型或版本。")
    lines.extend(["", "## 来源检查", ""])
    for check in payload.get("source_checks", []):
        source = check["source"]
        error = f"；{check['error']}" if check.get("error") else ""
        cache = check.get("cache")
        cache_text = (
            f"；缓存 {cache['status']}，最后拉取 {cache.get('fetched_at') or '未知'}"
            if cache
            else ""
        )
        lines.append(
            f"- {check['provider']['name']}：{SOURCE_STATUS_LABELS.get(check['status'], check['status'])}；"
            f"[{source['kind']}]({source['url']})；检查时间 {source['retrieved_at']}"
            f"{cache_text}{error}"
        )
    skill_update = payload.get("skill_update")
    if skill_update:
        revision = skill_update.get("to_revision", "")[:12]
        revision_text = f"；远端版本 `{revision}`" if revision else ""
        reason_value = str(skill_update.get("reason", "")).replace("|", "\\|")
        reason = f"；{reason_value}" if reason_value else ""
        lines.extend(
            [
                "",
                "## Skill 更新检查",
                "",
                f"- {SKILL_UPDATE_LABELS.get(skill_update['status'], skill_update['status'])}"
                f"；检查时间 {skill_update['checked_at']}{revision_text}{reason}",
            ]
        )
    return "\n".join(lines) + "\n"


def emit(payload: Any, output_format: str) -> None:
    if output_format == "markdown":
        print(to_markdown(payload), end="")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
