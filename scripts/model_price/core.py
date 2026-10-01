"""Read public sources politely and keep transport failures out of parsed records."""

from __future__ import annotations

import gzip
import http.client
import http.cookiejar
import io
import json
import math
import os
import random
import re
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .errors import SourceError
from .models import model_matches, normalize_model


RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
SAFE_METHODS = frozenset({"GET", "HEAD"})
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
)
BROWSER_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    "cache-control": "no-cache",
    "dnt": "1",
    "pragma": "no-cache",
    "sec-ch-ua": '"Chromium";v="154", "Google Chrome";v="154", "Not A(Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-storage-access": "active",
    "user-agent": DEFAULT_USER_AGENT,
}
AUTHENTICATION_HOSTS = frozenset({"accounts.google.com", "login.microsoftonline.com"})
CHALLENGE_MARKERS = (
    b"/cdn-cgi/challenge-platform/",
    b"window._cf_chl_opt",
    b'id="challenge-form"',
)
RequestKey = tuple[str, str, Optional[bytes], tuple[tuple[str, str], ...]]
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


def exponential_backoff(
    attempt: int,
    *,
    base: float = 2,
    cap: float = 30,
    uniform: Callable[[float, float], float] = random.uniform,
) -> float:
    """Keep a growing retry floor with jitter, shared by HTTP and Git reads."""
    ceiling = min(cap, base * (2**attempt))
    return uniform(ceiling / 2, ceiling)


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


class AuthenticationRedirect(SourceError):
    """A public read entered a sign-in flow that this credential-free client stops."""


class PacedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirects consume the destination host's budget and wait their turn too."""

    def __init__(self, before_request: Callable[[str], None]) -> None:
        self.before_request = before_request

    def redirect_request(
        self,
        request: urllib.request.Request,
        response: Any,
        code: int,
        message: str,
        headers: Any,
        url: str,
    ) -> urllib.request.Request | None:
        redirected = super().redirect_request(
            request, response, code, message, headers, url
        )
        if redirected is not None:
            try:
                parts = urllib.parse.urlsplit(redirected.full_url)
                if parts.hostname in AUTHENTICATION_HOSTS or re.match(
                    r"^/(?:oauth2authorize|oauth2callback|signin|login|"
                    r"o/oauth2|oauth2/authorize)(?:/|$)",
                    parts.path,
                ):
                    target = urllib.parse.urlunsplit(parts._replace(query="", fragment=""))
                    raise AuthenticationRedirect(
                        f"anonymous source redirected to authentication; "
                        f"source: {request.full_url}; target: {target}"
                    )
                referer = redirected.get_header("Referer", "")
                origin = urllib.parse.urlsplit(referer)
                same_origin = (parts.scheme, parts.netloc) == (origin.scheme, origin.netloc)
                redirected.add_header(
                    "Sec-Fetch-Site", "same-origin" if same_origin else "cross-site"
                )
                self.before_request(redirected.full_url)
            except Exception:
                if response is not None:
                    response.close()
                raise
        return redirected


class HttpClient:
    """Credential-free HTTP with host pacing, bounded retries, and run-local reuse."""

    def __init__(
        self,
        timeout: float = 30,
        *,
        attempts: int = 3,
        backoff_base: float = 2,
        backoff_cap: float = 30,
        retry_after_cap: float = 30,
        min_request_interval: float = 1,
        max_requests_per_host: int | None = 20,
        opener: Callable[..., Any] | None = None,
        sleeper: Callable[[float], None] | None = None,
        uniform: Callable[[float, float], float] | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if timeout <= 0 or attempts < 1:
            raise ValueError("HTTP timeout and attempts must be positive")
        if min(backoff_base, backoff_cap, retry_after_cap, min_request_interval) < 0:
            raise ValueError("HTTP retry delays cannot be negative")
        if max_requests_per_host is not None and max_requests_per_host < 1:
            raise ValueError("HTTP per-host request limit must be positive")
        self.timeout = timeout
        self.attempts = attempts
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.retry_after_cap = retry_after_cap
        self.min_request_interval = min_request_interval
        self.max_requests_per_host = max_requests_per_host
        self._opener = opener or urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            PacedRedirectHandler(self._before_request),
        ).open
        self._sleep = sleeper or time.sleep
        self._uniform = uniform or random.uniform
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._responses: dict[RequestKey, str] = {}
        self._failures: dict[RequestKey, str] = {}
        self._host_requests: dict[str, int] = {}
        self._host_ready_at: dict[str, float] = {}
        self._paused_hosts: dict[str, str] = {}

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        idempotent: bool | None = None,
    ) -> str:
        """Reuse safe reads, including their failures, only within this fresh run."""
        method = method.upper()
        may_retry = method in SAFE_METHODS if idempotent is None else idempotent
        attempts = self.attempts if may_retry else 1
        parts = urllib.parse.urlsplit(url)
        merged = {
            **BROWSER_HEADERS,
            "accept-encoding": "gzip, deflate",
            "referer": urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/", "", "")),
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        overrides = {name.lower(): value for name, value in (headers or {}).items()}
        merged.update(overrides)
        key: RequestKey = method, url, data, tuple(sorted(merged.items()))
        reusable = method in SAFE_METHODS or idempotent is True
        if reusable and key in self._responses:
            return self._responses[key]
        if reusable and key in self._failures:
            raise SourceError(self._failures[key])
        request = urllib.request.Request(url, data=data, headers=merged, method=method)
        try:
            text = self._request(request, attempts)
        except SourceError as exc:
            if reusable:
                self._failures[key] = str(exc)
            raise
        if reusable:
            self._responses[key] = text
        return text

    def _request(self, request: urllib.request.Request, attempts: int) -> str:
        """Retry transport failures without repeating unsafe operations or challenges."""
        url = request.full_url
        challenge_retried = False
        session_retried = False
        for attempt in range(1, attempts + 1):
            try:
                return self._once(request)
            except urllib.error.HTTPError as exc:
                error_url = exc.geturl() or url
                try:
                    challenge = (
                        exc.code in {200, 403, 429, 503} and self._is_challenge(exc)
                    )
                    retryable = (
                        not challenge_retried
                        if challenge
                        else exc.code in RETRYABLE_HTTP_STATUSES
                    )
                    retry_after = self._parse_retry_after(
                        (exc.headers or {}).get("Retry-After")
                    )
                    if retry_after is not None and retry_after > self.retry_after_cap:
                        message = self._retry_after_message(
                            error_url, exc, attempt, retry_after
                        )
                        self._pause_host(error_url, message)
                        raise SourceError(message) from exc
                    if not retryable or attempt >= attempts:
                        message = self._failure_message(
                            error_url, exc, attempt, challenge=challenge
                        )
                        if challenge or exc.code == 429:
                            self._pause_host(error_url, message)
                        raise SourceError(message) from exc
                    challenge_retried = challenge_retried or challenge
                    self._defer_host(
                        error_url, max(retry_after or 0, self._jitter(attempt - 1))
                    )
                finally:
                    exc.close()
            except AuthenticationRedirect:
                # DevSite sets anonymous sign-in-status cookies before its OAuth
                # redirect. Re-read the public page with those cookies and the
                # unchanged browser identity, without visiting a login endpoint.
                if session_retried or attempt >= attempts:
                    raise
                session_retried = True
                self._defer_host(url, self._jitter(attempt - 1))
            except TRANSIENT_NETWORK_ERRORS as exc:
                if attempt >= attempts:
                    raise SourceError(
                        self._failure_message(url, exc, attempt)
                    ) from exc
                self._defer_host(url, self._jitter(attempt - 1))
        raise AssertionError("HTTP request loop ended without a response")

    def _once(self, request: urllib.request.Request) -> str:
        self._before_request(request.full_url)
        with self._opener(request, timeout=self.timeout) as response:
            body = response.read()
            headers = getattr(response, "headers", None) or {}
            encoding = str(headers.get("Content-Encoding", "")).lower().strip()
            try:
                if encoding == "gzip" or body.startswith(b"\x1f\x8b"):
                    body = gzip.decompress(body)
                elif encoding == "deflate":
                    try:
                        body = zlib.decompress(body)
                    except zlib.error:
                        body = zlib.decompress(body, -zlib.MAX_WBITS)
                elif encoding not in ("", "identity"):
                    raise SourceError(
                        f"unsupported Content-Encoding {encoding}; source: {request.full_url}"
                    )
            except (EOFError, OSError, zlib.error) as exc:
                raise http.client.IncompleteRead(body) from exc
            if self._challenge_page(headers, body):
                # Some intermediaries serve their verification screen with 200.
                # It must neither reach a parser nor enter the response cache.
                raise urllib.error.HTTPError(
                    response.geturl(),
                    200,
                    "verification required",
                    headers,
                    io.BytesIO(body[:64 * 1024]),
                )
            return body.decode("utf-8", errors="replace")

    def _before_request(self, url: str) -> None:
        host = urllib.parse.urlsplit(url).hostname or "unknown host"
        if host in self._paused_hosts:
            raise SourceError(
                f"requests paused for {host} for this run; {self._paused_hosts[host]}"
            )
        used = self._host_requests.get(host, 0)
        if self.max_requests_per_host is not None and used >= self.max_requests_per_host:
            raise SourceError(
                f"request budget exceeded for {host}: "
                f"{self.max_requests_per_host} attempts per run"
            )
        self._host_requests[host] = used + 1
        delay = self._host_ready_at.get(host, 0) - self._monotonic()
        if delay > 0:
            self._sleep(delay)
        self._host_ready_at[host] = self._monotonic() + self.min_request_interval

    def _defer_host(self, url: str, delay: float) -> None:
        host = urllib.parse.urlsplit(url).hostname or "unknown host"
        self._host_ready_at[host] = max(
            self._host_ready_at.get(host, 0), self._monotonic() + delay
        )

    def _pause_host(self, url: str, reason: str) -> None:
        self._paused_hosts[urllib.parse.urlsplit(url).hostname or "unknown host"] = reason

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
        return exponential_backoff(
            attempt, base=self.backoff_base, cap=self.backoff_cap, uniform=self._uniform
        )

    @staticmethod
    def _is_challenge(exc: urllib.error.HTTPError) -> bool:
        if str((exc.headers or {}).get("cf-mitigated", "")).lower() == "challenge":
            return True
        try:
            body = exc.read(64 * 1024)
        except (OSError, ValueError, http.client.HTTPException):
            return False
        return HttpClient._challenge_page(exc.headers or {}, body)

    @staticmethod
    def _challenge_page(headers: Any, body: bytes) -> bool:
        if str(headers.get("cf-mitigated", "")).lower() == "challenge":
            return True
        prefix = body[:64 * 1024].lower().lstrip()
        if not prefix.startswith((b"<!doctype html", b"<html", b"<head", b"<title")):
            return False
        return any(marker in prefix for marker in CHALLENGE_MARKERS) or bool(
            re.search(br"<title>\s*(?:just a moment|attention required)[^<]*</title>", prefix)
        )

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
                if challenge or 400 <= exc.code < 500
                else "server error"
            )
            reason = str(exc.reason or "").strip()
            detail = f"HTTP {exc.code}" + (f" {reason}" if reason else "")
            if challenge:
                detail += " (anti-bot challenge)"
            return f"{category} by {host} after {attempts} attempt(s): {detail}; source: {url}"
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
                f"({self.timeout:g}s per attempt; last error: {detail}); source: {url}"
            )
        return (
            f"connection failed for {host} after {attempts} attempt(s): {detail}; source: {url}"
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
            f"{self.retry_after_cap:g}s wait limit; source: {url}"
        )

    def get_text(self, url: str) -> str:
        """Fetch a document once per process, including during forced refreshes."""
        return self.request(url)

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
