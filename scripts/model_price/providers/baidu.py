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
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable
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
    document_update_stamp,
    headed_document_tables,
    monetary_amount,
    time_band_label,
    token_price_kind,
)
from ..pricing import make_record, per_million_tokens, price_item, tokens_per_price_unit
from ..text import clean_text, first_cell_line

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
BAIDU_PLAIN_SERVICE = "推理服务"
# One billing item is priced per serving channel, and the batch column repeats
# once per promotion ("批量推理 （2月活动价）"). Only the undiscounted column is
# read, so a promotion that expires is never quoted as the price.
BAIDU_CHANNELS = ("在线推理", "批量推理")
BAIDU_PROMOTION_MARKER = "活动价"
BAIDU_RETIRING_MARKER = "即将下线"
# The window lives in the item name ("输入（高峰时段：8:00-22:00）").
BAIDU_WINDOW_RE = re.compile(
    r"(高峰时段|空闲时段|低峰时段|低谷时段)[：:]\s*"
    r"(\d{1,2}:\d{2}\s*[-–—~～]\s*(?:次日\s*)?\d{1,2}:\d{2})"
)


def price_data_url(page: str) -> str:
    """Read the article's own data address out of the page pre-fetching it."""
    match = BAIDU_PAGE_DATA_RE.search(page)
    if not match:
        raise SourceError("Baidu document published no page-data link")
    return urljoin(BAIDU_PAGE_URL, match.group(1))


def price_channel(header: str) -> str | None:
    """Name the serving channel a price column belongs to."""
    label = clean_text(header)
    if BAIDU_PROMOTION_MARKER in label:
        return None
    return next((channel for channel in BAIDU_CHANNELS if channel in label), None)


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
            position: channel
            for position, header in enumerate(headers)
            if (channel := price_channel(header)) is not None
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

    def _entries(
        self,
        cells: list[str],
        columns: dict[str, Any],
        width: int,
        headings: list[str],
    ) -> list[dict[str, Any]]:
        """Read one table row into one entry per channel that prices it."""
        cells = [*cells, *([""] * (width - len(cells)))][:width]

        def raw(key: str) -> str:
            position = columns.get(key)
            return cells[position] if position is not None else ""

        def value(key: str) -> str:
            return clean_text(raw(key))

        item = value("item")
        kind = token_price_kind(item)
        tokens = tokens_per_price_unit(value("unit"))
        # A version cell lists every variant at this price, one per line; the
        # first is the model the row is about.
        version, version_note = split_trailing_parenthetical(
            first_cell_line(raw("version"))
        )
        if not version or kind is None or tokens is None:
            return []
        # Baidu files a price per thousand tokens; the report compares one unit.
        # The raw figure travels in the price's display text so the vendor's own
        # number stays checkable against its page.
        unit_label = value("unit")
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
        entries = []
        for position, channel in columns["channels"].items():
            figure = clean_text(cells[position]) if position < width else ""
            amount = monetary_amount(figure, unit_label, self.currency)
            if amount is None:
                continue
            entries.append(
                {
                    "model_id": normalize_model(version),
                    "display_name": name,
                    "item": item,
                    "channel": channel,
                    "conditions": conditions,
                    "price": price_item(
                        kind,
                        item,
                        per_million_tokens(amount, tokens),
                        "CNY_per_million_tokens",
                        display=f"{amount} {unit_label}",
                    ),
                }
            )
        if not entries:
            entries.append(
                {
                    "model_id": normalize_model(version),
                    "display_name": name,
                    "item": item,
                    "channel": None,
                    "conditions": conditions,
                    "price": None,
                }
            )
        return entries

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
            if columns is None:
                continue
            for cells in table[1:]:
                entries.extend(self._entries(cells, columns, len(headers), headings))
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
        """Merge a model's entries into offers, one per channel and settlement."""
        offers: dict[tuple[Any, ...], dict[str, Any]] = {}
        for entry in group:
            if entry["price"] is None:
                continue
            key = (entry["channel"], tuple(sorted(entry["conditions"].items())))
            offer = offers.setdefault(
                key,
                {
                    "name": entry["channel"],
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
