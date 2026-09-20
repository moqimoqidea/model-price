"""Shared runtime primitives: timestamps, HTTP access, and the source contract."""

from __future__ import annotations

import gzip
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import SourceError
from .models import model_matches, normalize_model


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_json(path: Path, payload: Any, *, indent: int | None = None) -> None:
    """Write JSON through a temporary file, so a reader never sees a partial one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=indent,
                separators=None if indent else (",", ":"),
            )
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class HttpClient:
    """Minimal credential-free HTTP client for public pricing documents."""

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout
        self.user_agent = "model-price/2.0 (public-price-checker)"

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        merged = {"Accept": "*/*", "User-Agent": self.user_agent}
        merged.update(headers or {})
        request = urllib.request.Request(url, data=data, headers=merged, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                if body.startswith(b"\x1f\x8b"):
                    body = gzip.decompress(body)
                return body.decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SourceError(f"request failed: {exc}") from exc

    def get_text(self, url: str) -> str:
        return self.request(url)

    def post_form(self, url: str, fields: dict[str, str]) -> dict[str, Any]:
        text = self.request(
            url,
            method="POST",
            data=urllib.parse.urlencode(fields).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceError("source did not return JSON") from exc


class PriceSource(ABC):
    """One provider's official price catalogue.

    Adapters stay focused on parsing; caching and reporting are separate layers.
    """

    provider_id: str
    provider_name: str
    source_url: str
    source_kind: str
    catalog_url: str | None = None

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    @abstractmethod
    def query(self, model: str) -> list[dict[str, Any]]:
        """Return records for an exact model id or display name."""

    @abstractmethod
    def list_models(self, prefix: str = "") -> list[str]:
        """Return the model ids this source publishes."""

    def catalog_records(self) -> list[dict[str, Any]]:
        """Return every priced record this source publishes, for a whole-catalogue scan.

        Deliberately not abstract: an adapter that already parses its document in
        one pass should override this, but one that only knows how to answer a
        single model still scans correctly by walking its own catalogue. The
        fallback costs one ``query`` per model, so an adapter whose ``query`` makes
        its own HTTP request should override this rather than paying that.
        """
        records: dict[str, dict[str, Any]] = {}
        for model in self.list_models():
            for record in self.query(model):
                records.setdefault(normalize_model(record["model_id"]), record)
        return [records[key] for key in sorted(records)]

    def search(self, model: str, *, exact: bool = False) -> list[dict[str, Any]]:
        """Return records for a model, expanding to its family unless ``exact``."""
        if exact:
            return self.query(model)
        records: list[dict[str, Any]] = []
        for candidate in self.list_models():
            if model_matches(model, candidate):
                records.extend(self.query(candidate))
        return records
