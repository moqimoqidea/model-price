"""Exercise network pacing, retries, and failure isolation without live requests."""

from __future__ import annotations

import gzip
import http.client
import io
import sys
import unittest
import urllib.error
import urllib.request
import urllib.response
import zlib
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.core import (
    AuthenticationRedirect,
    BROWSER_HEADERS,
    DEFAULT_USER_AGENT,
    HttpClient,
    PacedRedirectHandler,
)
from model_price.errors import SourceError


class Response:
    def __init__(self, body: bytes, *, headers=None, url="https://example.test/a") -> None:
        self.body = body
        self.headers = headers or {}
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.body

    def geturl(self) -> str:
        return self.url


class Timer:
    """Advance a monotonic clock when a test sleeps, without waiting in real time."""

    def __init__(self) -> None:
        self.current = 0.0
        self.delays = []

    def monotonic(self) -> float:
        return self.current

    def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        self.current += delay


class SequenceOpener:
    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, *, timeout):
        self.requests.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def http_error(
    code: int,
    reason: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> urllib.error.HTTPError:
    response_headers = Message()
    for name, value in (headers or {}).items():
        response_headers[name] = value
    return urllib.error.HTTPError(
        "https://example.test/a",
        code,
        reason,
        response_headers,
        io.BytesIO(body),
    )


class HttpClientTests(unittest.TestCase):
    def setUp(self):
        self.timer = Timer()

    def client(self, opener, **options) -> HttpClient:
        settings = {
            "sleeper": self.timer.sleep,
            "monotonic": self.timer.monotonic,
            "uniform": lambda low, high: high,
            **options,
        }
        return HttpClient(opener=opener, **settings)

    def test_a_document_is_fetched_only_once_per_process(self):
        opener = SequenceOpener(Response(b"price table"))
        client = self.client(opener)

        self.assertEqual(client.get_text("https://example.test/a"), "price table")
        self.assertEqual(client.get_text("https://example.test/a"), "price table")
        self.assertEqual(len(opener.requests), 1)

    def test_safe_request_retries_transient_connections_with_exponential_backoff(self):
        opener = SequenceOpener(
            TimeoutError(),
            urllib.error.URLError("connection reset"),
            Response(b"ok"),
        )
        client = self.client(opener)

        self.assertEqual(client.get_text("https://example.test/a"), "ok")
        self.assertEqual(self.timer.delays, [2.0, 4.0])
        self.assertEqual(len(opener.requests), 3)

    def test_incomplete_read_is_retried_as_a_transport_failure(self):
        opener = SequenceOpener(
            http.client.IncompleteRead(b"partial", 10),
            Response(b"complete"),
        )
        client = self.client(opener)
        self.assertEqual(client.get_text("https://example.test/a"), "complete")
        self.assertEqual(len(opener.requests), 2)

    def test_post_is_not_retried_unless_caller_marks_it_idempotent(self):
        unsafe = SequenceOpener(TimeoutError(), Response(b'{}'))
        with self.assertRaises(SourceError):
            self.client(unsafe).post_form(
                "https://example.test/api", {"query": "a"}
            )
        self.assertEqual(len(unsafe.requests), 1)

        safe = SequenceOpener(TimeoutError(), Response(b'{"ok": true}'))
        result = self.client(safe).post_form(
            "https://example.test/api", {"query": "a"}, idempotent=True
        )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(safe.requests), 2)

    def test_permanent_client_error_is_not_retried(self):
        opener = SequenceOpener(
            http_error(404, "Not Found"),
            Response(b"should not be read"),
        )
        with self.assertRaisesRegex(SourceError, r"request rejected.*HTTP 404"):
            self.client(opener).get_text("https://example.test/missing")
        self.assertEqual(len(opener.requests), 1)

    def test_retryable_server_error_reports_exhausted_attempts(self):
        opener = SequenceOpener(
            http_error(500, "Internal Server Error"),
            http_error(500, "Internal Server Error"),
            http_error(500, "Internal Server Error"),
        )
        with self.assertRaisesRegex(
            SourceError, r"server error.*3 attempt\(s\).*HTTP 500"
        ):
            self.client(opener).get_text("https://example.test/a")
        self.assertEqual(len(opener.requests), 3)

    def test_retry_after_accepts_http_dates_and_rejects_long_waits(self):
        now = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
        retry_at = (now + timedelta(seconds=10)).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
        delayed = SequenceOpener(
            http_error(503, "Unavailable", headers={"Retry-After": retry_at}),
            Response(b"ok"),
        )
        client = self.client(delayed, clock=lambda: now)
        self.assertEqual(client.get_text("https://example.test/a"), "ok")
        self.assertEqual(self.timer.delays, [10.0])

        deferred = SequenceOpener(
            http_error(429, "Too Many Requests", headers={"Retry-After": "60"}),
            Response(b"should not be read"),
        )
        with self.assertRaisesRegex(SourceError, r"Retry-After requested 60s"):
            self.client(deferred).get_text("https://example.test/b")
        self.assertEqual(len(deferred.requests), 1)

    def test_cloudflare_challenge_is_retried_at_most_once(self):
        opener = SequenceOpener(
            http_error(403, "Forbidden", headers={"cf-mitigated": "challenge"}),
            http_error(403, "Forbidden", body=b"<title>Just a moment</title>"),
            Response(b"should not be read"),
        )
        with self.assertRaisesRegex(SourceError, r"anti-bot challenge"):
            self.client(opener).get_text("https://example.test/a")
        self.assertEqual(len(opener.requests), 2)

    def test_bare_timeout_always_has_a_nonempty_diagnostic(self):
        opener = SequenceOpener(TimeoutError())
        with self.assertRaisesRegex(
            SourceError, r"timed out.*1 attempt\(s\).*TimeoutError"
        ):
            self.client(opener, attempts=1).get_text(
                "https://example.test/a"
            )

    def test_per_host_budget_stops_a_runaway_request_loop(self):
        opener = SequenceOpener(Response(b"a"), Response(b"b"), Response(b"c"))
        client = self.client(
            attempts=1,
            max_requests_per_host=2,
            opener=opener,
        )
        client.get_text("https://example.test/a")
        client.get_text("https://example.test/b")
        with self.assertRaisesRegex(SourceError, r"request budget exceeded"):
            client.get_text("https://example.test/c")
        self.assertEqual(len(opener.requests), 2)

    def test_invalid_json_is_not_retried(self):
        opener = SequenceOpener(Response(b"not json"), Response(b'{}'))
        with self.assertRaisesRegex(SourceError, r"did not return JSON"):
            self.client(opener).post_form(
                "https://example.test/api", {"query": "a"}, idempotent=True
            )
        self.assertEqual(len(opener.requests), 1)

    def test_request_headers_are_browser_compatible_and_overrides_are_case_insensitive(self):
        opener = SequenceOpener(Response(b"a"), Response(b"b"))
        client = self.client(opener)
        client.get_text("https://example.test/a")
        client.request(
            "https://example.test/b",
            headers={"USER-AGENT": "test-agent", "Accept": "application/json"},
        )
        defaults, overridden = [
            {name.lower(): value for name, value in request.header_items()}
            for request, timeout in opener.requests
        ]
        self.assertEqual({name: defaults[name] for name in BROWSER_HEADERS}, BROWSER_HEADERS)
        self.assertEqual(defaults["referer"], "https://example.test/")
        self.assertEqual(defaults["sec-fetch-dest"], "empty")
        self.assertEqual(defaults["sec-fetch-mode"], "cors")
        self.assertEqual(defaults["sec-fetch-site"], "same-origin")
        self.assertEqual(defaults["accept-encoding"], "gzip, deflate")
        self.assertNotIn("cookie", defaults)
        self.assertNotIn("authorization", defaults)
        self.assertEqual(overridden["user-agent"], "test-agent")
        self.assertEqual(overridden["accept"], "application/json")

    def test_successful_requests_are_spaced_per_host_and_cache_hits_do_not_sleep(self):
        opener = SequenceOpener(Response(b"a"), Response(b"b"), Response(b"c"))
        client = self.client(opener)
        client.get_text("https://example.test/a")
        client.get_text("https://other.test/a")
        client.get_text("https://example.test/b")
        client.get_text("https://example.test/b")
        self.assertEqual(self.timer.delays, [1.0])
        self.assertEqual(len(opener.requests), 3)

    def test_equal_jitter_keeps_an_exponential_floor(self):
        opener = SequenceOpener(TimeoutError(), TimeoutError(), Response(b"ok"))
        self.client(opener, uniform=lambda low, high: low).get_text("https://example.test/a")
        self.assertEqual(self.timer.delays, [1.0, 2.0])

    def test_retry_after_cannot_shorten_backoff_even_on_other_retryable_statuses(self):
        opener = SequenceOpener(
            http_error(429, "Limited", headers={"Retry-After": "0"}),
            http_error(502, "Unavailable", headers={"Retry-After": "6"}),
            Response(b"ok"),
        )
        self.client(opener).get_text("https://example.test/a")
        self.assertEqual(self.timer.delays, [2.0, 6.0])

    def test_a_long_retry_after_pauses_the_host_but_other_hosts_can_continue(self):
        opener = SequenceOpener(
            http_error(429, "Limited", headers={"Retry-After": "120"}), Response(b"ok"),
        )
        client = self.client(opener)
        with self.assertRaisesRegex(SourceError, "Retry-After requested 120s"):
            client.get_text("https://example.test/a")
        with self.assertRaisesRegex(SourceError, "requests paused"):
            client.get_text("https://example.test/b")
        self.assertEqual(client.get_text("https://other.test/a"), "ok")
        self.assertEqual(len(opener.requests), 2)

    def test_exhausted_throttling_pauses_the_host_for_the_run(self):
        opener = SequenceOpener(*(http_error(429, "Limited") for _ in range(3)))
        client = self.client(opener)
        with self.assertRaisesRegex(SourceError, r"3 attempt\(s\).*HTTP 429"):
            client.get_text("https://example.test/a")
        with self.assertRaisesRegex(SourceError, "requests paused"):
            client.get_text("https://example.test/b")
        self.assertEqual(len(opener.requests), 3)
        self.assertEqual(self.timer.delays, [2.0, 4.0])

    def test_failed_reads_are_reused_without_hiding_the_error_and_a_new_run_retries(self):
        opener = SequenceOpener(http_error(403, "Forbidden"), Response(b"ok"))
        client = self.client(opener)
        for _ in range(2):
            with self.assertRaisesRegex(SourceError, "HTTP 403.*source: https://example.test/a"):
                client.get_text("https://example.test/a")
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(self.client(opener).get_text("https://example.test/a"), "ok")

    def test_a_path_permission_error_does_not_close_other_documents_on_the_host(self):
        opener = SequenceOpener(http_error(403, "Forbidden"), Response(b"ok"))
        client = self.client(opener)
        with self.assertRaises(SourceError):
            client.get_text("https://example.test/private")
        self.assertEqual(client.get_text("https://example.test/public"), "ok")
        self.assertEqual(self.timer.delays, [1.0])

    def test_idempotent_post_reuse_distinguishes_body_headers_and_method(self):
        opener = SequenceOpener(*(Response(body) for body in (b"a", b"b", b"c", b"d")))
        client = self.client(opener)
        for _ in range(2):
            result = client.request(
                "https://example.test/api", method="POST", data=b"one", idempotent=True
            )
            self.assertEqual(result, "a")
        result = client.request(
            "https://example.test/api", method="POST", data=b"two", idempotent=True
        )
        self.assertEqual(result, "b")
        result = client.request(
            "https://example.test/api", method="POST", data=b"one",
            headers={"Accept": "text/plain"}, idempotent=True,
        )
        self.assertEqual(result, "c")
        self.assertEqual(client.get_text("https://example.test/api"), "d")
        self.assertEqual(len(opener.requests), 4)

    def test_unsafe_posts_are_never_reused(self):
        opener = SequenceOpener(Response(b"one"), Response(b"two"))
        client = self.client(opener)
        self.assertEqual(client.request("https://example.test/api", method="POST"), "one")
        self.assertEqual(client.request("https://example.test/api", method="POST"), "two")

    def test_an_http_200_challenge_is_retried_but_never_cached_as_a_document(self):
        opener = SequenceOpener(
            Response(b"<html><title>Just a moment...</title></html>"), Response(b"prices")
        )
        client = self.client(opener)
        self.assertEqual(client.get_text("https://example.test/a"), "prices")
        self.assertEqual(client.get_text("https://example.test/a"), "prices")
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(self.timer.delays, [2.0])

    def test_repeated_http_200_challenges_pause_the_host(self):
        opener = SequenceOpener(*(
            Response(b"<html><title>Just a moment</title></html>") for _ in range(2)
        ))
        client = self.client(opener)
        with self.assertRaisesRegex(SourceError, "HTTP 200.*anti-bot challenge"):
            client.get_text("https://example.test/a")
        with self.assertRaisesRegex(SourceError, "requests paused"):
            client.get_text("https://example.test/b")
        self.assertEqual(len(opener.requests), 2)

    def test_an_article_mentioning_challenges_is_not_a_challenge(self):
        body = (
            b'<html><title>Model news</title><p>Just a moment: '
            b'attention required for cf-chl- changes.</p></html>'
        )
        opener = SequenceOpener(Response(body))
        self.assertEqual(self.client(opener).get_text("https://example.test/a"), body.decode())

    def test_supported_compression_is_decoded_and_truncated_gzip_is_retried(self):
        for encoding, encode in (("gzip", gzip.compress), ("deflate", zlib.compress)):
            with self.subTest(encoding=encoding):
                opener = SequenceOpener(Response(
                    encode(b"prices"), headers={"Content-Encoding": encoding}
                ))
                self.assertEqual(self.client(opener).get_text("https://example.test/a"), "prices")
        opener = SequenceOpener(Response(gzip.compress(b"prices")[:-3]), Response(b"prices"))
        self.assertEqual(self.client(opener).get_text("https://example.test/a"), "prices")
        self.assertEqual(len(opener.requests), 2)

    def test_redirects_share_pacing_and_the_destination_budget(self):
        client = self.client(SequenceOpener(Response(b"a")), max_requests_per_host=2)
        client.get_text("https://example.test/a")
        handler = PacedRedirectHandler(client._before_request)
        request = urllib.request.Request("https://example.test/a")
        redirected = handler.redirect_request(
            request, None, 302, "Found", {}, "https://example.test/b"
        )
        self.assertEqual(redirected.full_url, "https://example.test/b")
        self.assertEqual(self.timer.delays, [1.0])
        with self.assertRaisesRegex(SourceError, "request budget exceeded"):
            handler.redirect_request(request, None, 302, "Found", {}, "https://example.test/c")
        handler.redirect_request(request, None, 302, "Found", {}, "https://other.test/a")
        self.assertEqual(client._host_requests["other.test"], 1)

    def test_authentication_redirects_stop_before_a_login_request_and_hide_query_state(self):
        visited = []
        handler = PacedRedirectHandler(visited.append)
        response = io.BytesIO(b"redirect")
        with self.assertRaises(AuthenticationRedirect) as caught:
            handler.redirect_request(
                urllib.request.Request("https://example.test/document"), response,
                302, "Found", {}, "https://accounts.google.com/signin?state=private-state",
            )
        self.assertEqual(visited, [])
        self.assertTrue(response.closed)
        self.assertNotIn("private-state", str(caught.exception))

    def test_anonymous_cookies_allow_a_public_read_without_following_oauth(self):
        visited = []

        def https_open(handler, request):
            visited.append((request.full_url, dict(request.header_items())))
            headers = Message()
            status = 200
            body = b"public markdown"
            if len(visited) == 1:
                status = 302
                body = b"redirect"
                headers["Set-Cookie"] = "signin=0; Path=/; Secure"
                headers["Location"] = "https://example.test/oauth2authorize?state=private-state"
            response = urllib.response.addinfourl(io.BytesIO(body), headers, request.full_url, status)
            response.msg = "Found" if status == 302 else "OK"
            return response

        with mock.patch.object(urllib.request.HTTPSHandler, "https_open", https_open):
            client = self.client(None)
            self.assertEqual(client.get_text("https://example.test/pricing.md.txt"), "public markdown")
            self.assertEqual(client.get_text("https://example.test/pricing.md.txt"), "public markdown")
        self.assertEqual([url for url, headers in visited], ["https://example.test/pricing.md.txt"] * 2)
        self.assertNotIn("Cookie", visited[0][1])
        self.assertEqual(visited[1][1]["Cookie"], "signin=0")
        self.assertEqual([headers["User-agent"] for url, headers in visited], [DEFAULT_USER_AGENT] * 2)
        self.assertEqual(self.timer.delays, [2.0])

    def test_anonymous_session_retry_keeps_the_requested_browser_identity(self):
        sequence = SequenceOpener(
            AuthenticationRedirect("authentication redirect"),
            Response(b"markdown"), Response(b"notices"),
        )
        agents = []

        def opener(request, *, timeout):
            agents.append(request.get_header("User-agent"))
            return sequence(request, timeout=timeout)

        client = self.client(opener)
        self.assertEqual(client.get_text("https://example.test/pricing.md.txt"), "markdown")
        self.assertEqual(client.get_text("https://example.test/pricing.md.txt"), "markdown")
        self.assertEqual(client.get_text("https://example.test/notices"), "notices")
        self.assertEqual(agents, [DEFAULT_USER_AGENT] * 3)
        self.assertEqual(self.timer.delays, [2.0, 1.0])

    def test_anonymous_session_retry_cannot_repeat_unsafe_posts(self):
        sequence = SequenceOpener(
            AuthenticationRedirect("authentication redirect"),
            Response(b"should not read"),
        )
        with self.assertRaises(AuthenticationRedirect):
            self.client(sequence).request("https://example.test/document", method="POST")
        self.assertEqual(len(sequence.requests), 1)

    def test_a_repeated_authentication_redirect_does_not_try_to_authenticate(self):
        sequence = SequenceOpener(*(
            AuthenticationRedirect("authentication redirect") for _ in range(2)
        ))
        with self.assertRaises(AuthenticationRedirect):
            self.client(sequence).get_text("https://example.test/document")
        self.assertEqual(len(sequence.requests), 2)


if __name__ == "__main__":
    unittest.main()
