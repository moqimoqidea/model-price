"""Shared runtime primitives: timestamps, HTTP access, and the source contract."""

from __future__ import annotations

import gzip
import http.client
import json
import math
import os
import random
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

from .errors import SourceError
from .models import model_matches, normalize_model


RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
SAFE_METHODS = frozenset({"GET", "HEAD"})
CHALLENGE_MARKERS = (
    b"cf-chl-",
    b"challenge-platform",
    b"just a moment",
    b"attention required",
)
TRANSIENT_NETWORK_ERRORS = (
    urllib.error.URLError,
    TimeoutError,
    ConnectionError,
    http.client.IncompleteRead,
    http.client.RemoteDisconnected,
    ssl.SSLError,
)


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
    """Credential-free HTTP with bounded retries and per-run request limits."""

    def __init__(
        self,
        timeout: float = 30,
        *,
        attempts: int = 3,
        backoff_base: float = 0.5,
        backoff_cap: float = 8,
        retry_after_cap: float = 30,
        max_requests_per_host: int | None = 20,
        opener: Callable[..., Any] | None = None,
        sleeper: Callable[[float], None] | None = None,
        uniform: Callable[[float, float], float] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if timeout <= 0 or attempts < 1:
            raise ValueError("HTTP timeout and attempts must be positive")
        if backoff_base < 0 or backoff_cap < 0 or retry_after_cap < 0:
            raise ValueError("HTTP retry delays cannot be negative")
        if max_requests_per_host is not None and max_requests_per_host < 1:
            raise ValueError("HTTP per-host request limit must be positive")
        self.timeout = timeout
        self.attempts = attempts
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.retry_after_cap = retry_after_cap
        self.max_requests_per_host = max_requests_per_host
        self.user_agent = "model-price/2.0 (public-price-checker)"
        self._opener = opener or urllib.request.urlopen
        self._sleep = sleeper or time.sleep
        self._uniform = uniform or random.uniform
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._document_cache: dict[str, str] = {}
        self._host_requests: dict[str, int] = {}

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        idempotent: bool | None = None,
    ) -> str:
        """Read one response, retrying only requests that are safe to repeat."""
        method = method.upper()
        may_retry = method in SAFE_METHODS if idempotent is None else idempotent
        attempts = self.attempts if may_retry else 1
        merged = {"Accept": "*/*", "User-Agent": self.user_agent}
        merged.update(headers or {})
        request = urllib.request.Request(url, data=data, headers=merged, method=method)
        challenge_retried = False
        for attempt in range(1, attempts + 1):
            try:
                return self._once(request)
            except urllib.error.HTTPError as exc:
                challenge = exc.code == 403 and self._is_challenge(exc)
                retryable = exc.code in RETRYABLE_HTTP_STATUSES
                if challenge and not challenge_retried:
                    retryable = True
                    challenge_retried = True
                if not retryable or attempt >= attempts:
                    raise SourceError(
                        self._failure_message(
                            url, exc, attempt, challenge=challenge
                        )
                    ) from exc
                delay = self._retry_delay(exc, attempt - 1)
                if delay > self.retry_after_cap:
                    raise SourceError(
                        self._retry_after_message(url, exc, attempt, delay)
                    ) from exc
                if delay:
                    self._sleep(delay)
            except TRANSIENT_NETWORK_ERRORS as exc:
                if attempt >= attempts:
                    raise SourceError(
                        self._failure_message(url, exc, attempt)
                    ) from exc
                delay = self._jitter(attempt - 1)
                if delay:
                    self._sleep(delay)
        raise AssertionError("HTTP request loop ended without a response")

    def _once(self, request: urllib.request.Request) -> str:
        self._consume_host_budget(request.full_url)
        with self._opener(request, timeout=self.timeout) as response:
            body = response.read()
            if body.startswith(b"\x1f\x8b"):
                body = gzip.decompress(body)
            return body.decode("utf-8", errors="replace")

    def _consume_host_budget(self, url: str) -> None:
        if self.max_requests_per_host is None:
            return
        host = urllib.parse.urlsplit(url).hostname or "unknown host"
        used = self._host_requests.get(host, 0)
        if used >= self.max_requests_per_host:
            raise SourceError(
                f"request budget exceeded for {host}: "
                f"{self.max_requests_per_host} attempts per run"
            )
        self._host_requests[host] = used + 1

    def _retry_delay(self, exc: urllib.error.HTTPError, attempt: int) -> float:
        if exc.code in {429, 503}:
            value = (exc.headers or {}).get("Retry-After")
            parsed = self._parse_retry_after(value)
            if parsed is not None:
                return parsed
        return self._jitter(attempt)

    def _parse_retry_after(self, value: Any) -> float | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            seconds = float(text)
            return max(0.0, seconds) if math.isfinite(seconds) else None
        except ValueError:
            pass
        try:
            target = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return max(0.0, (target - now).total_seconds())

    def _jitter(self, attempt: int) -> float:
        ceiling = min(self.backoff_cap, self.backoff_base * (2**attempt))
        return self._uniform(0.0, ceiling)

    @staticmethod
    def _is_challenge(exc: urllib.error.HTTPError) -> bool:
        if str((exc.headers or {}).get("cf-mitigated", "")).lower() == "challenge":
            return True
        try:
            body = exc.read(64 * 1024).lower()
        except (OSError, ValueError, http.client.HTTPException):
            return False
        return any(marker in body for marker in CHALLENGE_MARKERS)

    def _failure_message(
        self,
        url: str,
        exc: Exception,
        attempts: int,
        *,
        challenge: bool = False,
    ) -> str:
        host = urllib.parse.urlsplit(url).hostname or "unknown host"
        if isinstance(exc, urllib.error.HTTPError):
            category = (
                "request rejected"
                if 400 <= exc.code < 500
                else "server error"
            )
            reason = str(exc.reason or "").strip()
            detail = f"HTTP {exc.code}" + (f" {reason}" if reason else "")
            if challenge:
                detail += " (anti-bot challenge)"
            return f"{category} by {host} after {attempts} attempt(s): {detail}"
        detail = str(exc).strip() or type(exc).__name__
        reason = getattr(exc, "reason", None)
        timed_out = (
            isinstance(exc, TimeoutError)
            or isinstance(reason, TimeoutError)
            or "timed out" in detail.lower()
            or "timeout" in detail.lower()
        )
        if timed_out:
            return (
                f"request timed out for {host} after {attempts} attempt(s) "
                f"({self.timeout:g}s per attempt; last error: {detail})"
            )
        return (
            f"connection failed for {host} after {attempts} attempt(s): {detail}"
        )

    def _retry_after_message(
        self,
        url: str,
        exc: urllib.error.HTTPError,
        attempts: int,
        delay: float,
    ) -> str:
        host = urllib.parse.urlsplit(url).hostname or "unknown host"
        return (
            f"request deferred by {host} after {attempts} attempt(s): "
            f"HTTP {exc.code} Retry-After requested {delay:g}s, above the "
            f"{self.retry_after_cap:g}s wait limit"
        )

    def get_text(self, url: str) -> str:
        """Fetch a document once per process, including during forced refreshes."""
        if url not in self._document_cache:
            self._document_cache[url] = self.request(url)
        return self._document_cache[url]

    def post_form(
        self,
        url: str,
        fields: dict[str, str],
        *,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        text = self.request(
            url,
            method="POST",
            data=urllib.parse.urlencode(fields).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            idempotent=idempotent,
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
        self._document_cache: dict[str, str] = {}

    def document(self, url: str) -> str:
        """Read one source document once for this adapter instance."""
        if url not in self._document_cache:
            self._document_cache[url] = self.client.get_text(url)
        return self._document_cache[url]

    @abstractmethod
    def query(self, model: str) -> list[dict[str, Any]]:
        """Return records for an exact model id or display name."""

    @abstractmethod
    def list_models(self, prefix: str = "") -> list[str]:
        """Return the model ids this source publishes."""

    def catalog_records(self) -> list[dict[str, Any]]:
        """Return every priced record this source publishes, for a whole-catalogue scan.

        Deliberately not abstract so small test or third-party sources can still
        walk their own catalogue. Registered adapters override this with a
        one-pass implementation. The fallback costs one ``query`` per model;
        ``document`` prevents repeat downloads but not repeat parsing.
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
