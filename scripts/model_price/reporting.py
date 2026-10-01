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
from decimal import Decimal, InvalidOperation
from typing import Any

from .changes import announcement_facts, changed_model_groups, lifecycle_change_kind
from .delta import EMPTY_SCAN, SOURCE_ERROR
from .descriptions.core import AVAILABLE as DESCRIPTION_AVAILABLE
from .descriptions.core import NOT_FOUND as DESCRIPTION_NOT_FOUND
from .descriptions.core import SUMMARY_MAX_CHARS
from .descriptions.core import unavailable_description
from .diffing import (
    BASELINE_CREATED,
    BASELINE_NOT_FOUND,
    CHANGE_FIELDS,
    CHANGED,
    PRICE_CHANGE_FIELD,
    UNCHANGED,
)
from .models import normalize_model
from .pricing import (
    is_free_amount,
    is_standard_offer,
    price_sort_key,
    unit_parts,
)
from .snapshots import AT_OR_BEFORE, LAST_MONTH, ON_DATE, YESTERDAY, YESTERDAY_FIRST

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

# The conditions this tool states on its own behalf, named in the language the
# report is written in. Every other key is a field name a vendor chose, and is
# printed exactly as that vendor published it: nothing at this layer knows what an
# English key a source invented meant, and a translation here would be a sentence
# the source page cannot be checked against. A key added to this map is one this
# repository named, so its wording is ours to fix.
CONDITION_LABELS = {
    "channel": "计费通道",
    "promotion_window": "活动窗口",
    "price_scope": "计价范围",
    # A cloud prices one model differently in each region it serves it from, and the
    # region a rate was read for is a term this tool states on the reader's behalf.
    "region_code": "计费区域",
    # The deployment scope a cloud bills under (Azure's Global / Data Zone Standard),
    # which is where the invoicing happens rather than what the model is; the name the
    # subscription gives that deployment; and where the inference it pays for runs.
    "deployment_scope": "部署范围",
    "deployment_name": "部署名称",
    "hosting": "托管方式",
}

# What a price is billed against, written once per measure rather than once per
# currency and measure: the code carries both, so a unit reads as "<currency>/
# <measure>" and a new currency costs one entry here.
CURRENCY_LABELS = {"CNY": "元", "USD": "美元"}
UNIT_MEASURE_LABELS = {
    "million_tokens": "百万 tokens",
    "million_tokens_per_hour": "百万 tokens/小时",
    "million_video_tokens": "百万视频 tokens",
    "thousand_tokens": "千 tokens",
    "10k_tokens": "万 tokens",
    "million_characters": "百万字符",
    "10k_characters": "万字符",
    "thousand_characters": "千字符",
    "character": "字符",
    "image": "张",
    "hundred_images": "百张",
    "frame": "帧",
    "megapixel_second": "百万像素秒",
    "second": "秒",
    "minute": "分钟",
    "hour": "小时",
    "request": "次",
    "thousand_requests": "千次",
    "10k_requests": "万次",
    "video": "视频",
    "item": "个",
    "song": "首",
    "page": "页",
}


# A price whose vendor published no unit is archived under this code rather than
# under an empty string, which would read as a unit that was lost.
UNSTATED_UNIT = "provider_defined"


def amount_with_unit(amount: Any, unit: Any) -> str:
    """One amount with the unit it was billed in, which a vendor may not state."""
    label = unit_label(unit)
    return f"{amount} {label}" if label else str(amount)


def unit_label(unit: Any) -> str:
    """Read a unit code as the unit a vendor bills in.

    A code this tool built reads as its currency and measure; anything else — a
    credit the vendor named itself, or a unit not yet read — is printed as the
    page wrote it rather than as a unit nobody published.
    """
    if str(unit or "") == UNSTATED_UNIT:
        return ""
    parts = unit_parts(unit)
    if parts:
        currency, measure = parts
        if (label := UNIT_MEASURE_LABELS.get(measure)) is not None:
            return f"{CURRENCY_LABELS.get(currency, currency)}/{label}"
    return str(unit or "")


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

MODEL_CHANGE_LABELS = {
    "models_added": "新增上架",
    "models_removed": "目录下架",
    PRICE_CHANGE_FIELD: "价格调整",
    "offers_added": "计费模式新增",
    "offers_removed": "计费模式移除",
    "replacement_updated": "替代模型更新",
    "new_notice": "退役公告新增",
    "date_revised": "退役日期更新",
    "lifecycle_status_updated": "官方状态更新",
    "lifecycle_detail_updated": "退役信息更新",
    "milestone_reached": "退役时间节点",
    "announcement_observed": "官方公布·首次收录",
    "model_announced": "新增公布",
    "access_changed": "开放状态更新",
    "announced_price_changed": "公告报价更新",
    "announcement_listing_changed": "公布模型目录状态更新",
}

ACCESS_STATUS_LABELS = {
    "unknown": "官方未明确说明",
    "pending": "尚待开放",
    "limited": "限定对象开放",
    "public": "已公开开放",
}
CATALOGUE_STATUS_LABELS = {
    "listed": "本渠道价格目录已收录",
    "not_listed": "未匹配到本渠道价格目录中的同名条目（不据此推断服务关闭）",
    "unknown": "本次价格目录未能核实",
}

CACHE_PRICE_TYPES = {"cache_hit", "cache_write", "cache_storage"}

# What a report says when the source published nothing to put in that place.
NO_CHANGE = "—"
UNKNOWN = "未知"
UNSTATED = "未说明原因"
UNPRICED = "价格未知（官方文档未给出本工具可解析的价格）"
# What a listed model with no rate at all says in place of a price. A vendor that
# publishes no charge — a model under test, a free catalogue variant, a router
# priced by whatever it routes to — has not withheld a price, so it must not read
# as one this tool could not parse.
PRICING_STATE_LABELS = {
    "free": "官方未公布可计费价格（价格为 0／免费）",
    "varies": "官方未公布固定价格（价格随路由到的模型而定）",
}
# A charge of nothing, for a vendor that published the zero without a word for it.
FREE_LABEL = "免费"
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


def format_moment(value: Any, *, preserve_seconds: bool = False) -> str:
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
    # Lifecycle clocks can specify 23:59:59. Preserve their published seconds
    # while ordinary scan/update timestamps keep the existing minute display.
    seconds = preserve_seconds and re.search(r"[T ]\d{1,2}:\d{2}:\d{2}", text)
    stamp = moment.strftime("%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M")
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
    target = selection.get("target")
    if mode == YESTERDAY:
        return "与昨天最后一次基线相比"
    if mode == YESTERDAY_FIRST:
        return "与昨天最早一次基线相比"
    if mode == LAST_MONTH:
        return "与上个月最后一次基线相比"
    if mode == ON_DATE:
        return f"与 {target} 当天最后一次基线相比"
    if mode == AT_OR_BEFORE:
        rendered = format_moment(target)
        if selection.get("uses_scan_timezone"):
            rendered += "（按本次扫描时区）"
        return f"与不晚于 {rendered} 的最后一次基线相比"
    return ""


def price_lookup(offer: dict[str, Any], kind: str) -> dict[str, Any] | None:
    return next(
        (item for item in offer.get("prices", []) if item.get("type") == kind), None
    )


def pricing_state_text(record: dict[str, Any]) -> str:
    """Why a listed model shows no price: the vendor's own state, or unknown."""
    return PRICING_STATE_LABELS.get(record.get("pricing_state"), UNPRICED)


def format_price(item: dict[str, Any] | None) -> str:
    """One amount with whatever the vendor published beside it.

    The cell's own sentence is the evidence, and it is what the report falls back to
    for a charge that is not a plain rate against a unit — a wording that states a
    free charge, or an amount the vendor equates to another. Once the numbers in that
    sentence have been read into terms, though, the terms are what is printed: they
    say the same thing in the language the report is written in, and leaving the
    sentence in their place would hide the very fields this reads.
    """
    if not item:
        return NO_CHANGE
    display = item.get("display")
    amount = item.get("amount")
    unit = item.get("unit")
    if is_free_amount(amount):
        # A charge of nothing reads as the vendor's own wording when it has one
        # ("免费", "限时免费", "Free of charge") and as 免费 when it does not,
        # never as an amount of zero next to a unit it is not billed in.
        return display or FREE_LABEL
    terms = price_terms_text(item)
    # The last day the amount applies sits against the amount rather than inside the
    # terms beside it: "原价 1.50，至 2026-12-31" reads as the list price being the
    # one that ends, which is the reverse of what a vendor dating its current rate
    # has said.
    until = item.get("effective_until") or ""
    if terms or until:
        current = amount_with_unit(amount, unit)
        if until:
            current += f" 至 {until}"
        return f"{current}（{terms}）" if terms else current
    if display and (
        "免费" in display
        or len(re.findall(r"\$\s*\d", display)) > 1
        or re.search(r"\b(?:through|starting)\b", display, re.I)
    ):
        return display
    if amount is None:
        return display or NO_CHANGE
    return amount_with_unit(amount, unit)


def price_terms_text(item: dict[str, Any]) -> str:
    """What an amount is published against, when it is published against anything.

    A vendor running a promotion prints two numbers for one charge — the rate billed
    now and the rate it gives way to — and the report shows both, so the lower one is
    never taken for the model's ordinary price. Nothing here says how long the
    reduction lasts: a period is stated only by a vendor who published one, and where
    it is stated it belongs to the amount rather than to these terms.
    """
    parts = []
    if item.get("list_amount") is not None:
        parts.append(f"原价 {item['list_amount']}")
    if (folds := discount_folds(item.get("discount"))) is not None:
        parts.append(f"{folds} 折")
    return "，".join(parts)


def discount_folds(discount: Any) -> str | None:
    """A vendor's discount multiplier as the 折 the report is written in.

    The multiplier counts tenths of the standing rate, so ``0.5`` is 5 折 and not
    0.5 折: Chinese reads the latter as five percent, which is the other end of the
    scale from the half it means. A multiplier of one is no reduction at all, and a
    value that is not a number is left to the JSON rather than guessed into a 折.
    """
    if discount is None:
        return None
    try:
        folds = Decimal(str(discount)) * 10
    except (InvalidOperation, ValueError):
        return None
    if folds <= 0 or folds >= 10:
        return None
    return format(folds.normalize(), "f")


def amount_text(value: dict[str, Any] | None) -> str:
    """Read a price stored in a snapshot as the reader sees it.

    The stored line is handed over whole, not reduced to its amount: a price that
    moved is still a price with terms, and ``6 → 4.8`` leaves a reader unable to
    tell a rate cut from a promotion that ended.
    """
    if not value:
        return NO_CHANGE
    return format_price(value)


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
    ``UNPRINTED_CONDITIONS``. A term this tool states itself is named in Chinese;
    see ``CONDITION_LABELS``.
    """
    return "；".join(
        f"{CONDITION_LABELS.get(key, key)}={value}"
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
            key: value for key, value in common.items() if conditions.get(key) == value
        }
    return common


def terms_beyond_name(name: str, conditions: dict[str, Any]) -> dict[str, Any]:
    """The terms a name does not already state.

    A vendor that files a tier in a condition column also names the offer after it
    (``batch`` carries ``service_tier=batch``, ``priority`` carries
    ``service_tier=priority``). The message prints that name as the offer's own
    heading, so repeating it as a term reads as a stutter rather than as two facts.
    """
    return {key: value for key, value in conditions.items() if str(value) != str(name)}


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
    return condition_text(head, terms_beyond_name(head, own))


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
    return condition_text(name, terms_beyond_name(name, conditions))


def description_source_text(description: dict[str, Any]) -> str:
    source = description.get("source") or {}
    if source.get("url"):
        label = source.get("name") or "官方介绍"
        return f"{label}：{source['url']}"
    attempts = description.get("attempted_sources") or []
    if attempts:
        attempt = attempts[0]
        name = attempt.get("name") or "该渠道官方介绍"
        return f"{name}：{attempt['url']}" if attempt.get("url") else name
    if url := description.get("reference_url"):
        return f"该渠道页面（未取得独立介绍）：{url}"
    return "未命中该渠道的可用介绍源"


def specification_text(specifications: dict[str, Any]) -> str:
    return "；".join(
        f"{SPECIFICATION_LABELS.get(key, key)}="
        f"{('是' if value else '否') if isinstance(value, bool) else value}"
        for key, value in (specifications or {}).items()
        if value not in (None, "", [])
    )


def changed_model_details(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Join every change to this channel's own introduction, never another's."""
    descriptions = {
        normalize_model(name): description
        for description in report.get("model_descriptions") or []
        for name in description.get("observed_model_ids") or [description["model_id"]]
    }
    details: list[dict[str, Any]] = []
    for model in changed_model_groups(report):
        description = descriptions.get(normalize_model(model["model_id"]))
        if description is None:
            description = unavailable_description(
                model["model_id"], model["display_name"]
            )
            description["reference_url"] = report.get("catalog_url") or (
                report.get("source") or {}
            ).get("url")
        details.append({**model, "description": description})
    return details


def model_title(model: dict[str, Any]) -> str:
    """Name the channel's literal ID beside its display name in every section."""
    model_id = model.get("model_id", "")
    return f"{model.get('display_name') or model_id}（{model_id}）"


def model_change_title(model: dict[str, Any]) -> str:
    """Show all causes of a model's change rather than its listing state."""
    labels = "".join(
        f"【{label}】"
        for kind, label in MODEL_CHANGE_LABELS.items()
        if kind in model["change_kinds"]
    )
    return f"{labels}{model_title(model)}"


def model_offer_changes_text(model: dict[str, Any]) -> str:
    """Name changed billing modes and conditions without quoting other tiers' prices."""
    parts = []
    for kind in ("offers_added", "offers_removed"):
        offers = list(
            dict.fromkeys(
                offering_text(
                    item["offer"].get("name", ""),
                    item["offer"].get("conditions") or {},
                )
                for item in model["changes"].get(kind) or []
            )
        )
        if offers:
            parts.append(f"{MODEL_CHANGE_LABELS[kind]}：{'、'.join(offers)}")
    return "；".join(parts)


def announcement_access_text(item: dict[str, Any]) -> str:
    """Distinguish public access from a price row, with both audiences visible."""
    access = item.get("access") or {}
    parts = [ACCESS_STATUS_LABELS.get(access.get("status", "unknown"), UNKNOWN)]
    parts.extend(
        f"{label}：{ACCESS_STATUS_LABELS.get(access.get(audience, 'unknown'), UNKNOWN)}"
        for audience, label in (("developers", "开发者"), ("consumers", "普通用户"))
    )
    return "；".join(parts)


def announcement_catalogue_text(item: dict[str, Any]) -> str:
    return CATALOGUE_STATUS_LABELS.get(item.get("catalog_status", "unknown"), UNKNOWN)


def announcement_price_text(item: dict[str, Any]) -> str:
    """Quote release rates as announcement evidence, never as today's API bill."""
    offers = item.get("announced_offers") or []
    if offers:
        state = "公告报价（保留公布时的适用条款；不代表当前可调用或当前账单）"
        parts = [
            f"公告条款 {index}："
            + "；".join(
                f"{price['label']} {format_price(price)}" for price in offer["prices"]
            )
            for index, offer in enumerate(offers, start=1)
        ]
        return f"{state}：{'；'.join(parts)}"
    if item.get("catalog_status") == "listed":
        return "公告未给出独立报价；本渠道目录价格按价格监控结果展示"
    return "公告尚无已核实报价"


def announcement_digest(report: dict[str, Any]) -> str:
    """Count release causes independently of price and retirement counts."""
    counts: dict[str, int] = {}
    for change in announcement_facts(report):
        kind = change["kind"]
        counts[kind] = counts.get(kind, 0) + 1
    return "；".join(
        f"{MODEL_CHANGE_LABELS[kind]} {count}" for kind, count in counts.items()
    )


def standard_price_changes(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Select standing price movements once for both scan sections."""
    return [
        move
        for move in (report.get("changes") or {}).get(PRICE_CHANGE_FIELD) or []
        if model_availability(report, move["model_id"]) == "listed"
        and is_standard_offer(
            {"name": move.get("offer"), "conditions": move.get("conditions")}
        )
    ]


def price_change_batch(report: dict[str, Any]) -> dict[str, Any] | None:
    """Link multi-model adjustments as one item, independently of message length.

    Full rates and conditions stay in JSON and snapshots. A single model's many
    charges or context tiers remain detailed; only distinct models form a batch.
    Without an official HTTPS page, the detailed rendering remains necessary.
    """
    moves = standard_price_changes(report)
    models = {normalize_model(move["model_id"]): move["model_id"] for move in moves}
    url = report.get("catalog_url") or (report.get("source") or {}).get("url") or ""
    if len(models) < 2 or not url.startswith("https://"):
        return None
    return {"model_ids": list(models.values()), "change_count": len(moves), "url": url}


def price_batch_text(batch: dict[str, Any]) -> str:
    """Name every affected model and leave the official details URL at line end."""
    return (
        f"【价格调整】（{len(batch['model_ids'])} 个模型，{batch['change_count']} 项标准价格变化）："
        f"{'、'.join(batch['model_ids'])}；详情查看该渠道官方页面：{batch['url']}"
    )


def model_availability(report: dict[str, Any], model_id: str) -> str:
    """Use this channel's fresh catalogue and conclusive retirement evidence."""
    key = normalize_model(model_id)
    statuses = report.get("model_availability") or {}
    return next(
        (status for name, status in statuses.items() if normalize_model(name) == key),
        "listed",
    )


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
    """Show the standard prices of a newly listed or withdrawn model."""
    offers = [offer for offer in model.get("offers", []) if is_standard_offer(offer)]
    digests = []
    for offer in offers:
        charges = "；".join(
            f"{price.get('label') or price.get('type')} {format_price(price)}"
            for price in sorted(offer.get("prices", []), key=price_sort_key)
        )
        condition = offering_text(offer.get("name", ""), offer.get("conditions", {}))
        digests.append(f"{condition} — {charges}" if charges else condition)
    return "；".join(digests)


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
        catalogue = "；".join(counts) or NO_CHANGE
    elif status == BASELINE_CREATED:
        catalogue = f"记录 {report.get('model_count', 0)} 个模型，下次扫描起参与对比"
    elif status == BASELINE_NOT_FOUND:
        catalogue = "没有符合请求时间的历史基线；本次扫描已归档"
    elif status in (EMPTY_SCAN, SOURCE_ERROR):
        catalogue = report.get("error") or UNSTATED
    else:
        catalogue = (
            "价格目录无变化"
            if (report.get("lifecycle") or {}).get("changes")
            else NO_CHANGE
        )
    return "；".join(
        filter(None, [catalogue, lifecycle_digest(report), announcement_digest(report)])
    )


def lifecycle_digest(report: dict[str, Any]) -> str:
    """Count notice changes by cause, including replacement updates."""
    counts: dict[str, int] = {}
    for change in (report.get("lifecycle") or {}).get("changes") or []:
        kind = lifecycle_change_kind(change)
        counts[kind] = counts.get(kind, 0) + 1
    return "；".join(
        f"{MODEL_CHANGE_LABELS[kind]} {count}" for kind, count in counts.items()
    )


def partial_catalogue_text(report: dict[str, Any]) -> str:
    """Keep a usable price outcome visible when the independent notice read failed."""
    digest = change_digest(report)
    detail = f"；{digest}" if digest != NO_CHANGE else ""
    return sentence_text(
        f"{report['provider']['name']}：价格目录{DELTA_STATUS_LABELS[report['status']]}{detail}"
    )


def channel_status(report: dict[str, Any]) -> str:
    """Count partial failures honestly and include notice-only changes."""
    if report["status"] in (EMPTY_SCAN, SOURCE_ERROR):
        return report["status"]
    independent = [report.get(key) or {} for key in ("lifecycle", "announcements")]
    if any(item.get("status") == SOURCE_ERROR for item in independent):
        return SOURCE_ERROR
    if any(item.get("changes") or item.get("observations") for item in independent):
        return CHANGED
    return report["status"]


def all_unchanged(payload: dict[str, Any]) -> bool:
    """Whether every channel was read and none of them moved.

    A first run is never quiet: it has no baseline, so saying nothing changed
    would claim knowledge the scan never had.
    """
    reports = payload.get("providers", [])
    return bool(reports) and all(
        report["status"] == UNCHANGED
        and (report.get("lifecycle") or {}).get("status", UNCHANGED)
        in (UNCHANGED, "no_public_schedule")
        and (report.get("announcements") or {}).get("status", UNCHANGED)
        in (UNCHANGED, "catalogue_only", "no_announcements")
        for report in reports
    )


def scan_conclusion(payload: dict[str, Any]) -> list[str]:
    """State the whole scan in one line, before any detail is read.

    A reader who only ever sees the first screen should still learn whether
    anything moved and how much was covered, so the counts of what moved come
    before the detail. A run that found no model omits the model total rather
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
    statuses = [channel_status(report) for report in reports]
    counts = [
        f"{statuses.count(CHANGED)} 个有变化",
        f"{statuses.count(UNCHANGED)} 个无变化",
    ]
    if summary.get("lifecycle_changes"):
        counts.append(f"退役公告及时间节点变化 {summary['lifecycle_changes']} 项")
    if summary.get("lifecycle_source_errors"):
        counts.append(f"退役公告读取失败 {summary['lifecycle_source_errors']} 个")
    if summary.get("announcement_changes"):
        counts.append(f"模型公布及开放变化 {summary['announcement_changes']} 项")
    if summary.get("announcement_observations"):
        counts.append(
            f"官方模型首次收录 {summary['announcement_observations']} 项（缺少所选公告历史基线）"
        )
    if summary.get("announcement_source_errors"):
        counts.append(
            f"模型发布来源读取失败 {summary['announcement_source_errors']} 个"
        )
    if statuses.count(BASELINE_CREATED):
        counts.append(f"{statuses.count(BASELINE_CREATED)} 个首次建立基线")
    if statuses.count(BASELINE_NOT_FOUND):
        counts.append(f"{statuses.count(BASELINE_NOT_FOUND)} 个未找到匹配基线")
    failed = statuses.count(EMPTY_SCAN) + statuses.count(SOURCE_ERROR)
    counts.append(f"{failed} 个未能完成")
    return [f"{scope}：{'，'.join(counts)}。"]


LIFECYCLE_MILESTONE_LABELS = {
    "announced_at": "公告日期",
    "eom_at": "停止新购",
    "redirect_at": "自动切换",
    "eos_at": "服务下线",
}

LIFECYCLE_BEHAVIOR_LABELS = {
    "redirect": "旧 ID 自动切换",
    "unavailable": "旧 ID 停止服务",
    "existing_access_continues": "存量服务继续可用",
    "unknown": "后续行为未明确",
}

LIFECYCLE_DETAIL_LABELS = {
    "replacement": "替换模型",
    "end_behavior": "旧 ID 后续行为",
    "eos_earliest": "仅最早可能下线日",
    "notice_status": "官方模型状态",
}

LIFECYCLE_NOTICE_LABELS = {
    "legacy": "官方列为旧版，未公布停服日期",
    "scheduled": "官方已预告下线",
    "retired": "官方确认已下线",
}


def lifecycle_detail_value(field: str, value: Any) -> str:
    if value is None:
        return "未公布"
    if field == "end_behavior":
        return LIFECYCLE_BEHAVIOR_LABELS.get(str(value), str(value))
    if field == "eos_earliest":
        return "是" if value else "否"
    if field == "notice_status":
        return LIFECYCLE_NOTICE_LABELS.get(str(value), str(value))
    return str(value)


def lifecycle_schedule_text(event: dict[str, Any]) -> str:
    """Describe only dates and effects that the vendor actually published."""
    parts = []
    if status := event.get("notice_status"):
        parts.append(LIFECYCLE_NOTICE_LABELS.get(status, status))
    parts.extend(
        f"{LIFECYCLE_MILESTONE_LABELS[field]} {format_moment(event[field], preserve_seconds=True)}"
        for field in LIFECYCLE_MILESTONE_LABELS
        if event.get(field)
    )
    if event.get("eos_earliest") and event.get("eos_at"):
        parts[-1] += "（最早可能日期）"
    if event.get("replacement"):
        parts.append(f"推荐/切换至 {event['replacement']}")
    behavior = event.get("end_behavior", "unknown")
    if behavior != "unknown":
        parts.append(LIFECYCLE_BEHAVIOR_LABELS.get(behavior, behavior))
    return "；".join(parts)


def lifecycle_change_text(change: dict[str, Any]) -> str:
    """Name an announcement, correction, or crossed date without guessing state."""
    event = change["event"]
    model = f"{event['model_id']}（{event['scope']}）"
    kind = change["kind"]
    if kind == "new_notice":
        return f"{model}：新增官方生命周期记录；{lifecycle_schedule_text(event)}"
    if kind == "date_revised":
        field = change["milestone"]
        old = (
            format_moment(change["before"], preserve_seconds=True)
            if change.get("before")
            else "未公布"
        )
        new = (
            format_moment(change["after"], preserve_seconds=True)
            if change.get("after")
            else "未公布"
        )
        return f"{model}：{LIFECYCLE_MILESTONE_LABELS[field]}修订，{old} → {new}"
    if kind == "milestone_reached":
        field = change["milestone"]
        label = LIFECYCLE_MILESTONE_LABELS[field]
        if (
            field == "eos_at"
            and event.get("eos_earliest")
            and event.get("notice_status") == "retired"
        ):
            label = "最早可能下线日期已到，官方已确认下线"
        elif field == "eos_at" and event.get("eos_earliest"):
            label = "最早可能下线日期已到，实际下线待官方确认"
        elif field == "eos_at" and event.get("end_behavior") == "redirect":
            label = "旧 ID 下线/自动切换日期已到"
        else:
            label += "日期已到"
        return (
            f"{model}：{label}（{format_moment(event[field], preserve_seconds=True)}）"
        )
    field = change.get("field", "详情")
    label = LIFECYCLE_DETAIL_LABELS.get(field, field)
    return f"{model}：{label}修订，{lifecycle_detail_value(field, change.get('before'))} → {lifecycle_detail_value(field, change.get('after'))}"
