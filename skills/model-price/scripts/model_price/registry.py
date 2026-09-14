"""Wire provider adapters together and query them across providers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .caching import CacheStore, CachedPriceSource
from .core import HttpClient, PriceSource, now_iso
from .models import normalize_model
from .paths import DEFAULT_CACHE_DIR
from .providers import ALL_PROVIDERS, DOMESTIC_PROVIDERS, OVERSEAS_PROVIDERS

DOMESTIC_PROVIDER_IDS = tuple(adapter.provider_id for adapter in DOMESTIC_PROVIDERS)
OVERSEAS_PROVIDER_IDS = tuple(adapter.provider_id for adapter in OVERSEAS_PROVIDERS)

# Name fragments that identify an overseas vendor when no provider is requested.
OVERSEAS_NAME_PATTERNS = (
    ("openai", r"(?:^|-)(?:openai|gpt|chatgpt|codex|sora)(?:-|$)"),
    ("anthropic", r"(?:^|-)(?:anthropic|claude)(?:-|$)"),
    ("google", r"(?:^|-)(?:google|gemini|gemma|veo|lyria|imagen)(?:-|$)"),
    ("xai", r"(?:^|-)(?:xai|grok)(?:-|$)"),
)


def build_adapters(
    client: HttpClient,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    refresh: bool = False,
) -> dict[str, PriceSource]:
    """Instantiate every provider, each wrapped in its cache decorator."""
    cache = CacheStore(cache_dir)
    return {
        provider.provider_id: CachedPriceSource(provider(client), cache, refresh=refresh)
        for provider in ALL_PROVIDERS
    }


def source_status(
    adapter: PriceSource, status: str, retrieved_at: str, error: str | None = None
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "provider": {"id": adapter.provider_id, "name": adapter.provider_name},
        "status": status,
        "source": {
            "url": adapter.source_url,
            "kind": adapter.source_kind,
            "retrieved_at": retrieved_at,
        },
    }
    if error:
        item["error"] = error
    cache_status = getattr(adapter, "cache_status", None)
    if cache_status and cache_status != "unused":
        item["cache"] = {
            "status": cache_status,
            "fetched_at": getattr(adapter, "cached_at", None),
        }
    return item


def catalog_payload(adapter: PriceSource, prefix: str = "") -> dict[str, Any]:
    """Describe one provider's published model list."""
    models = adapter.list_models(prefix)
    payload: dict[str, Any] = {
        "provider": {"id": adapter.provider_id, "name": adapter.provider_name},
        "prefix": prefix,
        "count": len(models),
        "models": models,
        "source": {
            "url": adapter.catalog_url or adapter.source_url,
            "kind": adapter.source_kind,
            "retrieved_at": now_iso(),
        },
    }
    cache_status = getattr(adapter, "cache_status", None)
    if cache_status and cache_status != "unused":
        payload["cache"] = {
            "status": cache_status,
            "fetched_at": getattr(adapter, "cached_at", None),
        }
    return payload


def inferred_overseas_providers(model: str) -> tuple[str, ...]:
    """Route a model name to the overseas sources that could carry it."""
    key = normalize_model(model)
    # "o3", "o4-mini", ... are OpenAI reasoning models with no family keyword.
    openai_reasoning = bool(re.match(r"^o\d(?:-|$)", key))
    return tuple(
        provider_id
        for provider_id, pattern in OVERSEAS_NAME_PATTERNS
        if re.search(pattern, key) or (provider_id == "openai" and openai_reasoning)
    )


def select_compare_providers(
    adapters: dict[str, PriceSource],
    model: str,
    *,
    requested: list[str] | None = None,
    include_overseas: bool = False,
) -> list[PriceSource]:
    if requested:
        provider_ids = requested
    else:
        overseas = (
            OVERSEAS_PROVIDER_IDS
            if include_overseas
            else inferred_overseas_providers(model)
        )
        provider_ids = [*DOMESTIC_PROVIDER_IDS, *overseas]
    return [adapters[provider_id] for provider_id in dict.fromkeys(provider_ids)]


def query_adapters(
    adapters: Iterable[PriceSource], model: str, *, exact: bool = False
) -> dict[str, Any]:
    """Query each adapter, keeping the others usable when one source changes."""
    started = now_iso()
    records = []
    checks = []
    for adapter in adapters:
        checked_at = now_iso()
        try:
            found = adapter.search(model, exact=exact)
            records.extend(found)
            checks.append(
                source_status(
                    adapter, "available" if found else "not_found", checked_at
                )
            )
        except Exception as exc:
            checks.append(source_status(adapter, "source_error", checked_at, str(exc)))
    return {
        "query": model,
        "match_mode": "exact" if exact else "model_family",
        "retrieved_at": started,
        "results": records,
        "source_checks": checks,
    }
