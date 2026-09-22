"""Tencent Cloud TokenHub pricing, read from the document's slate payload."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from ..core import PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, model_matches, normalize_model
from ..parsing import SpanGrid, normalize_update_stamp, time_bands_for
from ..pricing import make_record, price_item
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
            name_index = next((i for i, h in enumerate(headers) if "模型" in h), None)
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

    def _price_offers(self) -> dict[str, list[dict[str, Any]]]:
        slate = self._price_slate()
        offers_by_name: dict[str, list[dict[str, Any]]] = {}
        # Prefer the 广州 region tab, but fall back to any tab that carries price
        # tables so a renamed or added region tab does not blank the provider.
        tabs = [node for node in walk_objects(slate) if node.get("type") == "tab"]
        region_tabs = [tab for tab in tabs if "广州" in str(tab.get("name", ""))] or tabs
        for node in region_tabs:
            for child in walk_objects(node.get("children", [])):
                if child.get("type") != "table":
                    continue
                rows = expand_slate_table(child)
                if not rows:
                    continue
                headers = rows[0]
                if not any("推理输入" in h for h in headers):
                    continue

                def index_containing(label: str) -> int | None:
                    return next((i for i, h in enumerate(headers) if label in h), None)

                indexes = {
                    "name": index_containing("模型名称"),
                    "condition": index_containing("条件"),
                    "time_band": index_containing("峰谷计费"),
                    "input": index_containing("推理输入"),
                    "output": index_containing("推理输出"),
                    "cache_hit": index_containing("缓存命中"),
                }
                for row in rows[1:]:
                    name_pos = indexes["name"]
                    if name_pos is None or name_pos >= len(row):
                        continue
                    display_name = clean_text(row[name_pos])
                    if not display_name:
                        continue
                    prices = []
                    for kind in ("input", "output", "cache_hit"):
                        position = indexes[kind]
                        if (
                            position is None
                            or position >= len(row)
                            or row[position] in ("", "-")
                        ):
                            continue
                        prices.append(
                            price_item(
                                kind,
                                clean_text(headers[position].split("（", 1)[0]),
                                row[position],
                                "CNY_per_million_tokens",
                            )
                        )
                    conditions = {}
                    for key in ("condition", "time_band"):
                        position = indexes[key]
                        if (
                            position is not None
                            and position < len(row)
                            and row[position] not in ("", "-")
                        ):
                            conditions[key] = row[position]
                    if not prices:
                        continue
                    offers_by_name.setdefault(normalize_model(display_name), []).append(
                        {
                            "name": (
                                "online_standard"
                                if not conditions
                                else "online_conditional"
                            ),
                            "conditions": conditions,
                            "prices": prices,
                        }
                    )
        return offers_by_name

    def _records(self) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, str]]] = {}
        for entry in self._catalog():
            grouped.setdefault(normalize_model(entry["display_name"]), []).append(entry)
        offers_by_name = self._price_offers()
        retrieved_at = now_iso()
        records = []
        for name_key, entries in grouped.items():
            offers = offers_by_name.get(name_key, [])
            if not offers:
                continue
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
                    time_bands=time_bands_for(
                        self._band_document(),
                        model_id=aliases[0],
                        display_name=display_name,
                        labels=(
                            TENCENT_BAND_LABELS
                            if delivery_mode == "upstream_direct"
                            else ()
                        ),
                        source_url=self.source_url,
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
