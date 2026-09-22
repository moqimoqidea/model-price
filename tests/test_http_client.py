import io
import http.client
import sys
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.core import HttpClient
from model_price.errors import SourceError


class Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.body


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
        "https://example.test/pricing",
        code,
        reason,
        response_headers,
        io.BytesIO(body),
    )


class HttpClientTests(unittest.TestCase):
    def test_a_document_is_fetched_only_once_per_process(self):
        opener = SequenceOpener(Response(b"price table"))
        client = HttpClient(opener=opener)

        self.assertEqual(client.get_text("https://example.test/a"), "price table")
        self.assertEqual(client.get_text("https://example.test/a"), "price table")
        self.assertEqual(len(opener.requests), 1)

    def test_safe_request_retries_transient_connections_with_full_jitter(self):
        opener = SequenceOpener(
            TimeoutError(),
            urllib.error.URLError("connection reset"),
            Response(b"ok"),
        )
        delays = []
        client = HttpClient(
            opener=opener,
            sleeper=delays.append,
            uniform=lambda low, high: high,
        )

        self.assertEqual(client.get_text("https://example.test/a"), "ok")
        self.assertEqual(delays, [0.5, 1.0])
        self.assertEqual(len(opener.requests), 3)

    def test_incomplete_read_is_retried_as_a_transport_failure(self):
        opener = SequenceOpener(
            http.client.IncompleteRead(b"partial", 10),
            Response(b"complete"),
        )
        client = HttpClient(
            opener=opener,
            sleeper=lambda delay: None,
            uniform=lambda low, high: 0,
        )
        self.assertEqual(client.get_text("https://example.test/a"), "complete")
        self.assertEqual(len(opener.requests), 2)

    def test_post_is_not_retried_unless_caller_marks_it_idempotent(self):
        unsafe = SequenceOpener(TimeoutError(), Response(b'{}'))
        with self.assertRaises(SourceError):
            HttpClient(opener=unsafe, sleeper=lambda delay: None).post_form(
                "https://example.test/api", {"query": "a"}
            )
        self.assertEqual(len(unsafe.requests), 1)

        safe = SequenceOpener(TimeoutError(), Response(b'{"ok": true}'))
        result = HttpClient(
            opener=safe,
            sleeper=lambda delay: None,
            uniform=lambda low, high: 0,
        ).post_form(
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
            HttpClient(opener=opener).get_text("https://example.test/missing")
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
            HttpClient(
                opener=opener,
                sleeper=lambda delay: None,
                uniform=lambda low, high: 0,
            ).get_text("https://example.test/a")
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
        sleeps = []
        client = HttpClient(
            opener=delayed,
            sleeper=sleeps.append,
            clock=lambda: now,
        )
        self.assertEqual(client.get_text("https://example.test/a"), "ok")
        self.assertEqual(sleeps, [10.0])

        deferred = SequenceOpener(
            http_error(429, "Too Many Requests", headers={"Retry-After": "60"}),
            Response(b"should not be read"),
        )
        with self.assertRaisesRegex(SourceError, r"Retry-After requested 60s"):
            HttpClient(opener=deferred).get_text("https://example.test/b")
        self.assertEqual(len(deferred.requests), 1)

    def test_cloudflare_challenge_is_retried_at_most_once(self):
        opener = SequenceOpener(
            http_error(403, "Forbidden", headers={"cf-mitigated": "challenge"}),
            http_error(403, "Forbidden", body=b"<title>Just a moment</title>"),
            Response(b"should not be read"),
        )
        with self.assertRaisesRegex(SourceError, r"anti-bot challenge"):
            HttpClient(
                opener=opener,
                sleeper=lambda delay: None,
                uniform=lambda low, high: 0,
            ).get_text("https://example.test/a")
        self.assertEqual(len(opener.requests), 2)

    def test_bare_timeout_always_has_a_nonempty_diagnostic(self):
        opener = SequenceOpener(TimeoutError())
        with self.assertRaisesRegex(
            SourceError, r"timed out.*1 attempt\(s\).*TimeoutError"
        ):
            HttpClient(attempts=1, opener=opener).get_text(
                "https://example.test/a"
            )

    def test_per_host_budget_stops_a_runaway_request_loop(self):
        opener = SequenceOpener(Response(b"a"), Response(b"b"), Response(b"c"))
        client = HttpClient(
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
            HttpClient(opener=opener).post_form(
                "https://example.test/api", {"query": "a"}, idempotent=True
            )
        self.assertEqual(len(opener.requests), 1)


if __name__ == "__main__":
    unittest.main()
