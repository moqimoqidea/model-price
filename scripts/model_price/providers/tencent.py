"""Tencent Cloud TokenHub pricing, read from the document's slate payload."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from ..core import PriceSource, now_iso
from ..errors import SourceError
from ..models import (
    model_family,
    model_matches,
    normalize_model,
    without_trailing_parenthetical,
)
from ..parsing import (
    SpanGrid,
    cell_rates,
    header_unit_phrase,
    normalize_update_stamp,
    price_column_kinds,
    price_unit_code,
    time_bands_for,
)
from ..pricing import (
    is_credit_unit,
    make_record,
    price_item,
    unit_measure,
)
from ..text import clean_text

TENCENT_LIST_URL = "https://cloud.tencent.com/document/product/1823/130051"
TENCENT_PRICE_URL = "https://cloud.tencent.com/document/product/1823/130055"
# Two generations on this page are billed on different calendars: the 原厂直供
# series follows the first party (weekends are off-peak), while the 0731/0813
# builds it hosts itself stay on peak at weekends. The delivery label is what
# tells them apart, so it is used to pick the rule when no model name matches.
TENCENT_BAND_LABELS = ("原厂直供",)


def extract_tencent_article(page: str) -> dict[str, Any]:
    """Return the official article payload embedded in a Tencent document page."""
    match = re.search(
        r"window\.__staticRouterHydrationData\s*=\s*JSON\.parse\s*\("
        r"(?P<quoted>\"(?:\\.|[^\"\\])*\")\s*\)",
        page,
    )
    if not match:
        raise SourceError("Tencent document state was not found")
    try:
        state = json.loads(json.loads(match.group("quoted")))
        article = state["loaderData"]["product-article"]["data"]["article"][
            "content"
        ]
        if not isinstance(article, dict):
            raise SourceError("Tencent article content was not an object")
        return article
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SourceError("unexpected Tencent document state") from exc


def article_slate(article: dict[str, Any]) -> list[dict[str, Any]]:
    """Decode the Slate node list carried by one Tencent article payload."""
    try:
        slate: Any = article["slate"]
        for _ in range(3):
            if not isinstance(slate, str):
                break
            slate = json.loads(slate)
        if not isinstance(slate, list):
            raise SourceError("Tencent slate is not a node list")
        return slate
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SourceError("unexpected Tencent document state") from exc


def extract_tencent_slate(page: str) -> list[dict[str, Any]]:
    """Compatibility entry point for callers that only need the Slate nodes."""
    return article_slate(extract_tencent_article(page))


def object_text(node: Any) -> str:
    if isinstance(node, dict):
        own = str(node.get("text", ""))
        return own + "".join(object_text(v) for k, v in node.items() if k != "text")
    if isinstance(node, list):
        return "".join(object_text(v) for v in node)
    return ""


def cell_text(cell: dict[str, Any]) -> str:
    paragraphs = []
    for child in cell.get("children", []):
        text = re.sub(r"\s+", " ", object_text(child)).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def walk_objects(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk_objects(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_objects(value)


def expand_slate_table(table: dict[str, Any]) -> list[list[str]]:
    """Flatten a slate table, repeating values across row and column spans."""
    grid = SpanGrid()
    for raw_row in table.get("children", []):
        if raw_row.get("type") != "row":
            continue
        grid.start_row()
        for cell in raw_row.get("children", []):
            if cell.get("type") != "cell":
                continue
            # Slate spells a cell covered by the span above it as 0/0.
            if cell.get("rowSpan") == 0 and cell.get("colSpan") == 0:
                grid.skip()
                continue
            grid.add(
                cell_text(cell),
                rowspan=max(1, int(cell.get("rowSpan", 1))),
                colspan=max(1, int(cell.get("colSpan", 1))),
            )
        grid.end_row()
    return grid.rows


def tencent_delivery_mode(display_name: str) -> str:
    return "upstream_direct" if "原厂直供" in display_name else "self_deployed"


def slate_price_tables(
    slate: list[dict[str, Any]],
) -> Iterator[tuple[tuple[str, ...], str, list[list[str]]]]:
    """Walk the document in order, giving each table the headings above it.

    A table belongs to the section it sits in, and the region variants sit one
    level deeper, inside the tab that names the region. Following the document
    rather than hunting for one known table is what lets every product line be
    read: the language models are priced in one section, images in another, and
    the same header wording means different charges in each.
    """

    def walk(
        nodes: Iterable[Any], path: tuple[str, ...], tab: str
    ) -> Iterator[tuple[tuple[str, ...], str, list[list[str]]]]:
        for node in nodes:
            if not isinstance(node, dict):
                continue
            kind = node.get("type")
            if kind in ("h2", "h3", "h4"):
                title = clean_text(object_text(node))
                if title:
                    path = (*path[: int(kind[1]) - 1], title)
                continue
            if kind == "table":
                rows = expand_slate_table(node)
                if rows:
                    yield path, tab, rows
                continue
            name = str(node.get("name") or "").strip()
            if kind == "tab" and name:
                yield from walk(node.get("children") or [], (*path, name), name)
                continue
            yield from walk(node.get("children") or [], path, tab)

    yield from walk(slate, (), "")


# The columns that say what a rate covers, in the wording the page keeps them in.
# The peak/off-peak column is filed under one name whatever the page calls it,
# because the report reads it as a band rather than as one more condition.
TENCENT_CONDITION_KEYS = (("峰谷", "time_band"), ("条件", "condition"))


def tencent_condition_key(header: str) -> str:
    text = clean_text(header)
    return next((key for marker, key in TENCENT_CONDITION_KEYS if marker in text), text)


def tencent_price_label(header: str) -> str:
    """Name a charge the way the page heads it, without repeating its unit."""
    return clean_text(header).split("（", 1)[0].strip() or clean_text(header)


def tencent_readable_unit(header: str, cells: Iterable[str]) -> bool:
    """Whether a column is billed in a unit this tool can read.

    The unit is in the header on some tables and beside each amount on others, so
    both are consulted. Reserved throughput (元/kTPM/月) is billed in a unit that
    prices no use of a model, and reading it would put a capacity product beside a
    model's price.
    """
    phrase = header_unit_phrase(header)
    if unit_measure(phrase) or is_credit_unit(phrase):
        return True
    return any(
        rate.unit_phrase
        and (unit_measure(rate.unit_phrase) or is_credit_unit(rate.unit_phrase))
        for cell in cells
        for rate in cell_rates(cell, header=header)
    )


class TencentAdapter(PriceSource):
    provider_id = "tencent"
    provider_name = "腾讯云 TokenHub"
    source_url = TENCENT_PRICE_URL
    source_kind = "official_document"
    catalog_url = TENCENT_LIST_URL

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self._article: dict[str, Any] | None = None
        self._slate: list[dict[str, Any]] | None = None
        self._band_text: str | None = None

    def _price_article(self) -> dict[str, Any]:
        """Return the price article once, including its official update stamp."""
        if self._article is None:
            self._article = extract_tencent_article(
                self.document(TENCENT_PRICE_URL)
            )
        return self._article

    def _price_slate(self) -> list[dict[str, Any]]:
        """Return the price page's slate once, for both tables and prose."""
        if self._slate is None:
            self._slate = article_slate(self._price_article())
        return self._slate

    def source_updated_at(self) -> str | None:
        """Return the price article's labelled recent-release moment."""
        return normalize_update_stamp(
            str(self._price_article().get("recentReleaseTime") or ""),
            utc_offset="+08:00",
        )

    def _band_document(self) -> str:
        """Return the page's prose, where the peak/off-peak window is written."""
        if self._band_text is None:
            self._band_text = object_text(self._price_slate())
        return self._band_text

    def _catalog(self) -> list[dict[str, str]]:
        slate = extract_tencent_slate(self.document(TENCENT_LIST_URL))
        entries: list[dict[str, str]] = []
        for node in walk_objects(slate):
            if node.get("type") != "table":
                continue
            rows = expand_slate_table(node)
            if not rows:
                continue
            headers = [clean_text(header) for header in rows[0]]
            # The speech table heads its first column 模型类型 (a product category)
            # and names the model in the next one, so an exact label wins over a
            # column that merely mentions a model.
            name_index = (
                headers.index("模型名称")
                if "模型名称" in headers
                else next((i for i, h in enumerate(headers) if "模型" in h), None)
            )
            # The call-parameter column is labelled "model（调用参数）"; match it by
            # wording rather than by exact punctuation.
            id_index = next(
                (i for i, h in enumerate(headers) if "调用参数" in h),
                None,
            )
            if id_index is None:
                id_index = next(
                    (i for i, h in enumerate(headers) if "model" in h.lower()),
                    None,
                )
            if name_index is None or id_index is None:
                continue
            for row in rows[1:]:
                if max(name_index, id_index) >= len(row):
                    continue
                for model_id in row[id_index].splitlines():
                    model_id = model_id.strip()
                    if model_id:
                        display_name = row[name_index].strip()
                        entries.append(
                            {
                                "model_id": model_id,
                                "display_name": display_name,
                                "delivery_mode": tencent_delivery_mode(display_name),
                            }
                        )
        return entries

    def list_models(self, prefix: str = "") -> list[str]:
        models = {entry["model_id"] for entry in self._catalog()}
        if prefix:
            normalized = normalize_model(prefix)
            models = {m for m in models if normalize_model(m).startswith(normalized)}
        return sorted(models, key=str.lower)

    @staticmethod
    def _model_column(headers: list[str]) -> int | None:
        for label in ("模型名称", "模型"):
            if label in headers:
                return headers.index(label)
        return None

    def _price_offers(self) -> dict[str, list[dict[str, Any]]]:
        """Read every table on the price page that prices a model's use.

        One table prices one product line — tokens, images, seconds of video,
        songs, calls — and the cells inside it can publish several tiers at once.
        All of it is read the same way, and a column whose unit this tool cannot
        read is left to the page.
        """
        offers_by_name: dict[str, list[dict[str, Any]]] = {}
        for path, tab, rows in slate_price_tables(self._price_slate()):
            headers = [clean_text(header) for header in rows[0]]
            name_index = self._model_column(headers)
            if name_index is None:
                continue
            if any("旧计费方式" in h for h in headers) and any(
                "新计费方式" in h for h in headers
            ):
                # The page keeps a table per retired generation that sets the price
                # it charged beside the one that replaced it. The replacement has
                # its own billing table, so reading the comparison would report the
                # same model twice under two price lists.
                continue
            priced = price_column_kinds(headers, rows, currency="CNY")
            priced = {
                index: kind
                for index, kind in priced.items()
                if tencent_readable_unit(
                    headers[index],
                    [row[index] for row in rows[1:] if index < len(row)],
                )
            }
            if not priced:
                continue
            for row in rows[1:]:
                if name_index >= len(row):
                    continue
                display_name = clean_text(row[name_index])
                if not display_name or display_name in ("-", "—"):
                    continue
                # One model is sold in several regions at different rates, and the
                # region is the tab the table sits in. Two regions are two offers:
                # merging them would quote one region's price for the other.
                conditions: dict[str, Any] = {"region": tab} if tab else {}
                for index, header in enumerate(headers):
                    if index in priced or index == name_index or index >= len(row):
                        continue
                    value = clean_text(row[index])
                    if value and value not in ("-", "—"):
                        conditions[tencent_condition_key(header)] = value
                for index, kind in priced.items():
                    if index >= len(row):
                        continue
                    for rate in cell_rates(row[index], header=headers[index]):
                        offer_conditions = dict(conditions)
                        if rate.conditions:
                            offer_conditions["price_scope"] = "；".join(rate.conditions)
                        priced_offer = {
                            "name": (
                                "online_standard"
                                if not offer_conditions
                                else "online_conditional"
                            ),
                            "conditions": offer_conditions,
                            "prices": [
                                price_item(
                                    kind,
                                    tencent_price_label(headers[index]),
                                    rate.amount,
                                    price_unit_code(
                                        rate.unit_phrase,
                                        headers[index],
                                        currency="CNY",
                                        default="CNY_per_million_tokens",
                                    ),
                                    display=rate.display,
                                    list_amount=rate.list_amount,
                                    discount=rate.discount,
                                )
                            ],
                        }
                        found = offers_by_name.setdefault(
                            normalize_model(display_name), []
                        )
                        merged = next(
                            (
                                offer
                                for offer in found
                                if offer["name"] == priced_offer["name"]
                                and offer["conditions"] == priced_offer["conditions"]
                            ),
                            None,
                        )
                        if merged is None:
                            found.append(priced_offer)
                        else:
                            merged["prices"].extend(priced_offer["prices"])
        return offers_by_name

    def _records(self) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, str]]] = {}
        for entry in self._catalog():
            # The catalogue marks a retiring generation in the name itself
            # ("Kling-Video-v3\n（2026-09-15下线）") while the price page prices it
            # under the bare name, so the two are matched without that suffix.
            grouped.setdefault(
                normalize_model(without_trailing_parenthetical(entry["display_name"])),
                [],
            ).append(entry)
        offers_by_name = self._price_offers()
        retrieved_at = now_iso()
        records = []
        for name_key, entries in grouped.items():
            offers = offers_by_name.get(name_key, [])
            aliases = sorted(
                {entry["model_id"] for entry in entries},
                key=lambda value: (len(value), value),
            )
            display_name = entries[0]["display_name"]
            delivery_mode = entries[0]["delivery_mode"]
            records.append(
                make_record(
                    self.provider_id,
                    self.provider_name,
                    aliases[0],
                    display_name,
                    "中国区（广州）",
                    offers,
                    self.source_url,
                    self.source_kind,
                    retrieved_at,
                    catalog_url=TENCENT_LIST_URL,
                    model_aliases=aliases,
                    delivery_mode=delivery_mode,
                    model_family=model_family(display_name),
                    source_updated_at=self.source_updated_at(),
                    time_bands=(
                        time_bands_for(
                            self._band_document(),
                            model_id=aliases[0],
                            display_name=display_name,
                            labels=(
                                TENCENT_BAND_LABELS
                                if delivery_mode == "upstream_direct"
                                else ()
                            ),
                            source_url=self.source_url,
                        )
                        if offers
                        else {}
                    ),
                )
            )
        return records

    def catalog_records(self) -> list[dict[str, Any]]:
        """TokenHub's whole catalogue is already built from one document pass."""
        return self._records()

    def query(self, model: str) -> list[dict[str, Any]]:
        query_key = normalize_model(model)
        return [
            record
            for record in self._records()
            if query_key
            in {
                normalize_model(record["display_name"]),
                *map(normalize_model, record["model_aliases"]),
            }
        ]

    def search(self, model: str, *, exact: bool = False) -> list[dict[str, Any]]:
        return [
            record
            for record in self._records()
            if model_matches(model, record["display_name"], exact=exact)
            or any(
                model_matches(model, alias, exact=exact)
                for alias in record["model_aliases"]
            )
        ]
