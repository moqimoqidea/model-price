"""Render query payloads as JSON or as a Markdown comparison report."""

from __future__ import annotations

import json
import re
from typing import Any

from .delta import EMPTY_SCAN, SOURCE_ERROR
from .diffing import BASELINE_CREATED, CHANGED, UNCHANGED

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


def condition_text(name: str, conditions: dict[str, Any], window: str = "") -> str:
    """Describe a billing condition, always with the time window it covers.

    A peak/off-peak row is meaningless without the hours it covers, and every
    platform draws those hours differently, so the window the vendor published is
    repeated on the row rather than left to a footnote. The band itself is quoted
    in the vendor's own words (高峰时段 or 忙时) — the adapters translate their
    enum, this layer never guesses what an English key meant.
    """
    details = [str(name or "标准")]
    details.extend(f"{key}={value}" for key, value in (conditions or {}).items())
    if window and "time_band" in (conditions or {}):
        details.append(f"时段规则={window}")
    return "；".join(filter(None, details))


def offer_condition_text(offer: dict[str, Any], record: dict[str, Any]) -> str:
    """Describe one offer, carrying the record's own published window."""
    window = (record.get("time_bands") or {}).get("window", "")
    return condition_text(
        offer.get("name", "标准"), offer.get("conditions", {}), window
    )


def time_band_sections(results: list[dict[str, Any]]) -> list[str]:
    """List every platform's peak/off-peak window, quoted from its own document."""
    billed = [
        record
        for record in results
        if any(
            "time_band" in offer.get("conditions", {})
            for offer in record.get("offers", [])
        )
    ]
    if not billed:
        return []
    lines = ["", "## 峰谷时段（各平台规则不同，按官方原文）", ""]
    for record in billed:
        bands = record.get("time_bands") or {}
        name = record.get("display_name", record["model_id"])
        lines.append(
            f"- **{record['provider']['name']}**（{name}）："
            f"{bands.get('window') or '官方文档未公布具体时段'}"
        )
        for statement in bands.get("statements", []):
            lines.append(f"  - 官方原文：{statement}")
        if bands.get("source_url"):
            lines.append(f"  - 时段来源：{bands['source_url']}")
    return lines


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
                condition_text = offer_condition_text(offer, record)
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
    lines.extend(time_band_sections(results))
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


DELTA_STATUS_LABELS = {
    "changed": "有变化",
    "unchanged": "无变化",
    "baseline_created": "首次建立基线",
    "empty_scan": "未取到任何模型",
    "source_error": "来源解析失败",
}

CHANGE_HEADINGS = (
    ("models_added", "新增模型"),
    ("models_removed", "下架模型"),
    ("offers_added", "新增计费方式"),
    ("offers_removed", "移除计费方式"),
)


def amount_text(value: dict[str, Any] | None) -> str:
    """Read a snapshot price as one comparable amount."""
    if not value:
        return "—"
    return format_price({"amount": value.get("amount"), "unit": value.get("unit")})


def price_movement(change: dict[str, Any]) -> str:
    """Read one price change as newly billed, withdrawn, or moved."""
    if change.get("from") is None:
        return f"新增 {amount_text(change.get('to'))}"
    if change.get("to") is None:
        return f"已移除（原为 {amount_text(change.get('from'))}）"
    return f"{amount_text(change.get('from'))} → {amount_text(change.get('to'))}"


def offering_text(name: str, conditions: dict[str, Any]) -> str:
    """Name an offer in the vendor's own words.

    Two things are worth avoiding here. A vendor that files a band in a condition
    column also names the offer after it (Aliyun's offer 闲时 carries
    ``time_band=闲时``), and printing both reads as a stutter rather than as two
    facts. An adapter that instead names a band offer with its own API enum
    (``off_peak``) is repeating the vendor's wording one field away — and the
    wording is what a reader can match against the page, so it is shown in place
    of the enum. A name that is already the vendor's own wording (阿里云的「闲时」)
    is left alone.
    """
    conditions = conditions or {}
    band = conditions.get("time_band")
    if band is not None and name.isascii():
        return str(band)
    trimmed = {key: value for key, value in conditions.items() if str(value) != name}
    return condition_text(name, trimmed)


def model_digest(model: dict[str, Any]) -> str:
    """Name what a newly listed model charges, from its first published offer."""
    offers = model.get("offers", [])
    if not offers:
        return ""
    head = offers[0]
    charges = "；".join(
        f"{price.get('label') or price.get('type')} {format_price(price)}"
        for price in head.get("prices", [])
    )
    condition = offering_text(head.get("name", ""), head.get("conditions", {}))
    digest = f"{condition} — {charges}" if charges else condition
    remaining = len(offers) - 1
    return f"{digest}（另有 {remaining} 种计费方式）" if remaining else digest


def delta_to_markdown(payload: dict[str, Any]) -> str:
    """Render a whole-catalogue comparison, saying plainly where nothing moved."""
    reports = payload.get("providers", [])
    summary = payload.get("summary", {})
    lines = [
        "# 模型价格变化对比",
        "",
        f"扫描时间：{payload.get('retrieved_at', '')}",
        "",
        "| 结果 | 渠道数 |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {DELTA_STATUS_LABELS[status]} | {summary.get(status, 0)} |"
        for status in DELTA_STATUS_LABELS
    )
    for title, statuses, render in (
        ("有变化的渠道", (CHANGED,), changed_provider_block),
        ("无变化的渠道", (UNCHANGED,), quiet_provider_line),
        ("首次建立基线的渠道", (BASELINE_CREATED,), baseline_provider_line),
        ("未能完成的渠道", (EMPTY_SCAN, SOURCE_ERROR), failed_provider_line),
    ):
        selected = [report for report in reports if report["status"] in statuses]
        if not selected:
            continue
        lines.extend(["", f"## {title}", ""])
        for report in selected:
            lines.extend(render(report))
    return "\n".join(lines).rstrip() + "\n"


def changed_provider_block(report: dict[str, Any]) -> list[str]:
    """Detail everything one provider moved."""
    changes = report.get("changes") or {}
    lines = [
        f"### {report['provider']['name']}",
        "",
        f"上次扫描：{report.get('baseline_at') or '未知'}；"
        f"本次扫描 {report.get('model_count', 0)} 个模型",
    ]
    for field, heading in CHANGE_HEADINGS:
        lines.extend(change_bullets(heading, changes.get(field) or []))
    lines.extend(price_change_table(changes.get("price_changes") or []))
    return lines


def change_bullets(heading: str, items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return []
    return ["", f"**{heading}（{len(items)}）**", "", *map(change_bullet, items)]


def change_bullet(change: dict[str, Any]) -> str:
    """Read one change: a whole model, or one offer of a model that stayed."""
    model = f"**{change.get('display_name')}** (`{change.get('model_id')}`)"
    offer = change.get("offer")
    if offer is None:
        digest = model_digest(change)
    else:
        digest = offering_text(offer.get("name", ""), offer.get("conditions", {}))
    return f"- {model}：{digest}" if digest else f"- {model}"


def price_change_table(changes: list[dict[str, Any]]) -> list[str]:
    if not changes:
        return []
    lines = [
        "",
        f"**价格变化（{len(changes)}）**",
        "",
        "| 模型 | 计费条件 | 价格项 | 变化 |",
        "|---|---|---|---|",
    ]
    lines.extend(
        "| `{model}` | {condition} | {label} | {movement} |".format(
            model=change.get("model_id", ""),
            condition=offering_text(
                change.get("offer", ""), change.get("conditions", {})
            ).replace("|", "\\|"),
            label=change.get("label") or change.get("type", ""),
            movement=price_movement(change),
        )
        for change in changes
    )
    return lines


def quiet_provider_line(report: dict[str, Any]) -> list[str]:
    """Say a provider did not move, and on whose word."""
    stamp = (report.get("source") or {}).get("updated_at")
    detail = f"共 {report.get('model_count', 0)} 个模型"
    if stamp:
        detail += f"；官方标注更新时间 {stamp}"
    return [f"- **{report['provider']['name']}**：模型无变化（{detail}）"]


def baseline_provider_line(report: dict[str, Any]) -> list[str]:
    return [
        f"- **{report['provider']['name']}**：已记录 {report.get('model_count', 0)} 个模型，"
        "下次扫描起参与对比"
    ]


def failed_provider_line(report: dict[str, Any]) -> list[str]:
    reason = report.get("error") or "未说明原因"
    baseline = report.get("baseline_at")
    kept = f"；上次基线 {baseline} 保留" if baseline else ""
    return [
        f"- **{report['provider']['name']}**："
        f"{DELTA_STATUS_LABELS.get(report['status'], report['status'])}；{reason}{kept}"
    ]


def emit(payload: Any, output_format: str, render_markdown: Any = to_markdown) -> None:
    if output_format == "markdown":
        print(render_markdown(payload), end="")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
