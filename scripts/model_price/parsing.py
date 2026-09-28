"""Table readers for vendor documents published as HTML or Markdown."""

from __future__ import annotations

import json
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Iterable, NamedTuple

from .models import model_family, normalize_model
from .pricing import (
    CREDIT_WORD_PATTERN,
    CREDIT_WORDS,
    FREE_AMOUNT,
    is_credit_unit,
    unit_code,
    discount_multiplier,
    discounted_amount,
    is_free_statement,
    unit_measure,
    without_discount_terms,
)
from .text import (
    CELL_BREAK_RE,
    clean_text,
    clean_zero_width_text,
    numeric_values,
    unescape_markdown,
)

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


# A vendor writes a date either as digits the locale orders or as an English month
# name. Both readings live here because both are read off published documents — a
# catalogue stamp, a notice headline, and a price that applies only until a day.
ENGLISH_DATE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}\b",
    re.I,
)
NUMERIC_DATE_RE = re.compile(
    r"(?P<year>20\d\d)\s*(?:年|[-/.])\s*(?P<month>\d{1,2})"
    r"\s*(?:月|[-/.])\s*(?P<day>\d{1,2})\s*日?"
    r"(?:\s*(?P<clock>\d{1,2}:\d{2}(?::\d{2})?))?"
)


def date_value(value: str, *, utc_offset: str = "") -> str | None:
    """Read a published date, keeping the precision the vendor gave it.

    A date-only source stays date-only, and an offset is attached only where the
    document states a clock: inventing midnight would put a moment in the report
    the vendor never published.
    """
    value = clean_zero_width_text(value)
    match = NUMERIC_DATE_RE.search(value)
    if match:
        try:
            day = (
                datetime(int(match["year"]), int(match["month"]), int(match["day"]))
                .date()
                .isoformat()
            )
        except ValueError:
            return None
        clock = match["clock"]
        return f"{day}T{clock}{utc_offset}" if clock else day
    match = ENGLISH_DATE.search(value)
    if match:
        candidate = match.group().replace(",", "")
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue
    return None


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
        if tag == "br" and self.cell is not None:
            # A vendor writes the break as ``</br>`` often enough that the closing
            # form has to carry the same meaning as the opening one: read as
            # nothing, it glues the two values a cell stacks into one run.
            self.cell.append("<br>")
        if tag == "li" and self.cell is not None:
            # Lists inside a table cell are separate published values, just as
            # explicit <br> tags are. Joining them erases capability boundaries.
            self.cell.append("<br>")
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
            and re.match(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$", lines[index + 1])
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


# --- what a price column is --------------------------------------------------
#
# A price column says what a charge is *for* (an input, an output, cache activity)
# or what it is billed *against* (a token, an image, a second of video). A header
# that names neither describes the request instead — how long it is, how large the
# picture is — and a condition read as a price silently drops the tier that
# separates one row from the next. "条件 输入长度：千 token" is the case both
# readings have to survive: it names a unit without pricing one.
REQUEST_MARKERS = (
    "长度", "时长", "分辨率", "宽高比", "清晰度", "像素", "模态",
    "length", "duration", "resolution", "aspect", "modality",
)

# Cache *storage* is billed per million tokens per hour, so it stays a token price
# even though its header names an hour.
CACHE_STORAGE_MARKERS = ("缓存存储", "缓存空间")


def describes_request(header: str) -> bool:
    """Say whether a header names how big a request is, not what it costs."""
    value = clean_text(header).lower().replace("-", "").replace(" ", "")
    return any(marker in value for marker in REQUEST_MARKERS)


def price_role(header: str) -> str | None:
    """The charge a header names — input, output, cache activity — or ``None``."""
    value = clean_text(header).lower().replace("-", "").replace(" ", "")
    if any(marker in value for marker in CACHE_STORAGE_MARKERS) or (
        "cache" in value and "storage" in value
    ):
        return "cache_storage"
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


def header_unit_phrase(header: str) -> str:
    """The unit a price column names, preferring the one it put in brackets.

    A vendor heads a column with the charge and its unit together ("推理输入（元/
    百万 tokens）", "积分单价（元/积分）"); the bracketed part is the unit, and the
    header without it is what the charge is called.
    """
    text = clean_text(header)
    match = re.search(r"[（(]([^（()）]*)[)）]\s*$", text)
    return match.group(1).strip() if match else text


def price_unit_code(
    cell_unit: str, header: str, *, currency: str, default: str
) -> str:
    """The unit one price is billed in, from the cell's wording or the header's.

    The cell wins: a vendor that prices two charges in one column states the unit
    beside each amount. An amount whose unit this tool cannot read keeps the
    vendor's own wording rather than being labelled with a unit never published.
    """
    for candidate in (cell_unit, header_unit_phrase(header), header):
        if candidate and (unit_measure(candidate) or is_credit_unit(candidate)):
            return unit_code(candidate, currency)
    return unit_code(cell_unit, currency) if cell_unit else default


def price_column_kinds(
    headers: list[str],
    table: list[list[str]],
    *,
    currency: str,
    kind_of: Any = None,
) -> dict[int, str]:
    """Which columns price a charge, and what each charge is.

    A column is read when its header names a charge and when its cells publish
    amounts. Both readings are needed: a header naming the quantity billed
    ("输入音频时长") heads a price column once its cells are money, and a header
    naming a unit whose cells hold durations prices nothing at all.
    """
    classify = kind_of or price_kind
    columns: dict[int, str] = {}
    for index, header in enumerate(headers):
        cells = [row[index] if index < len(row) else "" for row in table[1:]]
        kind = classify(header)
        money = any(monetary_amount(cell, header, currency) is not None for cell in cells)
        if kind is not None and (not describes_request(header) or money):
            columns[index] = kind
        elif money:
            # A header that names the billed quantity rather than the charge
            # ("输入音频时长") still says which direction the charge runs.
            columns[index] = price_role(header) or unit_measure(header) or "price"
    return columns


def price_kind(header: str) -> str | None:
    """Classify a price column, or return ``None`` when it is a condition.

    A header naming what it bills against is a price column even when it names no
    direction — an image model's "输出图单价（元/张）" and a video model's
    "在线推理 元/百万token" both price a charge this way. The charge keeps the unit
    as its kind, because a label is what the vendor called it and the unit is what
    it is billed in.
    """
    if describes_request(header):
        return None
    return price_role(header) or unit_measure(header)


# --- what one cell publishes -------------------------------------------------
#
# A vendor that prices a charge in several tiers prints them into one cell: a list
# under the scope line it grouped them by, or clauses on one line ("音频：0.18
# 元/分钟；视频：1.2 元/分钟"). Reading one amount and dropping the rest quotes a
# tier the reader cannot tell apart from the model's whole price, and taking the
# last number in a cell that also states a resolution quotes a resolution. So every
# amount is read, and the vendor's own words for what each one covers travel with
# it.
class CellRate(NamedTuple):
    """One charge a cell publishes, under the words the vendor scoped it by."""

    amount: str
    conditions: tuple[str, ...] = ()
    unit_phrase: str = ""
    display: str = ""
    list_amount: str | None = None
    discount: str | None = None


MONEY_PATTERNS: dict[str, tuple[str, ...]] = {
    "CNY": (r"[¥￥]\s*(\d+(?:\.\d+)?)", r"(?<![\d.])(\d+(?:\.\d+)?)\s*元"),
    "USD": (r"\$\s*(\d+(?:\.\d+)?)", r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:usd|美元)"),
}
# A charge published as a span rather than as one figure ("15～60积分/次") is kept
# whole: both ends are the vendor's, and printing either one alone would quote a
# price the page does not have.
RANGE_RE = r"\d+(?:\.\d+)?(?:\s*[-~～—至]\s*\d+(?:\.\d+)?)?"
# A vendor that bills in an internal credit publishes the charge in that credit
# rather than in money ("480p：2 积分/次"). It is still the model's price, so it is
# read and kept in the unit the vendor wrote it in.
CREDIT_AMOUNT_RE = re.compile(
    rf"(?<![\d.])({RANGE_RE})\s*(?:{CREDIT_WORD_PATTERN})", re.I
)
CURRENCY_MARKERS: dict[str, tuple[str, ...]] = {
    "CNY": ("元", "￥", "¥", "cny", "rmb", "人民币"),
    "USD": ("$", "usd", "美元", "dollar"),
}
# A charge of nothing, however the vendor words it inside a longer cell.
FREE_WORD_RE = re.compile(r"免费|不收费|不计费|free\s+of\s+charge|\bfree\b", re.I)
# A rate the vendor published beside the rate it reduces.
LIST_PRICE_RE = re.compile(r"原价\s*[:：]?\s*[¥￥$]?\s*(\d+(?:\.\d+)?)")
# Only a parenthetical that names an amount is dropped: it restates the rate in
# another unit ("16 元/百万 Tokens（约 0.0002 元/秒）") or divides it, and no vendor
# bills a second charge inside the brackets of the first.
PARENTHETICAL_RE = re.compile(r"[（(][^（()）]*[)）]")
# A clause is one statement: the vendor ends it with a line break or a semicolon.
# A comma is not a separator — "480p，720p" is one scope, not two.
# A vendor ends a clause with a line break or a semicolon. A comma is not a
# separator — "480p，720p" is one scope, not two.
# A word joining two rates is not a condition either of them is billed under.
CONNECTOR_WORDS = re.compile(r"^(?:or|and|及|和|与|或)\s*", re.I)
CLAUSE_SPLIT_RE = re.compile(r"；|;|\n")


def money_spans(value: str, currency: str) -> list[tuple[int, int, str]]:
    """Every amount in ``value`` that names ``currency``, with where it sits."""
    spans = [
        (match.start(), match.end(), match.group(1))
        for pattern in MONEY_PATTERNS.get(currency, ())
        for match in re.finditer(pattern, value)
    ]
    spans.sort()
    return spans


def credit_spans(value: str) -> list[tuple[int, int, str]]:
    """Every amount in ``value`` that names the vendor's own credit."""
    return [
        (match.start(), match.end(), match.group(1))
        for match in CREDIT_AMOUNT_RE.finditer(value)
    ]


def names_currency(value: str, currency: str) -> bool:
    """Say whether a cell or header states the currency an amount is in."""
    lowered = clean_text(value).lower()
    return any(marker in lowered for marker in CURRENCY_MARKERS.get(currency, ()))


def _drop_money_parentheticals(value: str, currency: str) -> str:
    return PARENTHETICAL_RE.sub(
        lambda match: "" if money_spans(match.group(), currency) else match.group(),
        value,
    )


def _rate_text(value: str) -> str:
    """Reduce a clause to the words that scope a rate rather than lay it out."""
    return clean_text(_strip_list_marker(value)).strip("：:，,。；; *")


def _scope_words(value: str) -> str:
    """The most specific words a rate was published under.

    A clause carries a lead-in and a label ("按分辨率计费：2K"), and only the part
    after the last colon distinguishes this rate from its neighbours. A clause that
    is all label keeps it whole ("输入：").
    """
    parts = [part.strip() for part in _rate_text(value).split("：")]
    parts = [part for part in parts if part]
    return parts[-1] if parts else ""


def _cell_clauses(cell: str) -> list[str]:
    """The statements one cell publishes, in the order the vendor wrote them."""
    return [
        clause
        for line in CELL_BREAK_RE.split(cell)
        for clause in CLAUSE_SPLIT_RE.split(line)
        if clause.strip()
    ]


def _free_spans(clause: str) -> list[tuple[int, int, str, bool]]:
    return [
        (match.start(), match.end(), FREE_AMOUNT, False)
        for match in FREE_WORD_RE.finditer(clause)
    ]


def amount_spans(value: str, currency: str) -> list[tuple[int, int, str, bool]]:
    """Every charge a cell states, in the order it states them.

    The flag on each span says whether the amount is denominated in the vendor's
    own credit rather than in money, which decides how its unit reads.
    """
    return sorted(
        [(*span, False) for span in money_spans(value, currency)]
        + [(*span, True) for span in credit_spans(value)]
        + _free_spans(value)
    )


# A vendor that publishes a reduction without a multiplier strikes the rate it
# reduces through and prints the one it bills beside it ("~~4.20~~ 2.10"). Both
# numbers are the vendor's and both are kept: the struck one is what the charge
# goes back to when the promotion ends.
STRUCK_RATE_RE = re.compile(
    r"~~\s*[^\d~]*(\d+(?:\.\d+)?)\s*~~\s*[^\d]*(\d+(?:\.\d+)?)"
)


def _struck_rate(value: str) -> tuple[str, str] | None:
    """Read a struck-through list price and the rate billed beside it."""
    match = STRUCK_RATE_RE.search(value)
    return (match.group(2), match.group(1)) if match else None


def _bare_figure(value: str) -> str | None:
    """The amount a clause that is nothing but a number publishes.

    A number embedded in words is not a price: a scope line reading "输出视频分辨率
    为 1080p" states a resolution, and taking its digits for an amount would price
    the model at 1080.
    """
    stripped = without_discount_terms(value).strip("：:，,。;；* ")
    stripped = LIST_PRICE_RE.sub(r"\1", stripped).strip()
    numbers = numeric_values(stripped)
    if len(numbers) == 1 and re.sub(r"[\s,]", "", stripped) == numbers[0]:
        return numbers[0]
    return None


def _narrow(figure: str | None) -> tuple[str, str] | None:
    """A plain figure is a rate with no list price beside it."""
    return (figure, "") if figure is not None else None


def _unit_phrase(tail: str, *, credit: bool) -> str:
    """The unit a rate is billed in, as the cell wrote it beside the amount."""
    head = clean_text(tail).strip().strip("*")
    if credit:
        return f"{CREDIT_WORDS[0]}/{head.lstrip('/ ')}" if head else CREDIT_WORDS[0]
    return " ".join(head.split()[:3])


def _leading_parenthetical(value: str) -> str:
    """A bracket written straight after an amount, which describes that amount."""
    match = re.match(r"\s*[（(][^（()）]*[)）]", value)
    return match.group(0) if match else ""


def _tail_parts(value: str) -> tuple[str, str, str]:
    """Split what follows an amount into its unit, its brackets, and its label.

    A vendor writes "0.80 元/秒，768P 0.50 元/秒": the unit is the part before the
    comma and the label belongs to the rate after it. A bracket names what the rate
    covers and can sit after the unit ("$0.005/min (audio)") rather than straight
    after the amount. Where the amount ends the clause the whole remainder is its
    unit — and a remainder that names no unit at all is the next rate's label.
    """
    parts = re.split(r"[，,；;、]", clean_text(value), maxsplit=1)
    unit = parts[0].strip()
    carry = CONNECTOR_WORDS.sub("", parts[1]).strip() if len(parts) > 1 else ""
    brackets = " ".join(match.group(0) for match in PARENTHETICAL_RE.finditer(unit))
    unit = PARENTHETICAL_RE.sub(" ", unit).strip()
    if not carry and unit_measure(unit) is None:
        return "", brackets, CONNECTOR_WORDS.sub("", unit).strip()
    return unit, brackets, carry


def _promoted(amount: str, clause: str) -> tuple[str, dict[str, str]]:
    """The amount a clause bills and the terms it states for it.

    A vendor running a promotion prints the rate it reduces and how far, so the
    amount billed is the two together. The list price is not a second charge: it is
    what this one is reduced from, and it travels with it.
    """
    listed = LIST_PRICE_RE.search(clause)
    multiplier = discount_multiplier(clause)
    if not listed or not multiplier or listed.group(1) != amount:
        return amount, {}
    billed, _ = discounted_amount(amount, multiplier)
    return billed or amount, {"list_amount": amount, "discount": multiplier}


def cell_rates(cell: str, *, header: str = "", currency: str = "CNY") -> list[CellRate]:
    """Every rate one cell publishes, each with the scope the vendor gave it.

    An amount is read where the vendor named its currency or its credit; a bare
    number is read only where the clause published nothing else, and only when the
    header named the currency it is in. Everything else in a cell — a resolution, a
    duration, the vendor's working — is what scopes a rate or explains it, and
    reading one of those as a price quotes a number the page never priced.
    """
    if is_free_statement(cell):
        return [CellRate(FREE_AMOUNT, (), "", clean_text(cell))]
    text = _drop_money_parentheticals(cell, currency)
    rates: list[CellRate] = []
    scope: tuple[str, ...] = ()
    for clause in _cell_clauses(text):
        stripped = clause.strip()
        # A quoted line is the vendor explaining a rate, not pricing one.
        if stripped.startswith((">", "|")):
            continue
        body = _strip_list_marker(stripped)
        spans = amount_spans(body, currency)
        value_at = max(body.rfind("："), body.rfind(":"))
        label, value = (
            (body[:value_at], body[value_at + 1 :]) if value_at >= 0 else ("", body)
        )
        if not spans:
            priced = (
                _struck_rate(value) or _narrow(_bare_figure(value))
                if names_currency(header, currency)
                else None
            )
            if priced is not None:
                figure, listed = priced
                own = _scope_words(label) if label.strip() else ""
                conditions = tuple(
                    dict.fromkeys(part for part in (*scope, own) if part)
                )
                billed, terms = _promoted(figure, body)
                if listed and not terms:
                    terms = {"list_amount": listed}
                rates.append(
                    CellRate(billed, conditions, "", _rate_text(body), **terms)
                )
            elif words := _rate_text(body):
                # A statement that prices nothing is the scope of the rates under
                # it ("输出视频分辨率为 480p，720p").
                scope = (words,)
            continue
        head = _scope_words(label) if label.strip() else ""
        scoped = (*scope, head) if head else scope
        for index, (start, end, amount, credit) in enumerate(spans):
            next_start = spans[index + 1][0] if index + 1 < len(spans) else len(body)
            following = body[end:next_start]
            bracket = _leading_parenthetical(following)
            unit_tail, in_unit, carry = _tail_parts(following[len(bracket) :])
            # Only the first rate of a clause carries its lead-in label; the rates
            # after it are labelled by the words the vendor put between them.
            own = _scope_words(body[:start]) if index == 0 else ""
            conditions = tuple(
                dict.fromkeys(
                    part
                    for part in (
                        *scoped,
                        own,
                        _scope_words(bracket),
                        _scope_words(in_unit),
                    )
                    if part
                )
            )
            billed, terms = _promoted(amount, body)
            rates.append(
                CellRate(
                    billed,
                    conditions,
                    _unit_phrase(unit_tail, credit=credit),
                    # The rate's own words, not the whole cell: a cell that prices
                    # four tiers keeps each tier's own sentence as its evidence, so
                    # a report of one of them does not quote the other three.
                    _rate_text(body[spans[index - 1][1] if index else 0 : next_start]),
                    **terms,
                )
            )
            if carry:
                scoped = (*scoped, carry)
    return rates


def monetary_amount(value: str, header: str, currency: str) -> str | None:
    """Extract a monetary amount only when the cell or header names currency.

    A cell that states a charge of nothing is that model's price, recorded as a
    zero. A cell that merely mentions a free allowance beside other content is
    not this row's price and is left to whatever else the cell says — the reader
    that expands such a cell is :func:`cell_rates`.
    """
    cell = clean_text(value).replace(",", "")
    heading = clean_text(header).lower()
    if is_free_statement(cell):
        return FREE_AMOUNT
    if re.search(r"\bfree\b", cell, re.I) or any(
        label in cell for label in ("免费", "不收费")
    ):
        return None
    if spans := money_spans(cell, currency):
        return spans[0][2]
    if names_currency(heading, currency):
        values = numeric_values(cell)
        return values[0] if values else None
    return None


# --- peak / off-peak windows ------------------------------------------------
#
# A time band is only ever explained in the vendor's own words: the window differs
# per platform, and on the same platform it can differ per model (Tencent bills
# deepseek-flash on weekdays only, while its 0731 generation still treats weekends
# as peak). Nothing here may therefore be inferred from another provider's schedule,
# and a platform that publishes no window must be reported as such.
CLOCK_VALUE = r"\d{1,2}(?::\d{2}|点(?:\d{1,2}分)?)"
CLOCK_RANGE_RE = re.compile(
    rf"{CLOCK_VALUE}(?:\s*(?:[-–—~～]|至|到)\s*(?:次日\s*)?{CLOCK_VALUE})"
)
# A bullet or numbered item survives the Markdown reader as text; it is layout,
# not part of the rule, so it comes off before the window is quoted. A numbered
# item is "1. 条件" or "1、条件" — never "2.40", which is an amount and would be
# stripped into a different number by a marker read without its space.
LIST_MARKER_RE = re.compile(r"^\s*(?:[*\-+•]\s+|\d+[、)]\s*|\d+\.\s+)")
TIME_BAND_WORDS = ("高峰", "空闲", "闲时", "忙时", "峰时", "谷时", "峰谷", "peak")
TIME_BAND_SCHEDULE_WORDS = (
    "工作日",
    "周末",
    "每天",
    "每日",
    "全天",
    "其余",
    "此外",
    "周一",
    "周二",
)
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
# Server-rendered detail pages often put the rule in a tooltip ``span`` on one
# otherwise enormous HTML line. Block and inline closing tags provide the real
# text boundaries, so expose those before applying the same prose parser used for
# Markdown documents.
HTML_TEXT_BREAK_RE = re.compile(
    r"(?:<br\s*/?>|</(?:p|li|span|div|td|tr|button)\s*>)", re.I
)


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
    lines = HTML_TEXT_BREAK_RE.sub("\n", document).splitlines() or [document]
    for line in lines:
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
    rest = re.search(r"(?:其余|此外)[^，,。；;）)]*", statement)
    if rest:
        parts.append(_strip_list_marker(rest.group(0)))
    return "、".join(dict.fromkeys(part for part in parts if part))


def monetary_amount(value: str, header: str, currency: str) -> str | None:
    """Extract a monetary amount only when the cell or header names currency.

    A cell that states a charge of nothing is that model's price, recorded as a
    zero. A cell that merely mentions a free allowance beside other content is
    not this row's price and is left to whatever else the cell says.
    """
    cell = clean_text(value).replace(",", "")
    heading = clean_text(header).lower()
    if is_free_statement(cell):
        return FREE_AMOUNT
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


# A vendor that limits a rate in time dates it inside the cell: ``through <day>`` for
# the last day it applies, and — when the rate that follows is already announced —
# that rate and the day it starts. Google states both halves in its model rows and
# only the first in its cache rows. Reading the amount alone would take a
# limited-time rate for what the model ordinarily costs, and lose an increase the
# vendor had already published.
DATED_RATE_RE = re.compile(
    r"\$\s*(?P<current>\d+(?:\.\d+)?)[^.$]*?\s*through\s*(?P<until>[^.$]+)"
    r"(?:[.\s]*\$\s*(?P<next>\d+(?:\.\d+)?)[^.$]*?\s*starting\s*(?P<after>[^.$]+))?",
    re.I,
)


def dated_rate_terms(value: str) -> dict[str, str]:
    """Read the terms a cell that dates the rate it bills publishes.

    The day the amount stops applying is always read; the amount that takes over is
    read when the same cell announces one. The amount billed now is not returned —
    the caller already reads it, and it is what a purchase costs today. An empty
    mapping means the cell prices the charge once, which is every cell outside a
    promotion.
    """
    match = DATED_RATE_RE.search(clean_text(value))
    if not match:
        return {}
    terms: dict[str, str] = {}
    if until := date_value(match["until"]):
        terms["effective_until"] = until
    if match["next"]:
        terms["list_amount"] = match["next"]
    return terms


# Words a vendor writes when it is saying a price is not its ordinary one. Nothing
# here decides what the price is — the price table does — so the list is deliberately
# wide: a sentence carrying one of these, and naming a model, is that model's own
# explanation of what it costs.
PROMOTION_WORDS = (
    "promotional",
    "promotion",
    "discount",
    "限时",
    "优惠",
    "折扣",
    "折",
    "特价",
)

# Sentence-final punctuation, split so that a decimal point is not treated as one. A
# model name carries dots (``gpt-5.6-sol``, ``seedance-2.0``), and cutting at every
# dot leaves a name in halves that name nothing.
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])\s*|(?<=[.!?])(?=\s)")


def models_named_in(text: str, names: Iterable[str]) -> set[str]:
    """Which of these names a vendor's own text states, as a whole name.

    A dot separates one spelling and belongs to a version in another, so a name is
    matched part by part with any of ``-``, ``_``, ``.`` or a space between its
    parts. Where two names start in the same place the longer one wins: a sentence
    about ``GPT-5.6 Sol`` names that model and not the ``gpt-5.6`` its name also
    begins with, and settling for the shorter one would attribute a promotion to a
    model the sentence is not about.
    """
    candidates = {
        name: [part for part in re.split(r"[-_.\s]+", name.strip()) if part]
        for name in names
        if name and name.strip()
    }
    candidates = {name: parts for name, parts in candidates.items() if parts}
    if not candidates:
        return set()
    alternation = "|".join(
        r"[-_.\s]+".join(re.escape(part) for part in candidates[name])
        for name in sorted(candidates, key=lambda name: -len(candidates[name]))
    )
    stated = re.compile(
        r"(?<![A-Za-z0-9])(?:" + alternation + r")(?![A-Za-z0-9._-])", re.I
    )
    found: set[str] = set()
    for match in stated.finditer(text):
        written = re.sub(r"[-_.\s]+", "-", match.group()).lower()
        found.update(
            name
            for name in candidates
            if re.sub(r"[-_.\s]+", "-", name).lower() == written
        )
    return found


def promotion_notes(document: str, *, names: Iterable[str]) -> dict[str, list[str]]:
    """The vendor's own sentences about each named model's price, quoted as written.

    Every name is offered at once, which is what makes a sentence naming one model
    read as naming that one rather than another whose id it begins with: at each
    place a name starts, the longest one wins. A sentence naming none of them belongs
    to none of them, and a name the document explains nothing about gets no entry.

    Nothing here is reduced to a field — "at least through November 21, 2026" is not
    an end date, and 达到用量上限后恢复按刊例价结算 is not a multiplier — so the amount
    each sentence explains is still read from the price table and the sentence travels
    beside it as the vendor's own wording.
    """
    candidates = {name for name in names if name and name.strip()}
    if not candidates:
        return {}
    notes: dict[str, list[str]] = {}
    for sentence in _sentences(document):
        if not any(word in sentence.lower() for word in PROMOTION_WORDS):
            continue
        for name in models_named_in(sentence, candidates):
            explained = notes.setdefault(name, [])
            if sentence not in explained:
                explained.append(sentence)
    return notes


def _sentences(document: str) -> list[str]:
    """The document's own sentences, in the order it published them."""
    return [
        stripped
        for line in HTML_TEXT_BREAK_RE.sub("\n", document).splitlines()
        for part in SENTENCE_SPLIT_RE.split(clean_text(line))
        if (stripped := _strip_list_marker((part or "").strip()))
    ]
