"""A small file cache, kept separate from how each source is parsed."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import PriceSource, write_json
from .paths import CACHE_SCHEMA_VERSION, CACHE_TTL, DEFAULT_CACHE_DIR


class CacheStore:
    """Cache entries scoped by provider and operation."""

    def __init__(
        self,
        root: Path = DEFAULT_CACHE_DIR,
        ttl: Any = CACHE_TTL,
        clock: Any = None,
    ) -> None:
        self.root = root
        self.ttl = ttl
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _path(self, provider: str, operation: str, arguments: Any) -> Path:
        identity = json.dumps(
            [operation, arguments],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(identity.encode()).hexdigest()[:20]
        return self.root / provider / f"{operation}-{digest}.json"

    def read(
        self, provider: str, operation: str, arguments: Any
    ) -> tuple[Any, str] | None:
        """Return cached data and its fetch time, or ``None`` when unusable."""
        path = self._path(provider, operation, arguments)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != CACHE_SCHEMA_VERSION:
                return None
            fetched_at = datetime.fromisoformat(payload["fetched_at"])
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            if self.clock() - fetched_at > self.ttl:
                return None
            return payload["data"], payload["fetched_at"]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def write(self, provider: str, operation: str, arguments: Any, data: Any) -> str:
        fetched_at = self.clock().astimezone().isoformat(timespec="seconds")
        write_json(
            self._path(provider, operation, arguments),
            {
                "schema_version": CACHE_SCHEMA_VERSION,
                "provider": provider,
                "operation": operation,
                "arguments": arguments,
                "fetched_at": fetched_at,
                "data": data,
            },
        )
        return fetched_at


class CachedPriceSource(PriceSource):
    """Cache decorator; source adapters stay focused on parsing official data.

    A failed refresh is reported as ``refresh_failed`` and never silently
    replaced by stale data.
    """

    def __init__(
        self, source: PriceSource, cache: CacheStore, *, refresh: bool = False
    ) -> None:
        self.source = source
        self.cache = cache
        self.refresh = refresh
        self.provider_id = source.provider_id
        self.provider_name = source.provider_name
        self.source_url = source.source_url
        self.source_kind = source.source_kind
        self.catalog_url = source.catalog_url
        self.cache_status = "unused"
        self.cached_at: str | None = None

    def _cached(self, operation: str, arguments: Any, loader: Any) -> Any:
        if not self.refresh:
            cached = self.cache.read(self.provider_id, operation, arguments)
            if cached is not None:
                data, self.cached_at = cached
                self.cache_status = "hit"
                return data
        try:
            data = loader()
        except Exception:
            self.cache_status = "refresh_failed"
            raise
        self.cached_at = self.cache.write(self.provider_id, operation, arguments, data)
        self.cache_status = "refreshed" if self.refresh else "miss"
        return data

    def list_models(self, prefix: str = "") -> list[str]:
        return self._cached(
            "list", {"prefix": prefix}, lambda: self.source.list_models(prefix)
        )

    def catalog_records(self) -> list[dict[str, Any]]:
        """Cache a whole-catalogue scan as one entry, not one per model."""
        return self._cached("catalog", {}, self.source.catalog_records)

    def query(self, model: str) -> list[dict[str, Any]]:
        return self._cached("query", {"model": model}, lambda: self.source.query(model))

    def search(self, model: str, *, exact: bool = False) -> list[dict[str, Any]]:
        return self._cached(
            "search",
            {"model": model, "exact": exact},
            lambda: self.source.search(model, exact=exact),
        )
