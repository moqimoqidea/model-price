"""The vocabulary a report prints: labels, amounts, and moments as they are read.

Laying those values out is ``messages``' job. What lives here is shared wording
only — the Chinese name of every enum a source publishes, an amount shown in the
unit it was billed in, a timestamp spelled with the offset it was stamped in — so
the comparison and the scan say the same thing about the same fact, and a source
that publishes nothing gets named as such instead of being quietly left blank.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .delta import BASELINE_NOT_FOUND, EMPTY_SCAN, SOURCE_ERROR
from .descriptions.core import AVAILABLE as DESCRIPTION_AVAILABLE
from .descriptions.core import NOT_FOUND as DESCRIPTION_NOT_FOUND
from .descriptions.core import SUMMARY_MAX_CHARS
from .diffing import (
    BASELINE_CREATED,
    CHANGE_FIELDS,
    CHANGED,
    PRICE_CHANGE_FIELD,
    UNCHANGED,
)
from .snapshots import AT_OR_BEFORE, LAST_MONTH, ON_DATE, YESTERDAY

DELIVERY_LABELS = {
    "platform_hosted": "平台托管",
    "self_deployed": "自部署",
    "upstream_direct": "原厂直供",
    "third_party_hosted": "第三方托管",
    "first_party": "原厂",
}

# Condition terms that restate something the message already says, so printing them
# costs characters in a message that has to fit a channel and tells the reader
# nothing. ``billing_mode`` is written as ``pay_as_you_go`` on every row every
# table adapter produces, which is what a 计费方案 already is; ``source_section`` is
# where the vendor filed the row rather than how it is charged, which is why the
# snapshot layer already keeps it out of an offer's identity.
#
# They stay in the data and are filtered only here. Dropping them from the records
# would change every offer's identity against the baselines already on disk, and
# the next scan would read as every offer having been replaced.
UNPRINTED_CONDITIONS = frozenset({"billing_mode", "source_section"})

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

DESCRIPTION_STATUS_LABELS = {
    DESCRIPTION_AVAILABLE: "已找到官方介绍",
    DESCRIPTION_NOT_FOUND: "未找到官方独立介绍",
    "source_error": "介绍来源读取失败",
}

LIFECYCLE_LABELS = {
    "active": "在用",
    "preview": "预览/实验",
    "legacy": "旧版",
    "retired": "已下线",
    "unknown": "官方未说明",
}

SPECIFICATION_LABELS = {
    "context_window": "上下文窗口",
    "input_token_limit": "最大输入",
    "max_input_tokens": "最大输入",
    "output_token_limit": "最大输出",
    "max_output": "最大输出",
    "max_output_tokens": "最大输出",
    "knowledge_cutoff": "知识截止",
    "input_modalities": "输入模态",
    "output_modalities": "输出模态",
    "brand": "品牌",
    "released_at": "发布时间",
    "sunset_note": "下线提示",
    "official_direct_available": "提供原厂直供",
    "badge": "标记",
}

SKILL_UPDATE_LABELS = {
    "updated": "已快进到远端版本",
    "up_to_date": "当前 skill 已是远端版本",
    "update_skipped": "发现远端变化，但未自动更新",
    "check_failed": "远端更新检查失败",
}

DELTA_STATUS_LABELS = {
    CHANGED: "有变化",
    UNCHANGED: "无变化",
    BASELINE_CREATED: "首次建立基线",
    BASELINE_NOT_FOUND: "未找到匹配的历史基线",
    EMPTY_SCAN: "未取到任何模型",
    SOURCE_ERROR: "来源解析失败",
}

# Every change a channel can report, in the order a report lists them.
CHANGE_FIELD_LABELS = {
    "models_added": "新增模型",
    "models_removed": "下架模型",
    "offers_added": "新增计费方式",
    "offers_removed": "移除计费方式",
    PRICE_CHANGE_FIELD: "价格变化",
}

# The changes listed one per model; a price move carries more, so it is grouped
# separately by what it moved.
BULLET_CHANGE_FIELDS = tuple(
    field for field in CHANGE_FIELDS if field != PRICE_CHANGE_FIELD
)

CACHE_PRICE_TYPES = {"cache_hit", "cache_write", "cache_storage"}

# What a report says when the source published nothing to put in that place.
NO_CHANGE = "—"
UNKNOWN = "未知"
UNSTATED = "未说明原因"
UNPRICED = "官方文档未给出本工具可解析的价格"
NO_WINDOW = "官方文档未公布具体时段"
NO_SUMMARY = "官方页面未给出文字摘要"

# What an introduction's prose is filed under. A vendor's announcement can run far
# past what one message may carry, and the tool neither cuts it nor summarizes it —
# so the label carries the two facts a reader needs: how long it is, and that the
# message is not ready to send until someone has summarized it.
SUMMARY_LABEL = "用途"
SUMMARY_OVER_LIMIT_LABEL = (
    "用途（原文 {chars} 字，超过 {limit} 字上限，需先总结再发送）"
)


def sentence_text(value: Any) -> str:
    """Close one factual value so folded message lines remain readable."""
    text = str(value)
    return text if text.endswith(("。", "！", "？", ".", "!", "?")) else f"{text}。"


def summary_label(description: dict[str, Any]) -> str:
    """The label the 用途 line carries, saying so when the prose is over the limit."""
    if not description.get("summary_needs_condensing"):
        return SUMMARY_LABEL
    return SUMMARY_OVER_LIMIT_LABEL.format(
        chars=len(description.get("summary") or ""), limit=SUMMARY_MAX_CHARS
    )


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
    # Several official pages publish a date without a clock. Keep it that way:
    # rendering midnight would claim a precision the vendor never supplied.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
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


def baseline_selection_text(payload: dict[str, Any]) -> str:
    """Describe which archived scan a historical delta asks each provider for."""
    selection = payload.get("baseline_selection") or {}
    mode = selection.get("mode")
    requested = selection.get("requested")
    if mode == YESTERDAY:
        return "与昨天最后一次基线相比"
    if mode == LAST_MONTH:
        return "与上个月最后一次基线相比"
    if mode == ON_DATE:
        return f"与 {requested} 当天最后一次基线相比"
    if mode == AT_OR_BEFORE:
        rendered = format_moment(requested)
        try:
            moment = datetime.fromisoformat(str(requested).replace("Z", "+00:00"))
        except ValueError:
            moment = None
        if moment is not None and moment.tzinfo is None:
            rendered += "（按本次扫描时区）"
        return f"与不晚于 {rendered} 的最后一次基线相比"
    return ""


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


def amount_text(value: dict[str, Any] | None) -> str:
    """Read a price stored in a snapshot as one comparable amount."""
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


def provider_name(record: dict[str, Any]) -> str:
    return record.get("provider", {}).get("name", "未知渠道")


def provider_names(records: list[dict[str, Any]]) -> str:
    """Name the channels of a group once each, in the order the scan met them."""
    return "、".join(dict.fromkeys(provider_name(record) for record in records))


def delivery_text(record: dict[str, Any]) -> str:
    """Name how the model reaches the caller, in Chinese rather than as an enum."""
    mode = record.get("delivery_mode")
    return DELIVERY_LABELS.get(mode, mode or UNKNOWN)


def conditions_text(conditions: dict[str, Any]) -> str:
    """Every term a billing condition carries, as the source published it.

    Terms that restate something the message already says are left out; see
    ``UNPRINTED_CONDITIONS``.
    """
    return "；".join(
        f"{key}={value}"
        for key, value in (conditions or {}).items()
        if key not in UNPRINTED_CONDITIONS
    )


def condition_text(name: str, conditions: dict[str, Any]) -> str:
    """Describe a billing condition: what it is, then every term it carries.

    The name is quoted in the vendor's own words (高峰时段 or 忙时) and every
    other term is shown as the source published it — the adapters translate their
    enum, this layer never guesses what an English key meant.
    """
    return "；".join(filter(None, [str(name or "标准"), conditions_text(conditions)]))


def shared_conditions(record: dict[str, Any]) -> dict[str, Any]:
    """The terms every offer of one model carries — the model's, not one's own.

    A channel that bills the same way across its bands (Ark's online inference,
    billed by the same mode from the same section, whether the hour is busy or
    not) published those terms on each offer alike. They are facts about the
    model, and printing them per offer repeated them once per band.
    """
    offers = record.get("offers", [])
    if len(offers) < 2:
        return {}
    common = dict(offers[0].get("conditions") or {})
    common.pop("time_band", None)
    for offer in offers[1:]:
        conditions = offer.get("conditions") or {}
        common = {
            key: value
            for key, value in common.items()
            if conditions.get(key) == value
        }
    return common


def offer_condition_text(offer: dict[str, Any], shared: dict[str, Any]) -> str:
    """Describe one offer by the terms that tell it apart from its siblings.

    A peak/off-peak window is a fact about the model rather than about one of its
    offers: every offer of a model publishes the same hours, and the message
    prints them once per channel under 峰谷时段. Carrying them on each row
    repeated those hours as many times as the model has offers — a channel with
    two bands paid for them twice. So a row keeps the offer's own band, in the
    vendor's own words, plus whatever term this offer carries alone; a channel
    that names its band offer the same way (Aliyun's 闲时 carries
    ``time_band=闲时``) gets the band as the name rather than as a stutter.
    """
    conditions = dict(offer.get("conditions") or {})
    band = conditions.pop("time_band", None)
    head = str(band) if band else str(offer.get("name") or "标准")
    own = {key: value for key, value in conditions.items() if shared.get(key) != value}
    return condition_text(head, own)


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


def description_source_text(description: dict[str, Any]) -> str:
    source = description.get("source") or {}
    if source.get("url"):
        label = source.get("name") or "官方介绍"
        return f"{label}：{source['url']}"
    attempts = description.get("attempted_sources") or []
    names = list(
        dict.fromkeys(
            item.get("name", "") for item in attempts if item.get("name")
        )
    )
    return "、".join(names) or "未命中可用介绍源"


def specification_text(specifications: dict[str, Any]) -> str:
    return "；".join(
        f"{SPECIFICATION_LABELS.get(key, key)}="
        f"{('是' if value else '否') if isinstance(value, bool) else value}"
        for key, value in (specifications or {}).items()
        if value not in (None, "", [])
    )


def changed_descriptions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Every model a scan reports as moved, introduced once for the whole scan.

    A scan resolves introductions only for the models named in a change, so what
    this returns is exactly the set that needs one. The same model can be named by
    two channels, and what it is for does not vary by channel, so the repeats are
    dropped and the model is introduced once rather than once per channel.
    """
    seen: dict[str, dict[str, Any]] = {}
    for report in payload.get("providers", []):
        for description in report.get("model_descriptions") or []:
            key = description.get("model_id") or description.get("display_name", "")
            seen.setdefault(key, description)
    return list(seen.values())


def skill_update_text(payload: dict[str, Any]) -> str:
    """State the skill's own update check, on either kind of run.

    A scan fast-forwards the skill as part of refreshing, so the outcome of that
    check belongs in the scan report too: a run on stale code is a fact the
    reader needs, and hiding it would make the two reports disagree. A run that
    made no check has nothing to state.
    """
    skill_update = payload.get("skill_update")
    if not skill_update:
        return ""
    revision = (skill_update.get("to_revision") or "")[:12]
    revision_text = f"；远端版本 {revision}" if revision else ""
    reason = str(skill_update.get("reason", ""))
    reason_text = f"；{reason}" if reason else ""
    status = SKILL_UPDATE_LABELS.get(skill_update["status"], skill_update["status"])
    return sentence_text(
        f"{status}；检查时间 {format_moment(skill_update.get('checked_at'))}"
        f"{revision_text}{reason_text}"
    )


def banded_records(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The channels whose prices are split into peak and off-peak bands."""
    return [
        record
        for record in results
        if any(
            "time_band" in offer.get("conditions", {})
            for offer in record.get("offers", [])
        )
    ]


def cached_records(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The channels that publish a price for cached input."""
    return [
        record
        for record in results
        if any(
            price.get("type") in CACHE_PRICE_TYPES
            for offer in record.get("offers", [])
            for price in offer.get("prices", [])
        )
    ]


def comparison_conclusion(results: list[dict[str, Any]]) -> list[str]:
    """Summarize how far the comparison reached, before any price is read.

    What it covers is stated as counts — channels, channel versions, offers —
    rather than as a cheapest channel: two offers carry different billing
    conditions, so naming one of them cheapest would compare things that were
    never comparable.
    """
    if not results:
        return ["未发现由官方来源确认提供的匹配模型或版本。"]
    providers = list(dict.fromkeys(provider_name(record) for record in results))
    versions = {
        (provider_name(record), record.get("model_id", "")) for record in results
    }
    offers = sum(len(record.get("offers", [])) for record in results)
    return [
        f"本次找到 {len(providers)} 个渠道、{len(versions)} 个渠道版本、"
        f"{offers} 种计费方案。",
        "各渠道的差异集中在服务方式、峰谷时段、缓存计费与具体金额。",
    ]


def comparison_differences(results: list[dict[str, Any]]) -> list[str]:
    """Say what actually differs between the channels, dimension by dimension.

    Service mode, time bands, and cache billing are what make two offers
    incomparable, so each is named rather than left for the reader to infer from
    the amounts. Each line answers one dimension whether or not the channels
    differ in it: a dimension with nothing published says so.
    """
    if not results:
        return []
    groups: dict[str, list[str]] = {}
    for record in results:
        groups.setdefault(delivery_text(record), []).append(provider_name(record))
    delivery = "；".join(
        f"{label}（{'、'.join(dict.fromkeys(names))}）"
        for label, names in groups.items()
    )
    banded = provider_names(banded_records(results))
    cached = provider_names(cached_records(results))
    return [
        f"服务方式：{delivery}。",
        "峰谷："
        + (
            f"{banded} 发布了分时价格，各平台时段互不相同，见「峰谷时段」。"
            if banded
            else "本次结果未发现分时价格。"
        ),
        "缓存："
        + (
            f"{cached} 发布了缓存相关价格。"
            if cached
            else "本次结果未显示缓存相关价格。"
        ),
        "可比性：以上金额各自带自己的计费条件，不能脱离条件直接横向比较。",
    ]


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


def change_digest(report: dict[str, Any]) -> str:
    """Say in one phrase what a channel did — or why it could not be read."""
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
    if status == BASELINE_NOT_FOUND:
        return "没有符合请求时间的历史基线；本次扫描已归档"
    if status in (EMPTY_SCAN, SOURCE_ERROR):
        return report.get("error") or UNSTATED
    return NO_CHANGE


def all_unchanged(payload: dict[str, Any]) -> bool:
    """Whether every channel was read and none of them moved.

    A first run is never quiet: it has no baseline, so saying nothing changed
    would claim knowledge the scan never had.
    """
    reports = payload.get("providers", [])
    return bool(reports) and all(report["status"] == UNCHANGED for report in reports)


def scan_conclusion(payload: dict[str, Any]) -> list[str]:
    """State the whole scan in one line, before any detail is read.

    A reader who only ever sees the first screen should still learn whether
    anything moved and how much was covered, so the counts of what moved come
    before the detail. A run that priced nothing omits the model total rather
    than claiming zero.
    """
    reports = payload.get("providers", [])
    summary = payload.get("summary", {})
    total_models = sum(report.get("model_count") or 0 for report in reports)
    scope = f"{summary.get('providers', len(reports))} 个渠道"
    if total_models:
        scope += f"共 {total_models} 个模型"
    if all_unchanged(payload):
        return [f"{scope}，全部无变化。"]
    counts = [
        f"{summary.get(CHANGED, 0)} 个有变化",
        f"{summary.get(UNCHANGED, 0)} 个无变化",
    ]
    if summary.get(BASELINE_CREATED):
        counts.append(f"{summary[BASELINE_CREATED]} 个首次建立基线")
    if summary.get(BASELINE_NOT_FOUND):
        counts.append(f"{summary[BASELINE_NOT_FOUND]} 个未找到匹配基线")
    failed = summary.get(EMPTY_SCAN, 0) + summary.get(SOURCE_ERROR, 0)
    counts.append(f"{failed} 个未能完成")
    return [f"{scope}：{'，'.join(counts)}。"]


def scan_summary(payload: dict[str, Any]) -> list[str]:
    """Close the scan with what its per-channel states add up to.

    The overview lists every channel, so this line is where a reader learns which
    parts of it deserve a second look — what moved, what could not be read — and
    that the rest matched the selected baseline rather than being omitted.
    """
    reports = payload.get("providers", [])
    summary = payload.get("summary", {})
    failed = summary.get(EMPTY_SCAN, 0) + summary.get(SOURCE_ERROR, 0)
    parts = [f"{len(reports) - failed} 个渠道读取成功"]
    if failed:
        parts.append(f"{failed} 个未能完成，见上「未能完成的渠道」")
    if summary.get(CHANGED):
        parts.append(f"{summary[CHANGED]} 个有变化，见上「变化详情」")
    if summary.get(BASELINE_CREATED):
        parts.append(
            f"{summary[BASELINE_CREATED]} 个首次建立基线，下次扫描起参与对比"
        )
    if summary.get(BASELINE_NOT_FOUND):
        parts.append(
            f"{summary[BASELINE_NOT_FOUND]} 个未找到匹配的历史基线，"
            "本次扫描已归档"
        )
    if summary.get(UNCHANGED):
        comparison = (
            "与所选历史基线一致"
            if baseline_selection_text(payload)
            else "与上次扫描一致"
        )
        parts.append(f"{summary[UNCHANGED]} 个无变化，{comparison}，不再展开")
    return ["；".join(parts) + "。"]
