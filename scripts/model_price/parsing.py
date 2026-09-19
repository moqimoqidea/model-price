"""Table readers for vendor documents published as HTML or Markdown."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, NamedTuple

from .models import model_family, normalize_model
from .text import clean_text, numeric_values, unescape_markdown

HEADING_TAGS = ("h1", "h2", "h3", "h4")

# Chinese vendor pages use either an ISO-like date or a spaced Chinese date after
# the same label. The markup between the label and digits varies, so callers hand
# the whole official document here instead of duplicating one fragile regex per
# provider.
UPDATE_VALUE_PATTERN = (
    r"(?P<year>\d{4})\s*(?:年\s*|[-/])"
    r"(?P<month>\d{1,2})\s*(?:月\s*|[-/])"
    r"(?P<day>\d{1,2})(?:\s*日)?"
    r"(?:\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?))?"
)
UPDATE_VALUE_RE = re.compile(UPDATE_VALUE_PATTERN)
UPDATE_STAMP_RE = re.compile(
    r"(?:最近)?更新时间\s*[：:]?\s*" + UPDATE_VALUE_PATTERN
)


def normalize_update_stamp(value: str, *, utc_offset: str = "") -> str | None:
    """Normalize one official update stamp without inventing a time of day.

    Date-only pages stay date-only. A page that states a local clock but omits its
    offset can supply the documented offset, which keeps the moment unambiguous in
    a report that also contains UTC timestamps.
    """
    match = UPDATE_VALUE_RE.search(clean_text(value).replace("T", " "))
    if not match:
        return None
    stamp = (
        f"{int(match.group('year')):04d}-"
        f"{int(match.group('month')):02d}-"
        f"{int(match.group('day')):02d}"
    )
    clock = match.group("time")
    return f"{stamp}T{clock}{utc_offset}" if clock else stamp


def document_update_stamp(document: str, *, utc_offset: str = "") -> str | None:
    """Read a labelled update date from an official HTML or Markdown document."""
    match = UPDATE_STAMP_RE.search(clean_text(document))
    if not match:
        return None
    return normalize_update_stamp(match.group(0), utc_offset=utc_offset)


class SpanGrid:
    """A table grid built from cells that cover several rows or columns.

    A vendor writes a cell spanning four rows once, but every row it covers still
    needs that value in its own column: without it the columns below shift left
    and a price ends up under the wrong heading. The grid remembers what is
    carried over and refills it as each row is walked.
    """

    def __init__(self) -> None:
        self.rows: list[list[str]] = []
        self._spans: dict[int, tuple[int, str]] = {}
        self._carried: dict[int, str] = {}
        self._row: list[str | None] = []
        self._column = 0

    def start_row(self) -> None:
        self._row = []
        self._column = 0
        self._carried = {column: value for column, (_, value) in self._spans.items()}

    def add(self, value: str, *, rowspan: int = 1, colspan: int = 1) -> None:
        """Place a cell, skipping any column a carried span already occupies."""
        column = self._next_column()
        for offset in range(colspan):
            self._place(column + offset, value)
            if rowspan > 1:
                self._spans[column + offset] = (rowspan, value)
        self._column = column + colspan

    def skip(self) -> None:
        """Give the next column to a span that already covers it."""
        column = self._column
        self._place(column, self._carried.pop(column, ""))
        self._column = column + 1

    def end_row(self) -> None:
        """Close the row and retire the spans it consumed."""
        while self._column in self._carried:
            self._place(self._column, self._carried.pop(self._column))
            self._column += 1
        for column, (remaining, value) in list(self._spans.items()):
            if remaining <= 1:
                del self._spans[column]
            else:
                self._spans[column] = (remaining - 1, value)
        if any(self._row):
            self.rows.append(["" if cell is None else cell for cell in self._row])

    def _place(self, column: int, value: str) -> None:
        while len(self._row) <= column:
            self._row.append(None)
        self._row[column] = value

    def _next_column(self) -> int:
        """Return the next column a fresh cell may take."""
        while self._column in self._carried:
            self._place(self._column, self._carried.pop(self._column))
            self._column += 1
        return self._column


def _span_size(attrs: list[tuple[str, str | None]], name: str) -> int:
    """Read a rowspan or colspan, tolerating the malformed values vendors ship.

    Baidu's table publishes a ``rowspan=3"`` with the quote inside the value, so
    the digits are read out rather than the value being trusted to be a number.
    """
    for key, value in attrs:
        if key == name:
            digits = re.sub(r"\D", "", value or "")
            return int(digits) if digits else 1
    return 1


class TextTableParser(HTMLParser):
    """Collect HTML tables along with the heading path that precedes them."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self.table_headings: list[list[str]] = []
        self.path: list[str] = []
        self.heading_level: int | None = None
        self.heading_text: list[str] = []
        self.grid: SpanGrid | None = None
        self.row_open = False
        self.rowspan = 1
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in HEADING_TAGS and self.grid is None:
            self.heading_level = int(tag[1])
            self.heading_text = []
        elif tag == "table":
            self.grid = SpanGrid()
            self.row_open = False
        elif self.grid is not None and tag == "tr":
            self.grid.start_row()
            self.row_open = True
        elif self.grid is not None and self.cell is None and tag in ("td", "th"):
            # A vendor that drops a <tr> leaves its cells orphaned. Opening the
            # row here keeps them rather than silently discarding the whole row.
            if not self.row_open:
                self.grid.start_row()
                self.row_open = True
            self.cell = []
            # Only a row span is expanded. A column span is how a vendor writes a
            # row heading across several columns; repeating the heading into each
            # column it covers would invent cells inside the header row, which is
            # where every reader here looks for its price columns.
            self.rowspan = _span_size(attrs, "rowspan")
        elif self.cell is not None and tag == "br":
            # A vendor stacks several values in one cell, one per line. The break
            # is kept rather than flattened so the values stay separable: the
            # first is the model, the rest are variants the same price covers.
            self.cell.append("<br>")

    def handle_data(self, data: str) -> None:
        if self.heading_level is not None:
            self.heading_text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in HEADING_TAGS and self.heading_level == int(tag[1]):
            # A heading replaces everything from its own level down, so an h3
            # keeps the h2 above it while an h2 starts a new top-level section.
            self.path = self.path[: self.heading_level - 1] + [
                clean_text(" ".join(self.heading_text))
            ]
            self.heading_level = None
        elif tag in ("td", "th") and self.cell is not None and self.grid is not None:
            self.grid.add(
                re.sub(r"\s+", " ", "".join(self.cell)).strip(),
                rowspan=self.rowspan,
            )
            self.cell = None
        elif tag == "tr" and self.grid is not None and self.row_open:
            self.grid.end_row()
            self.row_open = False
        elif tag == "table" and self.grid is not None:
            self.tables.append(self.grid.rows)
            self.table_headings.append(list(self.path))
            self.grid = None
            self.row_open = False


def split_markdown_row(line: str) -> list[str]:
    """Split a table row, keeping empty cells such as a carried model name."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [unescape_markdown(cell.strip()) for cell in stripped.split("|")]


def markdown_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    """Return Markdown tables with the heading path that precedes them."""
    tables: list[tuple[list[str], list[list[str]]]] = []
    path: list[str] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            title = unescape_markdown(clean_text(line.lstrip("# ")))
            path = path[: level - 1] + [title]
        if (
            line.startswith("|")
            and index + 1 < len(lines)
            and re.match(r"^\s*\|(?:\s*:?-+\s*\|)+\s*$", lines[index + 1])
        ):
            rows = [split_markdown_row(line)]
            index += 2
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                rows.append(split_markdown_row(lines[index]))
                index += 1
            tables.append((list(path), rows))
            continue
        index += 1
    return tables


def markdown_link_text(value: str) -> str:
    """Flatten ``[label](url)`` into ``label``."""
    flattened = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", unescape_markdown(value))
    return clean_text(flattened)


def markdown_json_rows(document: str) -> list[list[str]]:
    """Read the JSON-array rows some vendors embed directly in Markdown."""
    rows = []
    for line in document.splitlines():
        candidate = line.strip().rstrip(",")
        if not candidate.startswith("[") or not candidate.endswith("]"):
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list) and value and isinstance(value[0], str):
            rows.append(value)
    return rows


DOC_TABLE_RE = re.compile(r"<DocTable\b(?P<body>.*?)/>", re.DOTALL)
DOC_TABLE_COLUMNS_RE = re.compile(
    r"columns\s*=\s*\{\[(?P<columns>.*?)\]\}", re.DOTALL
)
DOC_TABLE_ROWS_RE = re.compile(r"rows\s*=\s*\{\[(?P<rows>.*?)\]\}", re.DOTALL)
DOC_TABLE_TITLE_RE = re.compile(
    r"\btitle\s*:\s*(?P<title>\"(?:\\.|[^\"\\])*\")"
)


def markdown_doc_tables(document: str) -> list[list[list[str]]]:
    """Read the header and rows of JSX ``DocTable`` blocks in Markdown.

    Some documentation sites publish their source Markdown with table columns as
    JavaScript objects and rows as JSON arrays. Keeping the header beside each row
    lets an adapter locate prices by meaning after the vendor inserts or reorders
    columns instead of assigning an amount by its old position.
    """
    tables = []
    for match in DOC_TABLE_RE.finditer(document):
        body = match.group("body")
        columns_match = DOC_TABLE_COLUMNS_RE.search(body)
        rows_match = DOC_TABLE_ROWS_RE.search(body)
        if not columns_match or not rows_match:
            continue
        headers = [
            json.loads(title.group("title"))
            for title in DOC_TABLE_TITLE_RE.finditer(columns_match.group("columns"))
        ]
        rows = markdown_json_rows(rows_match.group("rows"))
        if headers and rows:
            tables.append([headers, *rows])
    return tables


def headed_document_tables(document: str) -> list[tuple[list[str], list[list[str]]]]:
    """Read pricing tables from either rendered HTML or official Markdown."""
    parser = TextTableParser()
    parser.feed(document.replace("\x00", ""))
    if parser.tables:
        return list(zip(parser.table_headings, parser.tables))
    return markdown_tables(document)


# Headers that bill a non-token unit (audio duration, characters, calls) are not
# token prices, so a per-million-token adapter must never read them. Without this
# guard "输入音频时长" (input audio duration, billed per hour) looks like "input".
NON_TOKEN_BILLING_MARKERS = ("时长", "小时", "秒", "字符", "千次", "万次", "/次")

# Cache *storage* is billed per million tokens per hour, so it stays a token price
# even though its header names an hour. Detecting it first keeps the guard above
# from rejecting it along with genuine duration billing.
CACHE_STORAGE_MARKERS = ("缓存存储", "缓存空间")

# Headers that describe the *request* (how long it is, which context window it
# falls in) rather than a price. Volcengine words its condition column
# "条件 输入长度：千 token"; that still contains "输入", so without this guard the
# whole column is read as an input price column instead of a condition, and the
# time band and length tier of every row in the table are silently dropped.
# Adapters that classify headers themselves must consult this too.
NON_PRICE_HEADER_MARKERS = ("长度", "length")


def describes_request_length(header: str) -> bool:
    """Say whether a header names how long a request is, not what it costs."""
    value = clean_text(header).lower().replace("-", "").replace(" ", "")
    return any(marker in value for marker in NON_PRICE_HEADER_MARKERS)


def token_price_kind(header: str) -> str | None:
    """Map English and Chinese token-price headers without model allowlists."""
    value = clean_text(header).lower().replace("-", "").replace(" ", "")
    if any(marker in value for marker in CACHE_STORAGE_MARKERS) or (
        "cache" in value and "storage" in value
    ):
        return "cache_storage"
    if any(marker.replace(" ", "") in value for marker in NON_TOKEN_BILLING_MARKERS):
        return None
    if describes_request_length(header):
        return None
    cached = "cache" in value or "缓存" in value
    if cached and ("write" in value or "写入" in value or "创建" in value):
        return "cache_write"
    # A cache *miss* is billed at the regular input rate, so it has to be tested
    # before the hit branch: "输入（未命中缓存）" matches "缓存" and "输入" too.
    if "未命中" in value or "不命中" in value or "miss" in value:
        return "input"
    if cached and any(
        label in value for label in ("input", "read", "hit", "输入", "读取", "命中")
    ):
        return "cache_hit"
    if "input" in value or "prompt" in value or "输入" in value:
        return "input"
    if "output" in value or "completion" in value or "输出" in value:
        return "output"
    return None


# --- peak / off-peak windows ------------------------------------------------
#
# A time band is only ever explained in the vendor's own words: the window differs
# per platform, and on the same platform it can differ per model (Tencent bills
# deepseek-flash on weekdays only, while its 0731 generation still treats weekends
# as peak). Nothing here may therefore be inferred from another provider's schedule,
# and a platform that publishes no window must be reported as such.
CLOCK_RANGE_RE = re.compile(
    r"\d{1,2}:\d{2}(?:\s*(?:[-–—~～]|至|到)\s*(?:次日\s*)?\d{1,2}:\d{2})"
)
# A bullet or numbered item survives the Markdown reader as text; it is layout,
# not part of the rule, so it comes off before the window is quoted.
LIST_MARKER_RE = re.compile(r"^\s*(?:[*\-+•]|\d+[.、)])\s*")
TIME_BAND_WORDS = ("高峰", "空闲", "闲时", "忙时", "峰时", "谷时", "峰谷", "peak")
TIME_BAND_SCHEDULE_WORDS = ("工作日", "周末", "每天", "每日", "全天", "其余", "周一", "周二")
# A band is filed in a free-form condition column — "条件" on Volcengine, "子项" on
# Baidu, a column the vendor also uses for length tiers — so the cell *value*, not
# the heading, is what says a row is banded. Without this both bands land in one
# bucket and become indistinguishable offers.
TIME_BAND_CELL_MARKERS = ("高峰时段", "空闲时段", "低峰时段", "低谷时段", "peak")
# Most specific first, so "工作日（周一至周五）" is quoted as 周一至周五.
WEEKDAY_QUALIFIERS = (
    "周一至周五",
    "周一至周六",
    "周一至周日",
    "工作日",
    "每天",
    "每日",
    "周末",
)
MAX_TIME_BAND_STATEMENT = 400
# A rendered page can glue a price table and the note that follows it into one line,
# so the "sentence" arrives carrying unit prices. A vendor explaining when a window
# applies does not quote amounts in the same breath, and keeping such a row would
# repeat a price table in the report's prose section.
PRICE_IN_STATEMENT_RE = re.compile(r"[$¥￥]\s*\d|\d[\d.]*\s*元")


class TimeBandRule(NamedTuple):
    """A vendor's own sentence about a time band, plus the scope it was written for.

    ``scope`` is the lead-in the document put in front of the sentence (for example
    ``deepseek-v4.1-flash 模型高峰、空闲时段如下：``), which is often the only
    place the rule says which model it is about.
    """

    scope: str
    statement: str


def time_band_label(value: str) -> str | None:
    """Return the band a cell names, in the vendor's own wording."""
    lowered = clean_text(value).lower()
    return next(
        (marker for marker in TIME_BAND_CELL_MARKERS if marker in lowered), None
    )


def _band_scope_key(value: str) -> str:
    """Normalise a name for matching inside vendor prose.

    Markdown escapes the model name it quotes (``deepseek\\-v4.1\\-flash``), and one
    document spells the same generation both ``v4.1`` and ``v4-1``, so the escapes
    come off and the two separators are treated alike.
    """
    return normalize_model(unescape_markdown(value)).replace(".", "-")


def _strip_list_marker(value: str) -> str:
    """Drop the Markdown bullet or numbering a rule was published under."""
    return LIST_MARKER_RE.sub("", value.strip())


def time_band_rules(document: str) -> list[TimeBandRule]:
    """Return the vendor's own sentences explaining a peak/off-peak window.

    Sentences are quoted verbatim: the wording carries what decides the bill
    (weekday-only or all week, the clock ranges, the time zone) and every vendor
    words it differently, so paraphrasing here would invent a rule.
    """
    rules: list[TimeBandRule] = []
    scope = ""
    for line in document.splitlines() or [document]:
        # Split on sentence-final punctuation only. A "；" joins clauses of one
        # rule (Tencent states the weekday window and the weekend exemption in a
        # single sentence), so cutting there would divorce a rule from its scope.
        for sentence in re.split(r"(?<=[。！？!?])", clean_text(line)):
            candidate = _strip_list_marker(sentence.strip())
            if not candidate or len(candidate) > MAX_TIME_BAND_STATEMENT:
                continue
            if PRICE_IN_STATEMENT_RE.search(candidate):
                continue
            if not any(word in candidate.lower() for word in TIME_BAND_WORDS):
                continue
            if CLOCK_RANGE_RE.search(candidate) or any(
                word in candidate for word in TIME_BAND_SCHEDULE_WORDS
            ):
                rule = TimeBandRule(scope=scope, statement=candidate)
                if rule not in rules:
                    rules.append(rule)
            else:
                # A band lead-in with no window of its own: it names the model the
                # sentences after it are about.
                scope = candidate
    return rules


def select_time_band_rules(
    rules: list[TimeBandRule],
    *,
    model_id: str = "",
    display_name: str = "",
    labels: tuple[str, ...] = (),
) -> list[TimeBandRule]:
    """Pick the rules that govern a model, or the ones naming its service mode.

    A document can state several rules for one band, so every matching sentence is
    kept. The rule naming this model wins; rules naming only the delivery mode
    ("原厂直供") are used as a fallback, because one page can bill two generations
    on different schedules and must not have them merged.
    """
    names = [
        name
        for name in (
            _band_scope_key(model_id),
            _band_scope_key(display_name),
            # A rule names the model, not the row: strip the 原厂直供/正式版 suffix
            # the vendor puts on a display name before giving up on the name match.
            _band_scope_key(model_family(display_name)) if display_name else "",
        )
        if name
    ]
    named = [
        rule
        for rule in rules
        if any(name in _band_scope_key(f"{rule.scope} {rule.statement}") for name in names)
    ]
    if named:
        return named
    for label in labels:
        if not label:
            continue
        labelled = [
            rule for rule in rules if label in f"{rule.scope} {rule.statement}"
        ]
        if labelled:
            return labelled
    # A document that states one window without naming any model (DeepSeek puts its
    # schedule in a footnote) is stating it for every model it lists, so keep it
    # rather than reporting that the platform publishes no window.
    return rules


def format_time_band_window(rules: list[TimeBandRule]) -> str:
    """Render the rules governing a model as the compact window shown in the table."""
    windows = [compact_time_band_window(rule.statement) for rule in rules]
    return "；".join(dict.fromkeys(window for window in windows if window))


def time_bands_for(
    document: str,
    *,
    model_id: str = "",
    display_name: str = "",
    labels: tuple[str, ...] = (),
    source_url: str = "",
) -> dict[str, Any]:
    """Summarise the peak/off-peak window a vendor publishes for one model.

    Returns ``{}`` when the vendor publishes no window, so a provider that never
    splits its price by time is not given an invented schedule. When a window is
    found the vendor's own sentences are kept alongside the compact form, because
    the wording is what settles the bill.
    """
    rules = select_time_band_rules(
        time_band_rules(document),
        model_id=model_id,
        display_name=display_name,
        labels=labels,
    )
    if not rules:
        return {}
    statements = list(
        dict.fromkeys(
            " ".join(part for part in (rule.scope, rule.statement) if part).strip()
            for rule in rules
        )
    )
    result: dict[str, Any] = {"statements": statements}
    window = format_time_band_window(rules)
    if window:
        result["window"] = window
    if source_url:
        result["source_url"] = source_url
    return result


def compact_time_band_window(statement: str) -> str:
    """Reduce a statement to its clock ranges, each carrying its weekday scope.

    The comparison table quotes this; :func:`time_band_rules` keeps the full
    sentence for the 时段规则 section, so the vendor's wording is never lost.
    """
    parts: list[str] = []
    statement = _strip_list_marker(clean_text(statement))
    qualifier = ""
    for match in CLOCK_RANGE_RE.finditer(statement):
        scope = next(
            (
                word
                for word in WEEKDAY_QUALIFIERS
                if word in statement[max(0, match.start() - 60) : match.start()]
            ),
            "",
        )
        value = re.sub(r"\s+", "", match.group(0))
        parts.append(value if scope == qualifier else f"{scope} {value}".strip())
        qualifier = scope
    # The weekend exemption is stated as a clause of its own and is the part that
    # differs most between platforms, so it is kept whole rather than truncated.
    weekend = re.search(r"周末[^。；;]*", statement)
    if weekend and any(word in weekend.group(0) for word in ("全天", "不区分")):
        parts.append(_strip_list_marker(weekend.group(0)))
    # "其余" closes the rule: it says what the band that was *not* named costs.
    rest = re.search(r"其余[^，,。；;）)]*", statement)
    if rest:
        parts.append(_strip_list_marker(rest.group(0)))
    return "、".join(dict.fromkeys(part for part in parts if part))


def monetary_amount(value: str, header: str, currency: str) -> str | None:
    """Extract a monetary amount only when the cell or header names currency."""
    cell = clean_text(value).replace(",", "")
    heading = clean_text(header).lower()
    if re.search(r"\bfree\b", cell, re.I) or any(
        label in cell for label in ("免费", "不收费")
    ):
        return None
    if currency == "USD":
        match = re.search(r"\$\s*(\d+(?:\.\d+)?)", cell)
        if match:
            return match.group(1)
        currency_named = "usd" in cell.lower() or "usd" in heading
    else:
        match = re.search(r"(?:¥|￥)\s*(\d+(?:\.\d+)?)", cell)
        if match:
            return match.group(1)
        match = re.search(r"(\d+(?:\.\d+)?)\s*元", cell)
        if match:
            return match.group(1)
        currency_named = any(label in heading for label in ("元", "cny", "rmb"))
    if currency_named:
        values = numeric_values(cell)
        return values[0] if values else None
    return None
