"""Resolve one authoritative introduction for each model in a report."""

from __future__ import annotations

import re
from typing import Any, Iterable

from ..caching import CacheStore
from ..models import canonical_model, normalize_model
from .core import (
    NOT_FOUND,
    SOURCE_ERROR,
    DescriptionSource,
    unavailable_description,
)

# A model vendor is preferred to whichever cloud happens to sell it.  Provider
# sources remain fallbacks for platform-native families and third-party models
# whose first-party documentation has no durable per-model page.
SOURCE_PATTERNS = (
    ("openai", re.compile(r"(?:^|[-/])(?:gpt|chatgpt|codex|sora)(?:[-/]|$)|^o\d")),
    ("anthropic", re.compile(r"(?:^|[-/])claude(?:[-/]|$)")),
    ("google", re.compile(r"(?:^|[-/])(?:gemini|gemma|veo|imagen|lyria)(?:[-/]|$)")),
    ("xai", re.compile(r"(?:^|[-/])grok(?:[-/]|$)")),
    ("deepseek", re.compile(r"(?:^|[-/])deepseek(?:[-/]|$)")),
    ("kimi", re.compile(r"(?:^|[-/])(?:kimi|moonshot)(?:[-/]|$)")),
    ("zhipu", re.compile(r"(?:^|[-/])(?:glm|cogview|cogvideo|autoglm)(?:[-/]|$)")),
    ("minimax", re.compile(r"(?:^|[-/])(?:minimax|hailuo)(?:[-/]|$)")),
    ("xiaomi", re.compile(r"(?:^|[-/])mimo(?:[-/]|$)")),
    (
        "aliyun",
        re.compile(r"(?:^|[-/])(?:qwen|wanx|tongyi|paraformer|sambert)(?:[-/]|$)"),
    ),
    ("volcengine", re.compile(r"(?:^|[-/])(?:doubao|seedream|seedance)(?:[-/]|$)")),
    ("baidu", re.compile(r"(?:^|[-/])ernie(?:[-/]|$)")),
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


def inferred_sources(model_id: str, display_name: str = "") -> list[str]:
    value = normalize_model(f"{model_id} {display_name}")
    return [
        source_id for source_id, pattern in SOURCE_PATTERNS if pattern.search(value)
    ]


class DescriptionResolver:
    """Try first-party then hosting-platform sources without failing prices."""

    def __init__(self, sources: dict[str, DescriptionSource]) -> None:
        self.sources = sources

    @staticmethod
    def _group(targets: Iterable[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for target in targets:
            model_id = target.get("model_id", "")
            if model_id:
                groups.setdefault(canonical_model(model_id), []).append(target)
        return list(groups.values())

    def resolve(self, targets: list[dict[str, Any]]) -> dict[str, Any]:
        """Resolve one canonical model represented by one or more price records."""
        head = targets[0]
        model_id = head["model_id"]
        canonical_id = canonical_model(model_id)
        display_name = head.get("display_name") or model_id
        candidates: list[tuple[str, dict[str, Any]]] = []
        for target in targets:
            ids = inferred_sources(
                target["model_id"], target.get("display_name", "")
            )
            provider_id = target.get("provider_id")
            if provider_id:
                ids.append(provider_id)
            for source_id in ids:
                if source_id in self.sources and all(
                    existing[0] != source_id for existing in candidates
                ):
                    candidates.append((source_id, target))
        attempted = []
        had_error = False
        for source_id, target in candidates:
            source = self.sources[source_id]
            try:
                description = source.describe(
                    target["model_id"],
                    target.get("display_name", ""),
                    record=target.get("record"),
                )
            except Exception as exc:
                had_error = True
                attempted.append(
                    {
                        "name": source.source_name,
                        "url": source.source_url,
                        "status": SOURCE_ERROR,
                        "error": str(exc),
                    }
                )
                continue
            attempted.append(
                {
                    "name": source.source_name,
                    "url": source.source_url,
                    "status": "available" if description else NOT_FOUND,
                }
            )
            if description:
                description["observed_model_ids"] = list(
                    dict.fromkeys(target["model_id"] for target in targets)
                )
                description["model_id"] = canonical_id
                description["canonical_model_id"] = canonical_id
                return description
        unavailable = unavailable_description(
            canonical_id,
            display_name,
            status=SOURCE_ERROR if had_error else NOT_FOUND,
            attempted_sources=attempted,
        )
        unavailable["observed_model_ids"] = list(
            dict.fromkeys(target["model_id"] for target in targets)
        )
        return unavailable

    def resolve_many(self, targets: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.resolve(group) for group in self._group(targets)]


def query_targets(
    query: str, records: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build model-level targets from query results, retaining provider metadata."""
    targets = [
        {
            "model_id": record["model_id"],
            "display_name": record.get("display_name", record["model_id"]),
            "provider_id": (record.get("provider") or {}).get("id"),
            "record": record,
        }
        for record in records
    ]
    return targets or [{"model_id": query, "display_name": query}]
