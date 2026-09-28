"""Baidu Qianfan (千帆) pay-as-you-go model pricing.

The pricing page is a Gatsby document: the portal's own HTML is the whole site,
and the Markdown behind its "查看 MD" button is assembled in the browser rather
than published as a file. What the site does serve is the article body the page
pre-fetches as ``page-data.json`` — an official structured source, read here in
preference to scraping the rendered portal page.

Baidu prices a row along three axes at once. The *billing item* is named inside
the row ("子项": 输入 / 缓存命中 / 输出), the *serving channel* is a column
(在线推理 / 批量推理), and the peak/off-peak window is written into the item's own
text rather than stated in a note beside the table. A row is therefore read
across the table instead of down a column.

A fourth axis is time. Baidu runs promotions on the same rows: a column can be
added for an activity, and a cell can be rewritten to publish the standing rate
and the promoted one side by side. Each is read as its own offer, because a rate
that ends is not the rate that stands, and a reader comparing channels has to see
both to know which one a purchase will actually be billed at.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any, Iterable, NamedTuple
from urllib.parse import urljoin

from ..core import PriceSource, now_iso
from ..errors import SourceError
from ..models import (
    model_family,
    normalize_model,
    split_trailing_parenthetical,
    trailing_parenthetical,
)
from ..parsing import (
    HTML_TEXT_BREAK_RE,
    cell_rates,
    document_update_stamp,
    headed_document_tables,
    header_unit_phrase,
    monetary_amount,
    price_kind,
    time_band_label,
)
from ..pricing import (
    is_commitment_unit,
    is_free_amount,
    make_record,
    per_million_tokens,
    price_item,
    tokens_per_price_unit,
    unit_code,
    unit_measure,
)
from ..text import CELL_BREAK_RE, clean_text, first_cell_line

BAIDU_PAGE_URL = "https://cloud.baidu.com/doc/qianfan/s/wmh4sv6ya"
# The page pre-fetches its own data file. Its address is read out of that link
# instead of being hard-coded, because the CDN path carries the build's asset
# prefix and would rot on the next deploy.
BAIDU_PAGE_DATA_RE = re.compile(r'href="([^"]*/page-data/[^"/]+/page-data\.json)"')

# Columns that identify a pay-as-you-go token table. Baidu prices token packages,
# TPM reservations, OCR pages, images, video and compute units on the same page,
# and those tables do not carry all of these.
BAIDU_MODEL_HEADER = "模型名称"
BAIDU_VERSION_HEADER = "版本名称"
BAIDU_SERVICE_HEADER = "服务内容"
BAIDU_ITEM_HEADER = "子项"
BAIDU_UNIT_HEADER = "单位"
# A one-price-per-model table heads its amount column with one of these.
BAIDU_PRICE_HEADERS = ("单价", "价格")
BAIDU_PLAIN_SERVICE = "推理服务"
# One billing item is priced per serving channel, and a channel can be repeated
# once per activity ("批量推理 （2月活动价）"). A column that names an activity is
# that activity's rate for the channel, never the standing one.
BAIDU_CHANNELS = ("在线推理", "批量推理")
BAIDU_PROMOTED_COLUMN_RE = re.compile(r"[（(]\s*(?P<activity>[^（()）]*活动[^（()）]*?)\s*[）)]")
BAIDU_RETIRING_MARKER = "即将下线"
# The window lives in the item name ("输入（高峰时段：8:00-22:00）").
BAIDU_WINDOW_RE = re.compile(
    r"(高峰时段|空闲时段|低峰时段|低谷时段)[：:]\s*"
    r"(\d{1,2}:\d{2}\s*[-–—~～]\s*(?:次日\s*)?\d{1,2}:\d{2})"
)

# A cell prices the row twice when an activity covers it: the standing rate and
# the promoted one, each under its own label ("原价：0.002" / "国庆限定价：0.0012").
BAIDU_LIST_PRICE_LABEL = "原价"
BAIDU_LABELLED_AMOUNT_RE = re.compile(
    r"^(?P<label>[^：:\d]*?)\s*[：:]\s*(?P<amount>\d+(?:\.\d+)?)$"
)

# The activity banner states its own name and the window it runs for, one per
# line. A banner is the only place the page dates an activity, so a promoted rate
# is dated only when the banner is there to date it.
BAIDU_ACTIVITY_LABEL = "活动时间"
BAIDU_PROMOTION_WORD = "活动"
# A badge, not a sentence: the longest activity tag this page has published.
BAIDU_ACTIVITY_NAME_MAX = 12
BAIDU_GENERIC_PROMOTION = "活动价"
BAIDU_ACTIVITY_DATE_RE = re.compile(r"20\d\d\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")
BAIDU_ACTIVITY_DATE_VALUE_RE = re.compile(
    r"(20\d\d)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)
# Every activity name shares these words, so what tells two of them apart is what
# is left: the tag above a table and the one beside it name one activity.
BAIDU_ACTIVITY_NOISE_RE = re.compile(r"活动|价")


class Promotion(NamedTuple):
    """One activity a rate belongs to: what it is called and how long it runs."""

    name: str
    window: str = ""


class BaiduRate(NamedTuple):
    """One rate a cell publishes, and how the page published it."""

    amount: str
    # The rate this one discounts, when the cell published both.
    list_amount: str | None = None
    # The activity that prices this rate, or ``None`` for the standing rate.
    promotion: Promotion | None = None
    # The vendor's own figure for this rate, kept so a charge of nothing keeps the
    # vendor's word for it.
    text: str = ""


def price_data_url(page: str) -> str:
    """Read the article's own data address out of the page pre-fetching it."""
    match = BAIDU_PAGE_DATA_RE.search(page)
    if not match:
        raise SourceError("Baidu document published no page-data link")
    return urljoin(BAIDU_PAGE_URL, match.group(1))


def price_channel(header: str) -> tuple[str, str] | None:
    """Name the serving channel a price column belongs to, and any activity.

    A column that prices an activity names it in its own heading, so the activity
    travels with the channel: its rate is a different offer from the standing rate
    of the same channel rather than a second reading of it.
    """
    label = clean_text(header)
    channel = next((name for name in BAIDU_CHANNELS if name in label), None)
    if channel is None:
        return None
    match = BAIDU_PROMOTED_COLUMN_RE.search(label)
    return channel, clean_text(match["activity"]) if match else ""


def price_note(item: str) -> str:
    """Return what an item's parenthetical says besides its band and hours.

    Baidu marks a price that settles on a later day inside the item itself
    ("命中缓存（高峰时段：8:00-22:00，9月9日起生效）"). That date is the only thing
    distinguishing two otherwise identical settlements, so it is kept rather than
    letting one overwrite the other.
    """
    note = trailing_parenthetical(item)
    if not note:
        return ""
    return clean_text(BAIDU_WINDOW_RE.sub("", note)).strip("，,、;； ")


def band_evidence(items: Iterable[str]) -> tuple[str, list[str]]:
    """Collect the hours behind each band, with the vendor's own wording.

    Both the compact window and the sentence it came from are read off the rows,
    because Baidu states the schedule nowhere else and a schedule may never be
    inferred from another platform.
    """
    hours: dict[str, str] = {}
    quoted: dict[str, str] = {}
    for item in items:
        text = clean_text(item)
        for match in BAIDU_WINDOW_RE.finditer(text):
            band = match.group(1)
            if band in hours:
                continue
            hours[band] = re.sub(r"\s+", "", match.group(2))
            quoted[band] = text
    window = "；".join(f"{band} {value}" for band, value in hours.items())
    return window, list(quoted.values())


def cell_lines(cell: str) -> list[str]:
    """The lines a cell publishes, one for each break the vendor wrote.

    A cell that prices a row twice writes each rate on its own line, so the breaks
    are read before the text is normalised: normalising first runs the two rates
    together and leaves only the first one readable.
    """
    return [
        line
        for line in (clean_text(part) for part in CELL_BREAK_RE.split(cell))
        if line
    ]


def labelled_amounts(lines: Iterable[str]) -> dict[str, str]:
    """Read the rates a cell publishes under labels rather than as one figure.

    The labels are the page's own: ``原价`` for the rate that stands and the
    activity's wording for the rate it promotes. Reading only the first number
    would quote the standing rate and lose the promotion; reading only the second
    would quote a rate that ends as though it were the one that stands.
    """
    amounts: dict[str, str] = {}
    for line in lines:
        match = BAIDU_LABELLED_AMOUNT_RE.match(line)
        if match and match["label"]:
            amounts.setdefault(match["label"], match["amount"])
    return amounts


def activity_card(document: str) -> Promotion | None:
    """Read the page's activity banner: its tag and the window it runs for.

    The banner is laid out one statement per line ("活动时间：", then the window),
    so the window is the line under its label and the tag is the short line above
    it. A page with no banner publishes no activity, and the rates it prices
    simply stand. The page also keeps banners of activities that have already run
    out, which is why the window is read as a window rather than as a rate that
    still applies.
    """
    lines = _text_lines(document)
    for index, line in enumerate(lines):
        if not line.startswith(BAIDU_ACTIVITY_LABEL):
            continue
        window = lines[index + 1] if index + 1 < len(lines) else ""
        if not BAIDU_ACTIVITY_DATE_RE.search(window):
            window = ""
        return Promotion(activity_name(lines, index), window)
    return None


def activity_name(lines: list[str], label_index: int) -> str:
    """The activity's own tag, published above its window."""
    for line in reversed(lines[:label_index]):
        if len(line) <= BAIDU_ACTIVITY_NAME_MAX and line.endswith(BAIDU_PROMOTION_WORD):
            return line
    return BAIDU_GENERIC_PROMOTION


def activity_is_current(window: str, today: date) -> bool:
    """Say whether a published activity window has not already run out.

    A window that has ended is the vendor's own statement that its rate no longer
    applies, so that rate is not read. A window with no readable end date is kept:
    an unreadable date is not evidence that an activity finished.
    """
    end = activity_end_date(window)
    return end is None or end >= today


def activity_tag(name: str) -> str:
    """The part of an activity's name that tells it apart from other activities."""
    return BAIDU_ACTIVITY_NOISE_RE.sub("", clean_text(name))


def same_activity(one: str, other: str) -> bool:
    """Say whether two published activity names are the same activity.

    The page names one activity twice over — in its banner, and in the heading of
    every column it prices — and not always in the same words, so one name counting
    as the other is what makes them one activity rather than an equality of strings.
    """
    first, second = activity_tag(one), activity_tag(other)
    return bool(first) and bool(second) and (first in second or second in first)


def activity_end_date(window: str) -> date | None:
    """The last day an activity window covers, when it names one."""
    days = BAIDU_ACTIVITY_DATE_RE.findall(window or "")
    if not days:
        return None
    match = BAIDU_ACTIVITY_DATE_VALUE_RE.fullmatch(days[-1])
    if not match:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def _text_lines(document: str) -> list[str]:
    """The document's own text lines, tags removed so each statement stands alone."""
    return [
        line
        for line in (
            clean_text(part) for part in HTML_TEXT_BREAK_RE.sub("\n", document).splitlines()
        )
        if line
    ]


class BaiduAdapter(PriceSource):
    provider_id = "baidu"
    provider_name = "百度智能云千帆"
    source_url = BAIDU_PAGE_URL
    source_kind = "official_json"
    region = "中国区"
    currency = "CNY"
    delivery_mode = "platform_hosted"

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._document: str | None = None
        self._catalogue: list[dict[str, Any]] | None = None
        self._source_updated_at: str | None = None
        self._promotion: Promotion | None = None
        self._promotion_read = False
        self._today: date | None = None

    def document_text(self) -> str:
        """Return the article body the page keeps beside the rendered portal."""
        if self._document is None:
            page = self.document(BAIDU_PAGE_URL)
            self._source_updated_at = document_update_stamp(
                page, utc_offset="+08:00"
            )
            try:
                payload = json.loads(self.document(price_data_url(page)))
                body = payload["result"]["data"]["markdownRemark"]["html"]
            except (ValueError, KeyError, TypeError) as exc:
                raise SourceError("unexpected Baidu page-data shape") from exc
            if not isinstance(body, str) or not body.strip():
                raise SourceError("Baidu document published no article body")
            self._document = body
        return self._document

    def promotion(self) -> Promotion | None:
        """The page's activity banner, read once."""
        if not self._promotion_read:
            self._promotion = activity_card(self.document_text())
            self._promotion_read = True
        return self._promotion

    def scan_date(self) -> date:
        """The calendar date this scan runs on, taken once so one run is coherent."""
        if self._today is None:
            self._today = datetime.fromisoformat(now_iso()).date()
        return self._today

    # --- parsing ----------------------------------------------------------

    @staticmethod
    def _columns(headers: list[str]) -> dict[str, Any] | None:
        """Locate the columns a pay-as-you-go token table is read through."""
        def index(label: str) -> int | None:
            return next((i for i, h in enumerate(headers) if h == label), None)

        version = index(BAIDU_VERSION_HEADER)
        item = index(BAIDU_ITEM_HEADER)
        unit = index(BAIDU_UNIT_HEADER)
        channels = {
            position: priced
            for position, header in enumerate(headers)
            if (priced := price_channel(header)) is not None
        }
        if version is None or item is None or unit is None or not channels:
            return None
        return {
            "name": index(BAIDU_MODEL_HEADER),
            "version": version,
            "service": index(BAIDU_SERVICE_HEADER),
            "item": item,
            "unit": unit,
            "channels": channels,
        }

    def _unit_price_columns(
        self, headers: list[str], table: list[list[str]]
    ) -> tuple[int, int | None, dict[int, str]] | None:
        """Locate the model column and the columns that price one charge each.

        The image, OCR, vector, and rerank sections charge one amount per model
        rather than one per settlement channel, and state what it is billed
        against either in a column of its own or beside the amount. So the columns
        are found by what their cells publish, read with the row's own unit as the
        evidence of what the number is.
        """
        name = next(
            (i for i, h in enumerate(headers) if h in (BAIDU_MODEL_HEADER, "模型")),
            None,
        )
        if name is None:
            return None
        unit = next((i for i, h in enumerate(headers) if h == BAIDU_UNIT_HEADER), None)

        def cell(row: list[str], index: int | None) -> str:
            return row[index] if index is not None and index < len(row) else ""

        priced: dict[int, tuple[str, str]] = {}
        for index, header in enumerate(headers):
            if index in (name, unit):
                continue
            if is_commitment_unit(header_unit_phrase(header)):
                # 预付费价格（单位：元/个/月）sells reserved throughput by the
                # month, which is a commitment rather than a model's use.
                continue
            for row in table[1:]:
                for rate in cell_rates(
                    cell(row, index), header=cell(row, unit) or header, currency="CNY"
                ):
                    priced[index] = (price_kind(header) or "price", header)
                    break
                if index in priced:
                    break
        return (name, unit, priced) if priced else None

    def _unit_price_entries(
        self,
        cells: list[str],
        columns: tuple[int, int | None, dict[int, tuple[str, str]]],
        width: int,
        headings: list[str],
    ) -> list[dict[str, Any]]:
        """Read one row of a one-price-per-model table."""
        cells = [*cells, *([""] * (width - len(cells)))][:width]
        name_index, unit_index, priced = columns

        def cell(index: int | None) -> str:
            return cells[index] if index is not None and index < len(cells) else ""

        name, name_note = split_trailing_parenthetical(first_cell_line(cell(name_index)))
        if not name:
            return []
        unit_label = clean_text(cell(unit_index))
        conditions: dict[str, Any] = {
            "billing_mode": "pay_as_you_go",
            "source_section": " / ".join(headings),
        }
        if name_note:
            conditions["model_note"] = name_note
        entries = []
        for index, (kind, header) in priced.items():
            label = clean_text(header)
            item = label if label in BAIDU_PRICE_HEADERS else ""
            for rate in cell_rates(cell(index), header=unit_label or label, currency="CNY"):
                # Baidu files most prices per thousand tokens; the report compares
                # one unit, and a charge per page or per image has no scale to take.
                tokens = tokens_per_price_unit(rate.unit_phrase or unit_label)
                entries.append(
                    {
                        "model_id": normalize_model(name),
                        "display_name": name,
                        "item": item,
                        "offer_name": item or "pay_as_you_go",
                        "conditions": dict(conditions),
                        "price": price_item(
                            price_kind(item) or kind,
                            item or label,
                            (
                                per_million_tokens(rate.amount, tokens)
                                if tokens
                                else rate.amount
                            ),
                            (
                                "CNY_per_million_tokens"
                                if tokens
                                else unit_code(rate.unit_phrase or unit_label, "CNY")
                            ),
                            display=rate.display,
                            list_amount=(
                                per_million_tokens(rate.list_amount, tokens)
                                if tokens and rate.list_amount
                                else rate.list_amount
                            ),
                            discount=rate.discount,
                        ),
                    }
                )
        return entries

    def _rates(self, cell: str, unit_label: str, activity: str) -> list[BaiduRate]:
        """Read one price cell into every rate that is still current.

        A cell that labels its rates publishes several; a cell with one bare figure
        publishes the channel's own rate, which belongs to an activity only when the
        column it sits under names one the page still announces.
        """
        if activity and not self._column_activity_stands(activity):
            return []
        lines = cell_lines(cell)
        amounts = labelled_amounts(lines)
        if amounts:
            listed = amounts.get(BAIDU_LIST_PRICE_LABEL)
            promoted = self.promotion() or Promotion(BAIDU_GENERIC_PROMOTION)
            rates = [BaiduRate(listed, None, None, listed)] if listed is not None else []
            rates.extend(
                BaiduRate(amount, listed, promoted, amount)
                for label, amount in amounts.items()
                if label != BAIDU_LIST_PRICE_LABEL
            )
        else:
            figure = " ".join(lines)
            amount = monetary_amount(figure, unit_label, self.currency)
            rates = (
                [BaiduRate(amount, None, self.promotion() if activity else None, figure)]
                if amount is not None
                else []
            )
        # An activity states how long its rate stands, so a rate whose window has run
        # out is not the rate a purchase is billed at — however the page priced it.
        today = self.scan_date()
        return [
            rate
            for rate in rates
            if rate.promotion is None or activity_is_current(rate.promotion.window, today)
        ]

    def _column_activity_stands(self, activity: str) -> bool:
        """Say whether the page still announces the activity a column names.

        A column dates nothing: it carries no window, and no standing rate beside
        its figure. The banner is the only place the page states which activity is
        running and until when, so a column naming an activity the banner does not
        carry prices a promotion the page has stopped running, and its figure is not
        the rate a purchase is billed at.
        """
        banner = self.promotion()
        return banner is not None and same_activity(activity, banner.name)

    def _entries(
        self,
        cells: list[str],
        columns: dict[str, Any],
        width: int,
        headings: list[str],
    ) -> list[dict[str, Any]]:
        """Read one table row into one entry per channel and rate it prices."""
        cells = [*cells, *([""] * (width - len(cells)))][:width]

        def raw(key: str) -> str:
            position = columns.get(key)
            return cells[position] if position is not None else ""

        def value(key: str) -> str:
            return clean_text(raw(key))

        item = value("item")
        kind = price_kind(item)
        # Baidu files most prices per thousand tokens and a few per page; a unit
        # this tool cannot read is the only one that prices nothing here.
        unit_label = value("unit")
        tokens = tokens_per_price_unit(unit_label)
        if tokens is None and unit_measure(unit_label) is None:
            return []
        # A version cell lists every variant at this price, one per line; the
        # first is the model the row is about.
        version, version_note = split_trailing_parenthetical(
            first_cell_line(raw("version"))
        )
        if not version or kind is None:
            return []
        # Baidu files a price per thousand tokens; the report compares one unit.
        # The raw figure travels in the price's display text so the vendor's own
        # number stays checkable against its page.
        # Baidu repeats a model once per settlement and says which one it is only
        # in the name's parenthetical ("…（8月24日生效）"). Dropping that would
        # merge two different sets of prices into one offer.
        name, name_note = split_trailing_parenthetical(
            first_cell_line(raw("name")) or version
        )
        conditions: dict[str, Any] = {
            "billing_mode": "pay_as_you_go",
            "source_section": " / ".join(headings),
        }
        if name_note:
            conditions["model_note"] = name_note
        if band := time_band_label(item):
            conditions["time_band"] = band
        if note := price_note(item):
            conditions["effective_from"] = note
        # Baidu folds the context tier into the service column rather than giving
        # it a column of its own ("推理服务 输入Token数：[0,32k]").
        if tier := re.sub(rf"^{BAIDU_PLAIN_SERVICE}\s*", "", value("service")).strip():
            conditions["context_tier"] = tier
        if version_note == BAIDU_RETIRING_MARKER:
            conditions["release_stage"] = "retiring"
        model_id = normalize_model(version)
        billing_unit = "CNY_per_million_tokens" if tokens else unit_code(unit_label)

        def rescale(amount: str) -> str:
            """One amount on the unit this report compares."""
            return per_million_tokens(amount, tokens) if tokens else amount

        entries = []
        for position, (channel, activity) in columns["channels"].items():
            # The cell is handed on as the page wrote it: its line breaks are what
            # separate the rates it publishes, and reading them is the reader's job.
            figure = cells[position] if position < width else ""
            for rate in self._rates(figure, unit_label, activity):
                entries.append(
                    {
                        "model_id": model_id,
                        "display_name": name,
                        "item": item,
                        "offer_name": rate.promotion.name if rate.promotion else channel,
                        "conditions": self._conditions(
                            conditions, rate.promotion, channel
                        ),
                        "price": price_item(
                            kind,
                            item,
                            rescale(rate.amount),
                            billing_unit,
                            display=self._display(rate, unit_label),
                            list_amount=(
                                rescale(rate.list_amount) if rate.list_amount else None
                            ),
                        ),
                    }
                )
        if not entries:
            entries.append(
                {
                    "model_id": model_id,
                    "display_name": name,
                    "item": item,
                    "offer_name": next(iter(columns["channels"].values()))[0],
                    "conditions": dict(conditions),
                    "price": None,
                }
            )
        return entries

    @staticmethod
    def _conditions(
        shared: dict[str, Any], promotion: Promotion | None, channel: str
    ) -> dict[str, Any]:
        """Add what says where an activity's rate applies and for how long.

        A standing rate is already named after its channel, so naming the channel
        again would be a stutter; an activity's name takes the offer's, so the
        channel it prices has nowhere else to be said and would otherwise merge the
        batch rate into the real-time one's offer. An activity with no published
        window adds none: the offer's own name already says which activity it
        prices, and inventing a period for it would claim a date the vendor never
        gave.
        """
        conditions = dict(shared)
        if promotion is None:
            return conditions
        conditions["channel"] = channel
        if promotion.window:
            conditions["promotion_window"] = promotion.window
        return conditions

    @staticmethod
    def _display(rate: BaiduRate, unit_label: str) -> str:
        """Show the rate the way the page published it.

        A charge of nothing has no figure to rescale, so it keeps the vendor's own
        word for it rather than being rendered as a zero beside a unit it is not
        billed in.
        """
        if is_free_amount(rate.amount):
            return rate.text or BAIDU_GENERIC_PROMOTION
        return f"{rate.amount} {unit_label}"

    def _records(self) -> list[dict[str, Any]]:
        """Group every priced entry into one record per model."""
        if self._catalogue is not None:
            return self._catalogue
        entries: list[dict[str, Any]] = []
        for headings, table in headed_document_tables(self.document_text()):
            if len(table) < 2:
                continue
            headers = [clean_text(cell) for cell in table[0]]
            columns = self._columns(headers)
            reader = self._entries if columns is not None else None
            if columns is None:
                columns = self._unit_price_columns(headers, table)
                reader = self._unit_price_entries if columns is not None else None
            if reader is None:
                continue
            for cells in table[1:]:
                entries.extend(reader(cells, columns, len(headers), headings))
        if not entries:
            raise SourceError("Baidu pay-as-you-go pricing table was not found")
        self._catalogue = [
            self._record(model_id, group)
            for model_id, group in self._group_by_model(entries).items()
        ]
        return self._catalogue

    @staticmethod
    def _group_by_model(
        entries: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            grouped.setdefault(entry["model_id"], []).append(entry)
        return grouped

    def _record(self, model_id: str, group: list[dict[str, Any]]) -> dict[str, Any]:
        """Merge a model's entries into offers, one per offer and settlement."""
        offers: dict[tuple[Any, ...], dict[str, Any]] = {}
        for entry in group:
            if entry["price"] is None:
                continue
            key = (entry["offer_name"], tuple(sorted(entry["conditions"].items())))
            offer = offers.setdefault(
                key,
                {
                    "name": entry["offer_name"],
                    "conditions": dict(entry["conditions"]),
                    "prices": [],
                },
            )
            offer["prices"].append(entry["price"])
        return make_record(
            self.provider_id,
            self.provider_name,
            model_id,
            group[0]["display_name"],
            self.region,
            list(offers.values()),
            self.source_url,
            self.source_kind,
            now_iso(),
            currency=self.currency,
            delivery_mode=self.delivery_mode,
            model_family=model_family(model_id),
            source_updated_at=self._source_updated_at,
            time_bands=self._time_bands(group),
        )

    def _time_bands(self, group: list[dict[str, Any]]) -> dict[str, Any]:
        window, statements = band_evidence(entry["item"] for entry in group)
        if not window:
            return {}
        return {
            "window": window,
            "statements": statements,
            "source_url": self.source_url,
        }

    # --- catalogue --------------------------------------------------------

    def list_models(self, prefix: str = "") -> list[str]:
        models = {record["model_id"] for record in self._records()}
        if prefix:
            key = normalize_model(prefix)
            models = {model for model in models if normalize_model(model).startswith(key)}
        return sorted(models, key=str.lower)

    def catalog_records(self) -> list[dict[str, Any]]:
        """The Qianfan article body is grouped into its models in one read."""
        return self._records()

    def query(self, model: str) -> list[dict[str, Any]]:
        key = normalize_model(model)
        return [
            record
            for record in self._records()
            if normalize_model(record["model_id"]) == key
        ]
