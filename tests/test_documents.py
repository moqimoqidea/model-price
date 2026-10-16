"""Exercise official document fallback without network reads or stale baselines."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.delta import scan_providers
from model_price.descriptions import build_description_resolver
from model_price.descriptions.sources import OpenAIDescriptionSource
from model_price.documents import DocumentClient, MCP_PROTOCOL_VERSION, OPENAI_DOCS_MCP_URL
from model_price.errors import SourceError
from model_price.lifecycle_sources import SOURCES, read_events
from model_price.providers.openai import OPENAI_MARKDOWN_URL, OPENAI_URL, OpenAIAdapter
from model_price.registry import build_adapters
from model_price.snapshots import SnapshotStore
from test_http_client import Response, SequenceOpener, Timer, http_error

PRICE_DOCUMENT = """# Pricing

### Standard pricing data

| Model | Short context input | Short context output |
| --- | --- | --- |
| gpt-test | $2.00 | $10.00 |
"""
NOTICE_DOCUMENT = """# Deprecations

### 2026-10-01: Old model

| Shutdown date | Model / system | Recommended replacement |
| --- | --- | --- |
| 2027-04-01 | `gpt-old` | `gpt-test` |
"""
MODEL_DOCUMENT = (Path(__file__).with_name("fixtures") / "openai-model-mcp.md").read_text()


def blocked(url: str = OPENAI_MARKDOWN_URL) -> Exception:
    error = http_error(403, "Forbidden", headers={
        "x-vercel-mitigated": "challenge", "Retry-After": "30",
    })
    error.url = url
    return error


def rpc_response(identifier: int, result: dict, *, sse: bool = False) -> Response:
    body = json.dumps({"jsonrpc": "2.0", "id": identifier, "result": result})
    if sse:
        body = (
            ': keepalive\r\nevent: message\r\n'
            'data: {"jsonrpc":"2.0","method":"notifications/message"}\r\n\r\n'
            'event: message\r\ndata: ' + body + '\r\n\r\n'
        )
    return Response(body.encode(), url=OPENAI_DOCS_MCP_URL)


def initialized() -> list[Response]:
    return [
        rpc_response(1, {"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {"tools": {}}}),
        Response(b"", url=OPENAI_DOCS_MCP_URL),
    ]


def document_response(identifier: int, text: str, **options) -> Response:
    return rpc_response(identifier, {"content": [{"type": "text", "text": text}]}, **options)


class DocumentTests(unittest.TestCase):
    def client(self, *outcomes, **options) -> tuple[DocumentClient, SequenceOpener, Timer]:
        timer = Timer()
        opener = SequenceOpener(*outcomes)
        client = DocumentClient(
            opener=opener, sleeper=timer.sleep, monotonic=timer.monotonic,
            uniform=lambda low, high: high, min_request_interval=0, **options,
        )
        return client, opener, timer

    def test_readable_markdown_needs_no_mcp_and_canonical_aliases_share_it(self):
        client, opener, _ = self.client(Response(PRICE_DOCUMENT.encode()))
        self.assertEqual(client.get_text(OPENAI_MARKDOWN_URL), PRICE_DOCUMENT)
        self.assertEqual(client.get_text(OPENAI_URL), PRICE_DOCUMENT)
        self.assertEqual(len(opener.requests), 1)

    def test_non_openai_reads_and_json_apis_keep_the_plain_transport(self):
        client, opener, _ = self.client(Response(b"other"), Response(b'{"ok":true}'))
        self.assertEqual(client.get_text("https://other.test/doc.md"), "other")
        self.assertEqual(client.request("https://developers.openai.com/mcp", method="POST"), '{"ok":true}')
        self.assertEqual(len(opener.requests), 2)

    def test_edge_failure_switches_once_for_price_notice_and_model_documents(self):
        client, opener, timer = self.client(
            *(blocked() for _ in range(3)), *initialized(),
            document_response(2, PRICE_DOCUMENT),
            document_response(3, NOTICE_DOCUMENT, sse=True),
            document_response(4, MODEL_DOCUMENT),
        )
        self.assertEqual(len(OpenAIAdapter(client).catalog_records()), 1)
        _, events, _ = read_events("openai", client, [])
        self.assertEqual(events[0]["model_id"], "gpt-old")
        description = OpenAIDescriptionSource(client).describe("gpt-6.1-sol")
        self.assertEqual(len(description["capabilities"]), 7)
        self.assertEqual(client.get_text(OPENAI_URL), PRICE_DOCUMENT)
        self.assertEqual(sum(timer.delays), 60)
        requests = [request for request, _ in opener.requests]
        self.assertEqual([r.full_url for r in requests[:3]], [OPENAI_MARKDOWN_URL] * 3)
        self.assertEqual([r.full_url for r in requests[3:]], [OPENAI_DOCS_MCP_URL] * 5)
        payloads = [json.loads(r.data) for r in requests[3:]]
        self.assertEqual([p["method"] for p in payloads], [
            "initialize", "notifications/initialized", "tools/call", "tools/call", "tools/call",
        ])
        self.assertEqual([p["params"]["arguments"]["url"] for p in payloads[2:]], [
            OPENAI_URL, SOURCES["openai"].removesuffix(".md"),
            "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
        ])

    def test_an_http200_checkpoint_also_reaches_the_official_fallback(self):
        client, _, _ = self.client(
            *(Response(b"<html><title>Vercel Security Checkpoint</title></html>") for _ in range(3)),
            *initialized(), document_response(2, PRICE_DOCUMENT),
        )
        self.assertEqual(client.get_text(OPENAI_MARKDOWN_URL), PRICE_DOCUMENT)

    def test_ordinary_permission_and_missing_pages_do_not_try_other_transports(self):
        for code in (401, 403, 404, 410):
            with self.subTest(code=code):
                client, opener, _ = self.client(http_error(code, "Rejected"))
                with self.assertRaisesRegex(SourceError, f"HTTP {code}"):
                    client.get_text(OPENAI_MARKDOWN_URL)
                self.assertEqual(len(opener.requests), 1)

    def test_invalid_html_is_not_accepted_as_a_markdown_document(self):
        client, _, _ = self.client(
            Response(b"<html><title>Unavailable</title></html>"),
            *initialized(), document_response(2, PRICE_DOCUMENT),
        )
        self.assertEqual(client.get_text(OPENAI_MARKDOWN_URL), PRICE_DOCUMENT)

    def test_transient_failure_on_one_page_does_not_close_other_direct_pages(self):
        client, opener, _ = self.client(
            TimeoutError("read timed out"), *initialized(), document_response(2, PRICE_DOCUMENT),
            Response(NOTICE_DOCUMENT.encode()), attempts=1,
        )
        self.assertEqual(client.get_text(OPENAI_MARKDOWN_URL), PRICE_DOCUMENT)
        self.assertEqual(client.get_text(SOURCES["openai"]), NOTICE_DOCUMENT)
        self.assertEqual(opener.requests[-1][0].full_url, SOURCES["openai"])

    def test_an_unavailable_mcp_endpoint_is_not_probed_for_every_document(self):
        client, opener, _ = self.client(*(blocked() for _ in range(3)), TimeoutError("MCP timed out"))
        for url in (OPENAI_MARKDOWN_URL, SOURCES["openai"], OPENAI_MARKDOWN_URL):
            with self.assertRaisesRegex(SourceError, "MCP.*timed out"):
                client.get_text(url)
        self.assertEqual(len(opener.requests), 4)

    def test_mcp_endpoint_404_is_a_source_error_not_a_missing_model(self):
        endpoint_error = http_error(404, "Not Found")
        endpoint_error.url = OPENAI_DOCS_MCP_URL
        model_url = "https://developers.openai.com/api/docs/models/gpt-6.1-sol.md"
        client, opener, _ = self.client(
            *(blocked(model_url) for _ in range(3)), endpoint_error,
        )
        with tempfile.TemporaryDirectory() as directory:
            result = build_description_resolver(client, cache_dir=Path(directory)).resolve([
                {"provider_id": "openai", "model_id": "gpt-6.1-sol"},
            ])
        self.assertEqual(result["status"], "source_error")
        self.assertEqual(len(opener.requests), 4)

    def test_direct_model_404_remains_not_found(self):
        client, opener, _ = self.client(http_error(404, "Not Found"))
        with tempfile.TemporaryDirectory() as directory:
            result = build_description_resolver(client, cache_dir=Path(directory)).resolve([
                {"provider_id": "openai", "model_id": "gpt-missing"},
            ])
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(len(opener.requests), 1)

    def test_only_readonly_document_calls_receive_post_retries(self):
        client, opener, _ = self.client(
            *(blocked() for _ in range(3)), *initialized(), TimeoutError(),
            document_response(2, PRICE_DOCUMENT),
        )
        self.assertEqual(client.get_text(OPENAI_MARKDOWN_URL), PRICE_DOCUMENT)
        self.assertEqual(opener.requests[-1][0].data, opener.requests[-2][0].data)

    def test_mcp_protocol_errors_stop_the_connection_for_this_run(self):
        for response in (
            Response(b"not json"),
            rpc_response(99, {"protocolVersion": MCP_PROTOCOL_VERSION}),
            rpc_response(1, {"protocolVersion": "unsupported"}),
            Response(b'{"jsonrpc":"2.0","id":1,"error":{"code":-32603,"message":"failed"}}'),
        ):
            with self.subTest(body=response.body):
                client, opener, _ = self.client(*(blocked() for _ in range(3)), response)
                for url in (OPENAI_MARKDOWN_URL, SOURCES["openai"]):
                    with self.assertRaises(SourceError):
                        client.get_text(url)
                self.assertEqual(len(opener.requests), 4)

    def test_tool_errors_and_incomplete_documents_never_replace_a_baseline(self):
        for result in (
            {"isError": True, "content": [{"type": "text", "text": "No document"}]},
            {"content": []},
            {"content": [{"type": "text", "text": "# Pricing\n\nSee the official website."}]},
            {"content": [{"type": "text", "text": "<html>Unavailable</html>"}]},
        ):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as directory:
                store = SnapshotStore(Path(directory))
                good, _, _ = self.client(Response(PRICE_DOCUMENT.encode()))
                scan_providers([OpenAIAdapter(good)], store, captured_at="2026-10-15T12:00:00+08:00")
                before = store.read("openai")
                client, opener, _ = self.client(
                    *(blocked() for _ in range(3)), *initialized(), rpc_response(2, result),
                )
                report = scan_providers([OpenAIAdapter(client)], store, captured_at="2026-10-16T12:00:00+08:00")
                self.assertEqual(report["providers"][0]["status"], "source_error")
                self.assertEqual(store.read("openai"), before)
                with self.assertRaises(SourceError):
                    client.get_text(OPENAI_MARKDOWN_URL)
                self.assertEqual(len(opener.requests), 6)

    def test_a_document_tool_error_does_not_hide_another_readable_document(self):
        client, opener, _ = self.client(
            *(blocked() for _ in range(3)), *initialized(), rpc_response(2, {"isError": True}),
            document_response(3, NOTICE_DOCUMENT),
        )
        with self.assertRaises(SourceError):
            client.get_text(OPENAI_MARKDOWN_URL)
        self.assertEqual(client.get_text(SOURCES["openai"]), NOTICE_DOCUMENT)
        self.assertEqual(len(opener.requests), 7)

    def test_fallback_keeps_host_limits_and_long_retry_after_pauses(self):
        for code, headers, options in (
            (429, {"Retry-After": "120"}, {}),
            (403, {"x-vercel-mitigated": "challenge", "Retry-After": "120"}, {}),
            (403, {"x-vercel-mitigated": "challenge"}, {"max_requests_per_host": 1}),
        ):
            with self.subTest(code=code, options=options):
                error = http_error(code, "Rejected", headers=headers)
                error.url = OPENAI_MARKDOWN_URL
                client, opener, _ = self.client(error, **options)
                with self.assertRaises(SourceError):
                    client.get_text(OPENAI_MARKDOWN_URL)
                self.assertEqual(len(opener.requests), 1)

    def test_registered_readers_share_fallback_and_archive_its_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SnapshotStore(root / "snapshots")
            first, _, _ = self.client(Response(PRICE_DOCUMENT.encode()))
            scan_providers([OpenAIAdapter(first)], store, captured_at="2026-10-15T12:00:00+08:00")
            client, _, _ = self.client(
                *(blocked() for _ in range(3)), *initialized(),
                document_response(2, PRICE_DOCUMENT), document_response(3, NOTICE_DOCUMENT),
                document_response(4, MODEL_DOCUMENT),
            )
            adapter = build_adapters(client, cache_dir=root / "cache", refresh=True)["openai"]
            report = scan_providers(
                [adapter], store, lifecycle_client=client,
                captured_at="2026-10-16T12:00:00+08:00",
            )["providers"][0]
            self.assertEqual(report["status"], "unchanged")
            price_source = adapter.catalog_records()[0]["source"]
            self.assertEqual(price_source["retrieval"]["transport"], "docs_mcp")
            self.assertEqual(price_source["retrieval"]["transport_url"], OPENAI_DOCS_MCP_URL)
            self.assertEqual(len(price_source["retrieval"]["document_sha256"]), 64)
            self.assertEqual(store.read("openai")["source"]["documents"], [price_source["retrieval"]])
            self.assertEqual(report["lifecycle"]["source"]["retrieval"]["transport"], "docs_mcp")
            resolver = build_description_resolver(client, cache_dir=root / "cache", refresh=True)
            description = resolver.resolve([{"provider_id": "openai", "model_id": "gpt-6.1-sol"}])
            self.assertEqual(description["status"], "available")
            self.assertEqual(description["source"]["retrieval"]["transport"], "docs_mcp")


if __name__ == "__main__":
    unittest.main()
