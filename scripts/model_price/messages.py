"""Render a payload as the message a DingTalk channel receives.

A DingTalk document is read back through DingTalk's own Markdown parser, and a
report this dense comes out of that parser wrong: a column lands under the wrong
model, a heading is swallowed, a table flattens into one line. So what is built
here is an ordinary message instead — plain characters only, hierarchy carried by
numbering and indentation, one blank line between blocks, and each source URL
written last on its line so nothing trails into the link DingTalk draws round it.

Both messages open with what the model is for rather than what it costs. A scan
then shows standard prices before closing with the channel outcomes.

A message also has to fit the channel it is sent through, and this module neither
measures nor shortens anything to make it fit: every selected block is laid out,
and ``budget`` reports by how much the result overruns. Shortening is
summarizing, which needs a model, and this tool takes no credentials — so an
over-long message leaves here as the report in full, saying that it still has to
be summarized before it goes out.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from .budget import DEFAULT_MAX_CHARS, overage
from .changes import announcement_facts
from .delta import EMPTY_SCAN, SOURCE_ERROR

from .descriptions.core import AVAILABLE as DESCRIPTION_AVAILABLE
from .models import normalize_model
from .pricing import is_standard_offer
from .diffing import (
    BASELINE_CREATED,
    BASELINE_NOT_FOUND,
    CHANGED,
    CHANGE_FIELDS,
    PRICE_CHANGE_FIELD,
    UNCHANGED,
)
from .reporting import (
    CHANGE_FIELD_LABELS,
    DELTA_STATUS_LABELS,
    DESCRIPTION_STATUS_LABELS,
    LIFECYCLE_LABELS,
    NO_SUMMARY,
    NO_WINDOW,
    SOURCE_STATUS_LABELS,
    UNKNOWN,
    UNPRICED,
    UNSTATED,
    banded_records,
    announcement_access_text,
    announcement_catalogue_text,
    announcement_digest,
    announcement_price_text,
    baseline_selection_text,
    channel_status,
    change_digest,
    changed_model_details,
    comparison_conclusion,
    comparison_differences,
    conditions_text,
    delivery_text,
    description_source_text,
    format_moment,
    format_price,
    lifecycle_change_text,
    lifecycle_digest,
    model_change_title,
    model_digest,
    model_availability,
    model_offer_changes_text,
    model_title,
    offer_condition_text,
    offering_text,
    partial_catalogue_text,
    price_movement,
    price_batch_text,
    price_change_batch,
    pricing_state_text,
    provider_name,
    scan_conclusion,
    sentence_text,
    shared_conditions,
    skill_update_text,
    specification_text,
    standard_price_changes,
    summary_label,
)

COMPARISON_TITLE = "模型价格对比"
COMPARISON_SUBJECT = "{model} 在各渠道的价格与服务方式"

SCAN_TITLE = "模型价格自动检测"
SCAN_SUBJECT = "全渠道模型与计费变化"

# Said instead of sending an over-long report as if it were complete. The tool
# neither cuts the message nor shortens it by dropping a block — a reader cannot
# tell an omitted channel from one the scan never reached — so what it states is
# the overrun and what a summary is not allowed to lose.
SUMMARIZE_NOTE = (
    "本消息 {chars} 字，超过 {limit} 字上限 {over} 字；"
    "发送前需总结压缩到 {limit} 字内，保留标题、渠道状态与已展示的全部金额"
)

# One step of hierarchy. A message is read on a phone, so a level is added by
# indentation rather than by a heading syntax something downstream might re-render.
INDENT = "   "

# The three levels a message uses: a list directly under a section heading, a
# labelled fact about the entry above it, and a list inside that entry.
SECTION = 0
FIELD = 1
ITEM = 2


def field(label: str, value: Any) -> str:
    """One labelled fact about the entry above it."""
    return f"{INDENT * FIELD}{label}：{value}"


def bullet(text: str, depth: int = SECTION) -> str:
    """One item of a list, indented to the level it belongs to."""
    return f"{INDENT * depth}- {text}"


def bullets(texts: Iterable[str], depth: int = SECTION) -> list[str]:
    """The items of a list, leaving out the ones with nothing to say."""
    return [bullet(text, depth) for text in texts if text]


def note(text: str, depth: int = FIELD) -> str:
    """A line continuing the item above it rather than being another item."""
    return f"{INDENT * depth}{text}"


def entry(position: int, title: str, fields: Iterable[str] = ()) -> list[str]:
    """A numbered entry: what it is, then the labelled facts ``field`` wrote."""
    return [f"{position}. {sentence_text(title)}", *fields]


def group(heading: str, lines: Sequence[str]) -> list[str]:
    """A heading and the lines under it, inside one entry."""
    if not lines:
        return []
    return [note(heading), *lines]


def section(heading: str, body: Sequence[str]) -> list[str]:
    """A headed block. A section with nothing to say is left out of the message."""
    if not body:
        return []
    return [f"【{heading}】", "", *body]


def stacked(blocks: Iterable[Sequence[str]]) -> list[str]:
    """Stack blocks one blank line apart, skipping the ones with nothing to say."""
    lines: list[str] = []
    for block in blocks:
        content = list(block)
        if not content:
            continue
        if lines:
            lines.append("")
        lines.extend(content)
    return lines


def header(title: str, subject: str, payload: dict[str, Any]) -> list[str]:
    """Open the message with what it is, when it ran, and what it covers."""
    return [
        title,
        f"时间：{sentence_text(format_moment(payload.get('retrieved_at')))}",
        f"主题：{sentence_text(subject)}",
    ]


def finalize(blocks: Iterable[Sequence[str]], max_chars: int) -> str:
    """Lay the blocks out, and say so when the result is too long to send.

    Nothing is dropped to make the message fit. A report that fits because a block
    was left out reports on less than the scan covered, and the reader cannot tell
    an omitted block from one that was never there — so an over-long message is
    the report in full, carrying the fact that it still has to be summarized.
    """
    lines = stacked(blocks)
    text = "\n".join(lines) + "\n"
    over = overage(text, max_chars)
    if not over:
        return text
    closing = SUMMARIZE_NOTE.format(chars=len(text), limit=max_chars, over=over)
    return "\n".join(stacked([lines, [closing]])) + "\n"


def comparison_message(
    payload: dict[str, Any], *, max_chars: int = DEFAULT_MAX_CHARS
) -> str:
    """Render a model comparison as the message a channel receives.

    Each channel's introduction opens the message, so a reader learns what the
    model is for before reading what it costs. The conclusion, the channels, and
    what differs between them follow in that order, so the first screen still
    answers what the comparison covers and where the channels part company. The
    peak and off-peak wording and the per-channel sources close it.
    """
    return finalize(comparison_blocks(payload), max_chars)


def comparison_blocks(payload: dict[str, Any]) -> list[list[str]]:
    """The blocks of a comparison, in the order a reader wants them.

    What the model is for comes first: it is the question the comparison is asked
    on behalf of, and a reader who does not know what the model does cannot judge
    a price for it.
    """
    results = payload.get("results", [])
    subject = COMPARISON_SUBJECT.format(model=payload.get("query", ""))
    return [
        header(COMPARISON_TITLE, subject, payload),
        section("模型介绍", description_lines(payload.get("model_descriptions", []))),
        section("结论", comparison_conclusion(results)),
        section("渠道对比", provider_entries(results)),
        section("差异总结", bullets(comparison_differences(results))),
        section("峰谷时段", band_lines(results)),
        section("来源检查", source_lines(payload.get("source_checks", []), results)),
        section("Skill 更新检查", bullets([skill_update_text(payload)])),
    ]


def provider_entries(results: list[dict[str, Any]]) -> list[str]:
    """One entry per channel: how it serves the model, what it charges, from where."""
    return stacked(
        provider_entry(position, record)
        for position, record in enumerate(results, start=1)
    )


def provider_entry(position: int, record: dict[str, Any]) -> list[str]:
    """One entry per channel: how it serves the model, what it charges, from where.

    The entry keeps the channel, the model version it sells, every amount with the
    terms it is billed under, and the page all of it was read from. What a summary
    of an over-long message may condense is the wording — never an amount, and
    never the channel it belongs to.
    """
    display_name = record.get("display_name") or record.get("model_id", "")
    offers = record.get("offers", [])
    lines = entry(
        position,
        f"{provider_name(record)}｜{display_name}",
        [
            field("模型", sentence_text(record.get("model_id", ""))),
            field("服务方式", sentence_text(delivery_text(record))),
            field("地域", sentence_text(record.get("region") or UNKNOWN)),
        ],
    )
    if not offers:
        lines.append(field("价格", sentence_text(pricing_state_text(record))))
    lines.extend(price_note_lines(record))
    shared = shared_conditions(record)
    # Tested after filtering, not before: a channel whose shared terms are all
    # unprinted ones has nothing to state here, and an empty label reads as a
    # value the source withheld.
    if shared_text := conditions_text(shared):
        lines.append(field("计费条件", sentence_text(shared_text)))
    for offer_position, offer in enumerate(offers, start=1):
        terms = offer_condition_text(offer, shared)
        lines.append(field(f"计费方案 {offer_position}", sentence_text(terms)))
        lines.extend(price_lines(offer))
    source = record.get("source") or {}
    lines.append(field("来源", source.get("url") or UNKNOWN))
    return lines


def price_lines(offer: dict[str, Any]) -> list[str]:
    """Every amount an offer publishes, in the unit it was billed in.

    Each keeps its own label rather than being fitted to a column: a channel that
    bills cached input, or splits by input length, publishes more prices than a
    fixed set of columns could hold without dropping one.
    """
    return bullets((price_text(price) for price in offer.get("prices", [])), ITEM) or [
        bullet(UNPRICED, ITEM)
    ]


def price_text(price: dict[str, Any]) -> str:
    """One price as ``输入（未命中缓存）：2 元/百万 tokens``."""
    label = price.get("label") or price.get("type", "价格")
    return sentence_text(f"{label}：{format_price(price)}")


def price_note_lines(record: dict[str, Any]) -> list[str]:
    """What the vendor says about a model's price, in the vendor's own words.

    A vendor that announces a price change in prose keeps the wording a reader needs
    — "at least through November 21, 2026" says something an end date would not — so
    the sentence is quoted rather than reduced to a term, and it is quoted beside the
    prices it explains rather than in a section of its own.
    """
    return [
        field("价格说明", sentence_text(note))
        for note in record.get("pricing_notes") or []
    ]


def band_lines(results: list[dict[str, Any]]) -> list[str]:
    """Each platform's peak/off-peak window, quoted from that platform's document.

    The window is repeated here as well as on the price row because no platform
    shares another's hours: what Aliyun discounts overnight, Ark bills as usual,
    and a reader comparing the two needs both statements in front of them.
    """
    blocks = []
    for record in banded_records(results):
        bands = record.get("time_bands") or {}
        name = record.get("display_name", record.get("model_id", ""))
        block = [
            bullet(
                f"{provider_name(record)}（{name}）："
                f"{sentence_text(bands.get('window') or NO_WINDOW)}"
            )
        ]
        block.extend(
            note(f"官方原文：{sentence_text(statement)}")
            for statement in bands.get("statements", [])
        )
        if bands.get("source_url"):
            block.append(note(f"时段来源：{bands['source_url']}"))
        blocks.append(block)
    return stacked(blocks)


def description_lines(descriptions: list[dict[str, Any]]) -> list[str]:
    """Introduce each channel's model before its price, keeping sources separate."""
    return stacked(
        entry(
            position,
            (f"{provider_name(description)}｜" if description.get("provider") else "")
            + model_title(description),
            description_fields(description),
        )
        for position, description in enumerate(descriptions, start=1)
    )


def description_fields(description: dict[str, Any]) -> list[str]:
    """What an introduction states, or why there was none to state.

    A vendor's announcement can run past what one message may carry, and it is
    never cut to fit: the label says how long it is and that it still has to be
    summarized, and the prose below it is the vendor's own, whole.
    """
    if description.get("status") != DESCRIPTION_AVAILABLE:
        status = DESCRIPTION_STATUS_LABELS.get(
            description.get("status"), description.get("status", UNKNOWN)
        )
        note_text = description.get("note")
        state = field(
            "状态", sentence_text(f"{status}；{note_text}" if note_text else status)
        )
        return [state, field("介绍来源", description_source_text(description))]
    lifecycle = description.get("lifecycle", "unknown")
    lines = [
        field(
            summary_label(description),
            sentence_text(description.get("summary") or NO_SUMMARY),
        ),
    ]
    # A newly listed model being active is implicit. Exceptional states remain
    # visible because preview, legacy, retirement, and an unknown state affect a
    # reader's decision even when the price source still lists the model.
    if lifecycle != "active":
        lines.append(
            field("生命周期", sentence_text(LIFECYCLE_LABELS.get(lifecycle, lifecycle)))
        )
    if description.get("capabilities"):
        lines.append(
            field("主打能力", sentence_text("、".join(description["capabilities"])))
        )
    if specs := specification_text(description.get("specifications") or {}):
        lines.append(field("规格", sentence_text(specs)))
    lines.append(field("来源", description_source_text(description)))
    return lines


def source_lines(
    checks: list[dict[str, Any]], results: list[dict[str, Any]]
) -> list[str]:
    """One line per channel: whether it answered, and the page it answered from.

    A channel that returned records has already had its page printed under its own
    entry above, and this message only holds so much, so that page is not printed
    a second time — the line says the channel answered and points back. A channel
    that answered with no match is the one whose page a reader still has to open,
    so it keeps its URL. The URL is written last so nothing trails into the link
    DingTalk draws round it; the cache state and the check time the report once
    carried are left out because this message is read on a phone rather than
    filed, and a channel that did not answer still says why.
    """
    printed = {(record.get("source") or {}).get("url") for record in results}
    return [bullet(source_line_text(check, printed)) for check in checks]


def source_line_text(check: dict[str, Any], printed: set[str | None]) -> str:
    """Read one source check, repeating its page only if no entry showed it."""
    name = check["provider"]["name"]
    status = source_status_text(check)
    url = (check.get("source") or {}).get("url")
    if url and url in printed:
        return sentence_text(f"{name}：{status}（来源见上）")
    return f"{name}：{status}｜{url or UNKNOWN}"


def source_status_text(check: dict[str, Any]) -> str:
    """The outcome of one source check, with its reason when it failed."""
    status = SOURCE_STATUS_LABELS.get(check["status"], check["status"])
    error = check.get("error")
    return f"{status}（{error}）" if error else status


def scan_message(payload: dict[str, Any], *, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Render a scan with model capabilities, standard prices, and outcomes."""
    return finalize(scan_blocks(payload), max_chars)


def scan_blocks(payload: dict[str, Any]) -> list[list[str]]:
    """Group capabilities and notice changes by channel, then prices and outcomes."""
    reports = payload.get("providers", [])
    comparison = baseline_selection_text(payload)
    subject = f"{SCAN_SUBJECT}（{comparison}）" if comparison else SCAN_SUBJECT
    return [
        header(SCAN_TITLE, subject, payload),
        section("模型能力", scan_capability_lines(reports)),
        section("模型价格", changed_blocks(reports)),
        section("渠道结论", channel_conclusion(payload)),
    ]


def channel_conclusion(payload: dict[str, Any]) -> list[str]:
    """Count the scan and name every channel without a second overview."""
    reports = payload.get("providers", [])
    lines = scan_conclusion(payload)
    for status in (CHANGED, UNCHANGED, BASELINE_CREATED, BASELINE_NOT_FOUND):
        matching = [report for report in reports if channel_status(report) == status]
        if not matching:
            continue
        label = DELTA_STATUS_LABELS[status]
        if status == UNCHANGED:
            lines.append(
                f"{label}：{'、'.join(report['provider']['name'] for report in matching)}。"
            )
        else:
            lines.extend(
                f"{report['provider']['name']}：{label}；{change_digest(report)}。"
                for report in matching
            )
    lines.extend(
        failed_text(report)
        for report in reports
        if report["status"] in (EMPTY_SCAN, SOURCE_ERROR)
    )
    lines.extend(
        partial_catalogue_text(report)
        for report in reports
        if channel_status(report) == SOURCE_ERROR
        and report["status"] not in (EMPTY_SCAN, SOURCE_ERROR)
    )
    for report in reports:
        lifecycle = report.get("lifecycle") or {}
        status = lifecycle.get("status")
        if status == SOURCE_ERROR:
            lines.append(
                sentence_text(
                    f"{report['provider']['name']} 退役公告：读取失败；{lifecycle.get('error') or UNSTATED}；历史记录保留"
                )
            )
        elif status == "no_public_schedule":
            lines.append(
                sentence_text(
                    f"{report['provider']['name']} 退役公告：暂无可核实的公开逐模型时间表"
                )
            )
        elif status in (BASELINE_CREATED, BASELINE_NOT_FOUND):
            lines.append(
                sentence_text(
                    f"{report['provider']['name']} 退役公告：{DELTA_STATUS_LABELS[status]}；记录 {lifecycle.get('event_count', 0)} 项"
                )
            )
        elif status == CHANGED and report["status"] in (EMPTY_SCAN, SOURCE_ERROR):
            lines.append(
                sentence_text(
                    f"{report['provider']['name']} 退役公告：{lifecycle_digest(report)}"
                )
            )
        announcements = report.get("announcements") or {}
        if announcements.get("status") == SOURCE_ERROR:
            lines.append(
                sentence_text(
                    f"{report['provider']['name']} 模型发布来源：读取失败；{announcements.get('error') or UNSTATED}；历史记录保留"
                )
            )
        elif announcements.get("status") == "catalogue_only":
            lines.append(
                sentence_text(
                    f"{report['provider']['name']} 模型发布来源：{announcements.get('note') or UNSTATED}"
                )
            )
        elif announcements.get("status") == BASELINE_NOT_FOUND:
            lines.append(
                sentence_text(
                    f"{report['provider']['name']} 模型发布来源：{DELTA_STATUS_LABELS[BASELINE_NOT_FOUND]}；本次发现已归档"
                )
            )
        elif announcement_facts(report) and channel_status(report) == SOURCE_ERROR:
            lines.append(
                sentence_text(
                    f"{report['provider']['name']} 模型发布来源：{announcement_digest(report)}"
                )
            )
    baselines = {
        report.get("baseline_at") for report in reports if report.get("baseline_at")
    }
    if len(baselines) == 1:
        lines.append(f"对比基线：{format_moment(baselines.pop())}。")
    elif baselines:
        lines.extend(
            f"{report['provider']['name']} 对比基线：{format_moment(report['baseline_at'])}。"
            for report in reports
            if report.get("baseline_at")
        )
    return lines


def scan_capability_lines(reports: list[dict[str, Any]]) -> list[str]:
    """Show all causes per channel model, with one item for bulk price moves."""
    blocks = []
    for report in reports:
        batch = price_change_batch(report)
        batched_ids = (
            {normalize_model(name) for name in batch["model_ids"]} if batch else set()
        )
        details = []
        for model in changed_model_details(report):
            if (
                model["change_kinds"] == [PRICE_CHANGE_FIELD]
                and normalize_model(model["model_id"]) in batched_ids
            ):
                continue
            details.extend(scan_model_entry(model))
        if batch:
            details.append(bullet(price_batch_text(batch), FIELD))
        if details:
            blocks.append(entry(len(blocks) + 1, report["provider"]["name"], details))
    return stacked(blocks)


def scan_model_entry(model: dict[str, Any]) -> list[str]:
    """Keep simultaneous billing and notice changes beside the correct introduction."""
    fields: list[str] = []
    if text := model_offer_changes_text(model):
        fields.append(field("计费变化", sentence_text(text)))
    fields.extend(description_fields(model["description"]))
    announcements = {
        change["event"]["source_url"]: change["event"]
        for change in model["announcement_changes"]
    }
    if item := model.get("announcement"):
        announcements.setdefault(item["source_url"], item)
    for item in announcements.values():
        if item.get("published_at"):
            fields.append(field("官方公布时间", format_moment(item["published_at"])))
        fields.append(
            field("公告开放说明", sentence_text(announcement_access_text(item)))
        )
        fields.append(
            field("目录状态", sentence_text(announcement_catalogue_text(item)))
        )
        fields.extend(
            field("开放原文", sentence_text(statement))
            for statement in item["access"]["statements"]
        )
        fields.append(field("发布来源", item["source_url"]))
    for change in model["lifecycle_changes"]:
        fields.append(field("公告变化", sentence_text(lifecycle_change_text(change))))
    urls = dict.fromkeys(
        change["event"]["source_url"] for change in model["lifecycle_changes"]
    )
    fields.extend(field("公告来源", url) for url in urls)
    return [
        bullet(sentence_text(model_change_title(model)), FIELD),
        *(f"{INDENT}{line}" for line in fields),
    ]


def changed_blocks(reports: list[dict[str, Any]]) -> list[str]:
    """Standard prices for models and offers that changed."""
    blocks = []
    for report in reports:
        details = (
            changed_entry(len(blocks) + 1, report)
            if report["status"] == CHANGED
            else []
        )
        announced = announcement_price_lines(report)
        if details:
            blocks.append([*details, *announced])
        elif announced:
            blocks.append(entry(len(blocks) + 1, report["provider"]["name"], announced))
    return stacked(blocks)


def announcement_price_lines(report: dict[str, Any]) -> list[str]:
    """Publish announced rates separately even when no price row has appeared yet."""
    items: dict[str, dict[str, Any]] = {}
    for change in announcement_facts(report):
        item = change["event"]
        items[item["model_id"]] = item
    lines = []
    for item in items.values():
        lines.append(
            bullet(
                sentence_text(
                    f"{model_title(item)}：{announcement_price_text(item)}；{announcement_access_text(item)}"
                ),
                FIELD,
            )
        )
        lines.extend(f"{INDENT}{line}" for line in price_note_lines(item))
        lines.append(f"{INDENT}{field('公告价格来源', item['source_url'])}")
    return lines


PRICE_BULLET_CHANGE_FIELDS = tuple(
    field
    for field in CHANGE_FIELDS
    if field not in ("models_removed", PRICE_CHANGE_FIELD)
)


def changed_notes(changes: dict[str, Any]) -> list[str]:
    """What the vendor says about the price of the models this entry names.

    One model's change is reported as several bullets and each of them carries the
    same sentence, so it is taken once: repeated per bullet it would read as several
    notes saying one thing.
    """
    return list(
        dict.fromkeys(
            note
            for field_name in CHANGE_FIELDS
            for item in changes.get(field_name) or []
            for note in item.get("pricing_notes") or []
        )
    )


def changed_entry(position: int, report: dict[str, Any]) -> list[str]:
    """Show selected standard prices or a linked batch, without merging rates."""
    changes = report.get("changes") or {}
    displayed: dict[str, Any] = {}
    details: list[str] = []
    for field_name in PRICE_BULLET_CHANGE_FIELDS:
        items = changes.get(field_name) or []
        items = [
            item
            for item in items
            if model_availability(report, item.get("model_id", "")) == "listed"
        ]
        if field_name in ("offers_added", "offers_removed"):
            items = [
                item for item in items if is_standard_offer(item.get("offer") or {})
            ]
        displayed[field_name] = items
        details.extend(
            group(
                f"{CHANGE_FIELD_LABELS[field_name]}（{len(items)}）",
                bullets((change_text(item) for item in items), ITEM),
            )
        )
    moves = standard_price_changes(report)
    batch = price_change_batch(report)
    displayed[PRICE_CHANGE_FIELD] = [] if batch else moves
    details.extend(
        group(
            f"{CHANGE_FIELD_LABELS[PRICE_CHANGE_FIELD]}（{len(moves)}）",
            (
                [bullet(price_batch_text(batch), ITEM)]
                if batch
                else bullets((price_change_text(move) for move in moves), ITEM)
            ),
        )
    )
    details.extend(price_note_lines({"pricing_notes": changed_notes(displayed)}))
    return entry(position, report["provider"]["name"], details) if details else []


def change_text(change: dict[str, Any]) -> str:
    """Read one change: a whole model, or one offer of a model that stayed."""
    model = model_title(change)
    offer = change.get("offer")
    if offer is None:
        digest = model_digest(change) or UNPRICED
    else:
        digest = model_digest({"offers": [offer]})
    return sentence_text(f"{model}：{digest}" if digest else model)


def price_change_text(change: dict[str, Any]) -> str:
    """Read one price move as its model, its condition, and the move itself."""
    model = model_title(change)
    condition = offering_text(change.get("offer", ""), change.get("conditions", {}))
    label = change.get("label") or change.get("type", "")
    return sentence_text(f"{model}：{condition}；{label} {price_movement(change)}")


def failed_text(report: dict[str, Any]) -> str:
    status = DELTA_STATUS_LABELS.get(report["status"], report["status"])
    reason = report.get("error") or UNSTATED
    baseline = report.get("last_successful_at")
    kept = (
        f"；最近一次成功基线 {format_moment(baseline)} 及历史归档均保留"
        if baseline
        else ""
    )
    return sentence_text(f"{report['provider']['name']}：{status}；{reason}{kept}")
