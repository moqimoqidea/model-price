"""Resolve a model's introduction only from the channel that lists it."""

from __future__ import annotations

from typing import Any, Iterable

from ..caching import CacheStore
from ..models import normalize_model
from .core import (
    NOT_FOUND,
    SOURCE_ERROR,
    DescriptionSource,
    unavailable_description,
)


class CachedDescriptionSource(DescriptionSource):
    """Cache model introductions separately from price lookups."""

    def __init__(
        self, source: DescriptionSource, cache: CacheStore, *, refresh: bool = False
    ) -> None:
        self.source = source
        self.cache = cache
        self.refresh = refresh
        self.source_id = source.source_id
        self.source_name = source.source_name
        self.source_url = source.source_url
        self.source_kind = source.source_kind

    def describe(
        self,
        model_id: str,
        display_name: str = "",
        *,
        record: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        arguments = {"model_id": model_id, "display_name": display_name}
        if not self.refresh:
            cached = self.cache.read(self.source_id, "description", arguments)
            if cached is not None:
                return cached[0]
        result = self.source.describe(model_id, display_name, record=record)
        self.cache.write(self.source_id, "description", arguments, result)
        return result


class DescriptionResolver:
    """Use each hosting channel's own source without failing price results."""

    def __init__(self, sources: dict[str, DescriptionSource]) -> None:
        self.sources = sources

    @staticmethod
    def _group(targets: Iterable[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for target in targets:
            model_id = target.get("model_id", "")
            if model_id:
                key = (target.get("provider_id") or "", normalize_model(model_id))
                groups.setdefault(key, []).append(target)
        return list(groups.values())

    def resolve(self, targets: list[dict[str, Any]]) -> dict[str, Any]:
        """Resolve one literal model on one provider, including honest failures."""
        if len(self._group(targets)) != 1:
            raise ValueError("description targets must name one model on one provider")
        head = targets[0]
        model_id = head["model_id"]
        display_name = head.get("display_name") or model_id
        provider_id = head.get("provider_id") or ""
        source = self.sources.get(provider_id)
        attempted: list[dict[str, str]] = []
        description: dict[str, Any] | None = None
        status = NOT_FOUND
        note = ""
        error = ""
        if head.get("description") is not None:
            description = dict(head["description"])
        elif source is not None:
            try:
                description = source.describe(
                    model_id, display_name, record=head.get("record")
                )
            except Exception as exc:
                status = SOURCE_ERROR
                error = str(exc)
                note = (
                    f"该渠道的模型介绍来源暂时无法读取；价格结果不受影响。原因：{error}"
                )
            attempt = {
                "name": source.source_name,
                "url": source.source_url,
                "status": "available" if description else status,
            }
            if status == SOURCE_ERROR:
                attempt["error"] = error
            attempted.append(attempt)
        elif provider_id:
            note = "该渠道尚无可读取的官方模型介绍源；价格结果不受影响。"
        result = (
            dict(description)
            if description
            else unavailable_description(
                model_id,
                display_name,
                status=status,
                note=note,
                attempted_sources=attempted,
            )
        )
        result["model_id"] = model_id
        result["provider"] = {
            "id": provider_id,
            "name": head.get("provider_name")
            or (source.source_name if source is not None else provider_id),
        }
        result["observed_model_ids"] = list(
            dict.fromkeys(target["model_id"] for target in targets)
        )
        if not description and (url := head.get("reference_url")):
            result["reference_url"] = url
        return result

    def resolve_many(self, targets: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.resolve(group) for group in self._group(targets)]


def query_targets(
    query: str,
    records: Iterable[dict[str, Any]],
    *,
    providers: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Build model-level targets from query results, retaining provider metadata."""
    targets = [
        {
            "model_id": record["model_id"],
            "display_name": record.get("display_name", record["model_id"]),
            "provider_id": (record.get("provider") or {}).get("id"),
            "provider_name": (record.get("provider") or {}).get("name"),
            "reference_url": (record.get("source") or {}).get("url"),
            "record": record,
        }
        for record in records
    ]
    if targets:
        return targets
    return [
        {
            "model_id": query,
            "display_name": query,
            "provider_id": check["provider"]["id"],
            "provider_name": check["provider"]["name"],
            "reference_url": (check.get("source") or {}).get("url"),
        }
        for check in providers
    ] or [{"model_id": query, "display_name": query}]
