"""Render query payloads as JSON or as a Markdown comparison report."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from .delta import EMPTY_SCAN, SOURCE_ERROR
from .diffing import (
    BASELINE_CREATED,
    CHANGE_FIELDS,
    CHANGED,
    PRICE_CHANGE_FIELD,
    UNCHANGED,
)

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

# What a cell says when the source published nothing to put there.
NO_CHANGE = "—"
UNKNOWN = "未知"
UNSTATED = "未说明原因"


def format_moment(value: Any) -> str:
    """Render a timestamp the way a person reads it, offset included.

    The offset is spelled out because one instant is 13:37 in one region and
    05:37 in another, and this report is skimmed by eye long before it is ever
    parsed. A stamp this parser cannot read is passed through in the vendor's
    own wording rather than dropped, so nothing silently disappears.
    """
    text = str(value or "")
    if not text:
        return UNKNOWN
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    stamp = f"{moment:%Y-%m-%d %H:%M}"
    offset = moment.utcoffset()
    if offset is None:
        return stamp
    total_minutes = int(offset.total_seconds() // 60)
    sign = "-" if total_minutes < 0 else "+"
    hours, remainder = divmod(abs(total_minutes), 60)
    zone = f"UTC{sign}{hours}" + (f":{remainder:02d}" if remainder else "")
    return f"{stamp}（{zone}）"


def moment_cell(value: Any) -> str:
    """Render a timestamp inside a table, where an absent one is a dash."""
    return format_moment(value) if value else NO_CHANGE


def skill_update_section(payload: dict[str, Any]) -> list[str]:
    """Report the skill's own update check, on either kind of run.

    A scan fast-forwards the skill as part of refreshing, so the outcome of
    that check belongs in the scan report too: a run on stale code is a fact
    the reader needs, and hiding it would make the two reports disagree.
    """
    skill_update = payload.get("skill_update")
    if not skill_update:
        return []
    revision = (skill_update.get("to_revision") or "")[:12]
    revision_text = f"；远端版本 `{revision}`" if revision else ""
    reason_value = str(skill_update.get("reason", "")).replace("|", "\\|")
    reason = f"；{reason_value}" if reason_value else ""
    status = SKILL_UPDATE_LABELS.get(skill_update["status"], skill_update["status"])
    return [
        "",
        "## Skill 更新检查",
        "",
        f"- {status}；检查时间 {format_moment(skill_update.get('checked_at'))}"
        f"{revision_text}{reason}",
    ]


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
        return NO_CHANGE
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
        return display or NO_CHANGE
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
    return "；".join(items) or NO_CHANGE


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# 模型价格查询：`{payload.get('query', '')}`",
        "",
        f"抓取时间：{format_moment(payload.get('retrieved_at'))}",
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
            f"；缓存 {cache['status']}，最后拉取 "
            f"{format_moment(cache.get('fetched_at'))}"
            if cache
            else ""
        )
        lines.append(
            f"- {check['provider']['name']}：{SOURCE_STATUS_LABELS.get(check['status'], check['status'])}；"
            f"[{source['kind']}]({source['url']})；"
            f"检查时间 {format_moment(source['retrieved_at'])}"
            f"{cache_text}{error}"
        )
    lines.extend(skill_update_section(payload))
    return "\n".join(lines) + "\n"


DELTA_TITLE = "模型价格自动检测"

DELTA_STATUS_LABELS = {
    CHANGED: "有变化",
    UNCHANGED: "无变化",
    BASELINE_CREATED: "首次建立基线",
    EMPTY_SCAN: "未取到任何模型",
    SOURCE_ERROR: "来源解析失败",
}

# The order a reader wants the channels in: what needs a look first, then what
# is merely fine. Sorting is stable, so channels of one status keep the order
# the scan covered them in, and two reports stay comparable line by line.
DELTA_STATUS_ORDER = (CHANGED, EMPTY_SCAN, SOURCE_ERROR, BASELINE_CREATED, UNCHANGED)

DELTA_STATUS_RANK = {status: rank for rank, status in enumerate(DELTA_STATUS_ORDER)}

# Every change kind a channel can report, in the order a report lists them.
CHANGE_FIELD_LABELS = {
    "models_added": "新增模型",
    "models_removed": "下架模型",
    "offers_added": "新增计费方式",
    "offers_removed": "移除计费方式",
    PRICE_CHANGE_FIELD: "价格变化",
}

# The kinds detailed as bullets; price moves get the table that follows instead.
BULLET_CHANGE_FIELDS = tuple(
    field for field in CHANGE_FIELDS if field != PRICE_CHANGE_FIELD
)


def amount_text(value: dict[str, Any] | None) -> str:
    """Read a snapshot price as one comparable amount."""
    if not value:
        return NO_CHANGE
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


def scan_conclusion(payload: dict[str, Any]) -> str:
    """State the whole scan in one line, before any table is read.

    A reader who only ever sees the first screen should still learn whether
    anything moved and how much was covered, so the counts of what moved come
    before the detail and the total model count comes before the channel count.
    A run that priced nothing omits the model total rather than claiming zero.
    """
    reports = payload.get("providers", [])
    summary = payload.get("summary", {})
    total_models = sum(report.get("model_count") or 0 for report in reports)
    scope = f"{summary.get('providers', len(reports))} 个渠道"
    if total_models:
        scope += f"共 {total_models} 个模型"
    counts = [
        f"{summary.get(CHANGED, 0)} 个有变化",
        f"{summary.get(UNCHANGED, 0)} 个无变化",
    ]
    if summary.get(BASELINE_CREATED):
        counts.append(f"{summary[BASELINE_CREATED]} 个首次建立基线")
    failed = summary.get(EMPTY_SCAN, 0) + summary.get(SOURCE_ERROR, 0)
    counts.append(f"{failed} 个未能完成")
    return f"**本次结论**：{scope}：{'，'.join(counts)}。"


def status_rank(report: dict[str, Any]) -> int:
    return DELTA_STATUS_RANK.get(report["status"], len(DELTA_STATUS_ORDER))


def channel_overview(reports: list[dict[str, Any]]) -> list[str]:
    """List every scanned channel with its size and what this scan found.

    Every channel appears, including the ones that failed: a table that only
    showed the interesting rows would leave the reader unable to tell a silent
    channel from one the scan never reached. A failure has no model count of
    its own, so its cell says so rather than borrowing the baseline's number.
    """
    lines = [
        "## 各渠道模型数量",
        "",
        "| 渠道 | 模型数 | 结果 | 本次变化 | 官方更新时间 |",
        "|---|---:|---|---|---|",
    ]
    lines.extend(
        "| {name} | {count} | {status} | {change} | {updated} |".format(
            name=report["provider"]["name"].replace("|", "\\|"),
            count=report.get("model_count") or NO_CHANGE,
            status=DELTA_STATUS_LABELS.get(report["status"], report["status"]),
            change=change_digest(report).replace("|", "\\|"),
            updated=moment_cell((report.get("source") or {}).get("updated_at")),
        )
        for report in sorted(reports, key=status_rank)
    )
    return lines


def change_digest(report: dict[str, Any]) -> str:
    """Say in one cell what this channel did — or why it could not be read."""
    status = report["status"]
    if status == CHANGED:
        changes = report.get("changes") or {}
        counts = [
            f"{CHANGE_FIELD_LABELS[field]} {len(changes.get(field) or [])}"
            for field in CHANGE_FIELDS
            if changes.get(field)
        ]
        return "；".join(counts) or NO_CHANGE
    if status == BASELINE_CREATED:
        return f"记录 {report.get('model_count', 0)} 个模型，下次扫描起参与对比"
    if status in (EMPTY_SCAN, SOURCE_ERROR):
        return report.get("error") or UNSTATED
    return NO_CHANGE


def delta_to_markdown(payload: dict[str, Any]) -> str:
    """Render a whole-catalogue comparison in the order a reader scans it.

    The title, the scan time and one line of conclusion come first, so the
    answer to "did anything move" arrives before any table. The per-channel
    table then answers "which channel, how big, what moved" in a single
    screen, and only the channels that actually moved are detailed below it —
    a channel that held still is a row, not a section.
    """
    reports = payload.get("providers", [])
    lines = [
        f"# {DELTA_TITLE}",
        "",
        f"扫描时间：{format_moment(payload.get('retrieved_at'))}",
        "",
        scan_conclusion(payload),
        "",
        *channel_overview(reports),
    ]
    moved = [report for report in reports if report["status"] == CHANGED]
    if moved:
        lines.extend(["", "## 变化详情", ""])
        for position, report in enumerate(moved):
            if position:
                lines.append("")
            lines.extend(changed_provider_block(report))
    failed = [
        report for report in reports if report["status"] in (EMPTY_SCAN, SOURCE_ERROR)
    ]
    if failed:
        lines.extend(["", "## 未能完成的渠道", ""])
        for report in failed:
            lines.extend(failed_provider_line(report))
    lines.extend(skill_update_section(payload))
    return "\n".join(lines).rstrip() + "\n"


def changed_provider_block(report: dict[str, Any]) -> list[str]:
    """Detail everything one provider moved."""
    changes = report.get("changes") or {}
    lines = [
        f"### {report['provider']['name']}",
        "",
        f"上次扫描：{format_moment(report.get('baseline_at'))}；"
        f"本次扫描 {report.get('model_count', 0)} 个模型",
    ]
    for field in BULLET_CHANGE_FIELDS:
        lines.extend(
            change_bullets(CHANGE_FIELD_LABELS[field], changes.get(field) or [])
        )
    lines.extend(price_change_table(changes.get(PRICE_CHANGE_FIELD) or []))
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
        f"**{CHANGE_FIELD_LABELS[PRICE_CHANGE_FIELD]}（{len(changes)}）**",
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


def failed_provider_line(report: dict[str, Any]) -> list[str]:
    """Name a channel that produced no catalogue, and what is kept meanwhile."""
    reason = report.get("error") or UNSTATED
    baseline = report.get("baseline_at")
    kept = (
        f"；上次基线 {format_moment(baseline)} 保留，下次扫描仍与它对比"
        if baseline
        else ""
    )
    return [
        f"- **{report['provider']['name']}**："
        f"{DELTA_STATUS_LABELS.get(report['status'], report['status'])}；{reason}{kept}"
    ]


def emit(payload: Any, output_format: str, render_markdown: Any = to_markdown) -> None:
    if output_format == "markdown":
        print(render_markdown(payload), end="")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
