"""Render a payload as the message a DingTalk channel receives.

A DingTalk document is read back through DingTalk's own Markdown parser, and a
report this dense comes out of that parser wrong: a column lands under the wrong
model, a heading is swallowed, a table flattens into one line. So what is built
here is an ordinary message instead — plain characters only, hierarchy carried by
numbering and indentation, one blank line between blocks, and each source URL
written last on its line so nothing trails into the link DingTalk draws round it.

Both messages open with what the model is for rather than what it costs — a reader
who does not yet know what the model does cannot judge a price for it — and run
conclusion, detail, then summary from there.

A message also has to fit the channel it is sent through, and this module neither
measures nor shortens anything to make it fit: every block the sources support is
laid out, and ``budget`` reports by how much the result overruns. Shortening is
summarizing, which needs a model, and this tool takes no credentials — so an
over-long message leaves here as the report in full, saying that it still has to
be summarized before it goes out.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from .budget import DEFAULT_MAX_CHARS, overage
from .delta import EMPTY_SCAN, SOURCE_ERROR

from .descriptions.core import AVAILABLE as DESCRIPTION_AVAILABLE
from .diffing import (
    BASELINE_CREATED,
    BASELINE_NOT_FOUND,
    CHANGED,
    PRICE_CHANGE_FIELD,
    UNCHANGED,
)
from .reporting import (
    BULLET_CHANGE_FIELDS,
    CHANGE_FIELD_LABELS,
    DELTA_STATUS_LABELS,
    DESCRIPTION_STATUS_LABELS,
    LIFECYCLE_LABELS,
    NO_CHANGE,
    NO_SUMMARY,
    NO_WINDOW,
    SOURCE_STATUS_LABELS,
    UNKNOWN,
    UNPRICED,
    UNSTATED,
    all_unchanged,
    banded_records,
    baseline_selection_text,
    change_digest,
    changed_descriptions,
    comparison_conclusion,
    comparison_differences,
    conditions_text,
    delivery_text,
    description_source_text,
    format_moment,
    format_price,
    model_digest,
    offer_condition_text,
    offering_text,
    price_movement,
    provider_name,
    scan_conclusion,
    scan_summary,
    sentence_text,
    shared_conditions,
    skill_update_text,
    specification_text,
    summary_label,
)

COMPARISON_TITLE = "模型价格对比"
COMPARISON_SUBJECT = "{model} 在各渠道的价格与服务方式"

SCAN_TITLE = "模型价格自动检测"
SCAN_SUBJECT = "全渠道模型与计费变化"

# The order a reader wants the channels in: what needs a look first, then what is
# merely fine. Sorting is stable, so channels of one status keep the order the
# scan covered them in, and two reports stay comparable line by line.
STATUS_ORDER = (
    CHANGED,
    EMPTY_SCAN,
    SOURCE_ERROR,
    BASELINE_NOT_FOUND,
    BASELINE_CREATED,
    UNCHANGED,
)

STATUS_RANK = {status: rank for rank, status in enumerate(STATUS_ORDER)}

# Said instead of sending an over-long report as if it were complete. The tool
# neither cuts the message nor shortens it by dropping a block — a reader cannot
# tell an omitted channel from one the scan never reached — so what it states is
# the overrun and what a summary is not allowed to lose.
SUMMARIZE_NOTE = (
    "本消息 {chars} 字，超过 {limit} 字上限 {over} 字；"
    "发送前需总结压缩到 {limit} 字内，保留标题、结论、各渠道条目与全部金额"
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

    The model's own introduction opens the message, so a reader learns what the
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
        section(
            "模型介绍", description_lines(payload.get("model_descriptions", []))
        ),
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
        lines.append(field("价格", sentence_text(UNPRICED)))
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
    return bullets(
        (price_text(price) for price in offer.get("prices", [])), ITEM
    ) or [bullet(UNPRICED, ITEM)]


def price_text(price: dict[str, Any]) -> str:
    """One price as ``输入（未命中缓存）：2 元/百万 tokens``, discount included."""
    label = price.get("label") or price.get("type", "价格")
    value = format_price(price)
    if price.get("discount") is not None:
        value += f"；折扣 {price['discount']}"
    return sentence_text(f"{label}：{value}")


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
    """One entry per model, from its vendor's own introduction.

    An introduction is a property of the model rather than of a price channel, so
    a model served by five platforms is introduced once here instead of five
    times over, and its source is independent of every price source.

    This block opens both messages, so a reader learns what the model is for
    before reading what it costs.
    """
    return stacked(
        entry(
            position,
            f"{description.get('display_name') or description.get('model_id', '')}"
            f"（{description.get('model_id', '')}）",
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
        return [state, field("已检查", description_source_text(description))]
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
            field(
                "生命周期", sentence_text(LIFECYCLE_LABELS.get(lifecycle, lifecycle))
            )
        )
    if description.get("capabilities"):
        lines.append(
            field(
                "主打能力", sentence_text("、".join(description["capabilities"]))
            )
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


def scan_message(
    payload: dict[str, Any], *, max_chars: int = DEFAULT_MAX_CHARS
) -> str:
    """Render a whole-catalogue scan as the message a scheduled task sends.

    A scan that read every channel and found none of them changed collapses to
    its conclusion: a run that repeats the whole catalogue daily is one a reader
    stops reading, and the point of the message is to say it ran. Anything else
    keeps the full shape — a channel that moved, a channel that could not be
    read, or a first run with nothing to compare against — because in each of
    those cases the detail is what the message is for.
    """
    return finalize(scan_blocks(payload), max_chars)


def scan_blocks(payload: dict[str, Any]) -> list[list[str]]:
    """The blocks of a scan, in the order a reader wants them.

    A change is worth knowing about, not just countable, so every model the scan
    reports as moved is introduced once at the top: what it is for and what it can
    do, before any channel's before-and-after. One model can move on several
    channels, and its purpose does not change with the channel, so the block is
    keyed by model rather than by the change that mentioned it.
    """
    reports = payload.get("providers", [])
    comparison = baseline_selection_text(payload)
    subject = f"{SCAN_SUBJECT}（{comparison}）" if comparison else SCAN_SUBJECT
    blocks = [
        header(SCAN_TITLE, subject, payload),
        section("变化模型能力", description_lines(changed_descriptions(payload))),
        section("结论", scan_conclusion(payload)),
    ]
    if not all_unchanged(payload):
        blocks.extend(
            [
                section("渠道概览", channel_entries(reports)),
                section("变化详情", changed_blocks(reports)),
                section("未能完成的渠道", failed_lines(reports)),
                section("小结", scan_summary(payload)),
            ]
        )
    blocks.append(section("Skill 更新检查", bullets([skill_update_text(payload)])))
    return blocks


def status_rank(report: dict[str, Any]) -> int:
    return STATUS_RANK.get(report["status"], len(STATUS_ORDER))


def channel_entries(reports: list[dict[str, Any]]) -> list[str]:
    """Every scanned channel with its size, its state, and what this scan found.

    Every channel appears, including the ones that failed: a list showing only
    the interesting rows would leave the reader unable to tell a silent channel
    from one the scan never reached. A failure has no model count of its own, so
    its entry says so rather than borrowing the baseline's number.
    """
    return stacked(
        channel_entry(position, report)
        for position, report in enumerate(sorted(reports, key=status_rank), start=1)
    )


def channel_entry(position: int, report: dict[str, Any]) -> list[str]:
    updated_at = (report.get("source") or {}).get("updated_at")
    status = DELTA_STATUS_LABELS.get(report["status"], report["status"])
    model_count = report.get("model_count") or NO_CHANGE
    facts = [
        field("状态", sentence_text(status)),
        field("模型数", sentence_text(model_count)),
    ]
    if report["status"] != UNCHANGED:
        facts.append(field("本次变化", sentence_text(change_digest(report))))
    if updated_at:
        facts.append(
            field("官方更新时间", sentence_text(format_moment(updated_at)))
        )
    elif report.get("last_successful_at"):
        facts.append(
            field(
                "上次更新时间",
                sentence_text(format_moment(report["last_successful_at"])),
            )
        )
    return entry(
        position,
        report["provider"]["name"],
        facts,
    )


def changed_blocks(reports: list[dict[str, Any]]) -> list[str]:
    """The detail of every channel that moved — a channel that held still is a
    line in the overview, never a block of its own."""
    moved = [report for report in reports if report["status"] == CHANGED]
    return stacked(
        changed_entry(position, report)
        for position, report in enumerate(moved, start=1)
    )


def changed_entry(position: int, report: dict[str, Any]) -> list[str]:
    """Detail everything one channel moved.

    The changed models are not introduced here: every one of them is already in
    the message's opening block, which an introduction belongs in because it is a
    property of the model rather than of the channel that reported the change.
    """
    changes = report.get("changes") or {}
    lines = entry(
        position,
        report["provider"]["name"],
        [
            field(
                "对比基线", sentence_text(format_moment(report.get("baseline_at")))
            ),
            field(
                "本次扫描", sentence_text(f"{report.get('model_count', 0)} 个模型")
            ),
        ],
    )
    for field_name in BULLET_CHANGE_FIELDS:
        items = changes.get(field_name) or []
        lines.extend(
            group(
                f"{CHANGE_FIELD_LABELS[field_name]}（{len(items)}）",
                bullets((change_text(item) for item in items), ITEM),
            )
        )
    moves = changes.get(PRICE_CHANGE_FIELD) or []
    lines.extend(
        group(
            f"{CHANGE_FIELD_LABELS[PRICE_CHANGE_FIELD]}（{len(moves)}）",
            bullets((price_change_text(move) for move in moves), ITEM),
        )
    )
    return lines


def change_text(change: dict[str, Any]) -> str:
    """Read one change: a whole model, or one offer of a model that stayed."""
    model = f"{change.get('display_name')}（{change.get('model_id')}）"
    offer = change.get("offer")
    if offer is None:
        digest = model_digest(change)
    else:
        digest = offering_text(offer.get("name", ""), offer.get("conditions", {}))
    return sentence_text(f"{model}：{digest}" if digest else model)


def price_change_text(change: dict[str, Any]) -> str:
    """Read one price move as its model, its condition, and the move itself."""
    model = (
        f"{change.get('display_name') or change.get('model_id', '')}"
        f"（{change.get('model_id', '')}）"
    )
    condition = offering_text(change.get("offer", ""), change.get("conditions", {}))
    label = change.get("label") or change.get("type", "")
    return sentence_text(
        f"{model}：{condition}；{label} {price_movement(change)}"
    )


def failed_lines(reports: list[dict[str, Any]]) -> list[str]:
    """Name each channel that produced no catalogue, and what is kept meanwhile."""
    return bullets(
        failed_text(report)
        for report in reports
        if report["status"] in (EMPTY_SCAN, SOURCE_ERROR)
    )


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
