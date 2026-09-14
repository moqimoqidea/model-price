"""Volcengine Ark (方舟) pricing, read from its structured document JSON."""

from __future__ import annotations

import json
from typing import Any, Iterable

from ..core import PriceSource, now_iso
from ..errors import SourceError
from ..models import model_family, model_matches, normalize_model
from ..pricing import make_record, price_item, unit_code
from ..text import clean_text, numeric_values

VOLCENGINE_PAGE_URL = "https://docs.volcengine.com/docs/82379/1544106"
VOLCENGINE_DOC_API = (
    "https://docs.volcengine.com/api/doc/getDocDetail"
    "?DocumentID=1544106&LibraryID=82379&lang=zh"
)


class VolcDocument:
    """Read Volcengine's structured document JSON and its embedded tables."""

    def __init__(self, payload: dict[str, Any]) -> None:
        try:
            result = payload["Result"]
            if result.get("ContentType") != "json":
                raise SourceError("Volcengine pricing document is not structured JSON")
            content = json.loads(result["Content"])
            self.data = content["data"]
            self.updated_at = result.get("UpdatedTime")
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise SourceError("unexpected Volcengine document response") from exc

    def _text(self, zone_id: str, seen: set[str] | None = None) -> str:
        seen = set() if seen is None else seen
        if zone_id in seen:
            return ""
        seen.add(zone_id)
        pieces = []
        for op in self.data.get(zone_id, {}).get("ops", []):
            value = op.get("insert", "")
            if isinstance(value, str):
                if value == "*" and op.get("attributes", {}).get("lineId"):
                    continue
                pieces.append(value)
            elif isinstance(value, dict) and value.get("id"):
                pieces.append(self._text(value["id"], seen))
        raw = "".join(pieces)
        return "\n".join(
            line for line in (clean_text(line) for line in raw.splitlines()) if line
        )

    def _table(self, reference: str) -> list[list[str]]:
        try:
            row_zone, column_zone = reference.split()[:2]
            rows = [
                op["insert"]["id"]
                for op in self.data[row_zone]["ops"]
                if isinstance(op.get("insert"), dict) and op["insert"].get("id")
            ]
            columns = [
                op["insert"]["id"]
                for op in self.data[column_zone]["ops"]
                if isinstance(op.get("insert"), dict) and op["insert"].get("id")
            ]
        except (KeyError, ValueError) as exc:
            raise SourceError("unexpected Volcengine table structure") from exc
        return [
            [self._text(f"x{row_id}x{column_id}") for column_id in columns]
            for row_id in rows
        ]

    def tables(self) -> Iterable[tuple[list[str], list[list[str]]]]:
        headings: list[str] = []
        pending_level: int | None = None
        try:
            operations = self.data["0"]["ops"]
        except (KeyError, TypeError) as exc:
            raise SourceError("Volcengine document root was not found") from exc
        for op in operations:
            attributes = op.get("attributes", {})
            heading = attributes.get("heading")
            if isinstance(heading, str) and heading.startswith("h"):
                pending_level = int(heading[1:])
            value = op.get("insert")
            if (
                pending_level
                and isinstance(value, str)
                and value.strip() not in ("", "*")
            ):
                title = clean_text(value)
                headings = headings[: pending_level - 1]
                headings.append(title)
                pending_level = None
            if attributes.get("aceTable"):
                yield list(headings), self._table(attributes["aceTable"])


def volc_offer_name(headings: list[str]) -> str:
    title = " / ".join(headings)
    if "低延迟" in title:
        return "online_low_latency"
    if "批量推理" in title:
        return "batch"
    if "TPM" in title:
        return "tpm_package"
    return "online_standard"


def price_type_from_header(header: str) -> str:
    compact = clean_text(header).replace(" ", "")
    if "缓存存储" in compact:
        return "cache_storage"
    if "缓存命中" in compact and "音频" in compact and "非音频" not in compact:
        return "audio_cache_hit"
    if "缓存命中" in compact:
        return "cache_hit"
    if "输入" in compact and "音频" in compact and "非音频" not in compact:
        return "audio_input"
    if "输入" in compact:
        return "input"
    if "输出" in compact:
        return "output"
    return "other"


def amount_from_cell(value: str) -> str | None:
    if clean_text(value) in ("", "-"):
        return None
    values = numeric_values(value)
    return values[-1] if values else None


class VolcengineAdapter(PriceSource):
    provider_id = "volcengine"
    provider_name = "火山引擎方舟"
    source_url = VOLCENGINE_PAGE_URL
    source_kind = "official_document_json"
    catalog_url = VOLCENGINE_PAGE_URL

    def _document(self) -> VolcDocument:
        return VolcDocument(json.loads(self.client.get_text(VOLCENGINE_DOC_API)))

    def _records(self) -> list[dict[str, Any]]:
        document = self._document()
        retrieved_at = now_iso()
        grouped: dict[str, dict[str, Any]] = {}
        for headings, table in document.tables():
            if len(table) < 2:
                continue
            headers = table[0]
            model_index = next(
                (
                    i
                    for i, header in enumerate(headers)
                    if clean_text(header) in ("模型", "模型名称")
                ),
                None,
            )
            if model_index is None or not any(
                price_type_from_header(h) != "other" for h in headers
            ):
                continue
            condition_index = next(
                (i for i, header in enumerate(headers) if "条件" in clean_text(header)),
                None,
            )
            current_model_cell = ""
            for row in table[1:]:
                if model_index >= len(row):
                    continue
                raw_model_cell = row[model_index]
                if clean_text(raw_model_cell):
                    current_model_cell = raw_model_cell
                if not current_model_cell or "不适用" in current_model_cell:
                    continue
                display_name = clean_text(current_model_cell.splitlines()[0])
                prices = []
                for index, header in enumerate(headers):
                    kind = price_type_from_header(header)
                    if kind == "other" or index >= len(row):
                        continue
                    amount = amount_from_cell(row[index])
                    if amount is not None:
                        prices.append(
                            price_item(
                                kind, clean_text(header), amount, unit_code(header)
                            )
                        )
                if not prices:
                    continue
                conditions: dict[str, Any] = {}
                if condition_index is not None and condition_index < len(row):
                    condition = clean_text(row[condition_index])
                    if condition not in ("", "-"):
                        conditions["context_tier"] = condition
                stage = "preview" if "预览版" in display_name else "stable"
                conditions["release_stage"] = stage
                key = normalize_model(display_name)
                record = grouped.setdefault(
                    key,
                    make_record(
                        self.provider_id,
                        self.provider_name,
                        key,
                        display_name,
                        "中国区",
                        [],
                        self.source_url,
                        self.source_kind,
                        retrieved_at,
                        source_api=VOLCENGINE_DOC_API,
                        source_updated_at=document.updated_at,
                        delivery_mode="platform_hosted",
                        model_family=model_family(display_name),
                    ),
                )
                record["offers"].append(
                    {
                        "name": volc_offer_name(headings),
                        "conditions": conditions,
                        "prices": prices,
                    }
                )
        return list(grouped.values())

    def list_models(self, prefix: str = "") -> list[str]:
        models = {record["display_name"] for record in self._records()}
        if prefix:
            models = {model for model in models if model_matches(prefix, model)}
        return sorted(models, key=str.lower)

    def query(self, model: str) -> list[dict[str, Any]]:
        return [
            record
            for record in self._records()
            if model_matches(model, record["model_id"], exact=True)
            or model_matches(model, record["display_name"], exact=True)
        ]

    def search(self, model: str, *, exact: bool = False) -> list[dict[str, Any]]:
        return [
            record
            for record in self._records()
            if model_matches(model, record["model_id"], exact=exact)
            or model_matches(model, record["display_name"], exact=exact)
        ]
