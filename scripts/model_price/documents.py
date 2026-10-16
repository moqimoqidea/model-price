"""Keep official document retrieval separate from HTTP and vendor price parsing."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

from .core import HttpClient, RETRYABLE_HTTP_STATUSES, now_iso
from .errors import HttpSourceError, SourceError

OPENAI_DOCS_MCP_URL = "https://developers.openai.com/mcp"
MCP_PROTOCOL_VERSION = "2025-03-26"


class DocumentClient(HttpClient):
    """Share one transport budget across official document representations."""

    _openai_documents: _OpenAIDocuments | None = None

    def get_text(self, url: str) -> str:
        canonical = _openai_document_url(url)
        if canonical is None:
            return super().get_text(url)
        if self._openai_documents is None:
            self._openai_documents = _OpenAIDocuments(self)
        return self._openai_documents.read(canonical)

    def document_evidence(self, url: str) -> dict[str, str]:
        canonical = _openai_document_url(url)
        if self._openai_documents is None or canonical is None:
            return {}
        return dict(self._openai_documents.evidence.get(canonical, {}))


def document_evidence(client: HttpClient, url: str) -> dict[str, dict[str, str]]:
    """Keep optional retrieval evidence out of parsers and fixture client contracts."""
    evidence = client.document_evidence(url) if isinstance(client, DocumentClient) else {}
    return {"retrieval": evidence} if evidence else {}


def _openai_document_url(url: str) -> str | None:
    parts = urlsplit(url)
    if (
        parts.scheme != "https" or parts.netloc != "developers.openai.com"
        or not parts.path.startswith("/api/docs/") or parts.query
    ):
        return None
    return urlunsplit(parts._replace(path=parts.path.removesuffix(".md"), fragment=""))


def _validate_document(url: str, text: str) -> str:
    """Reject error pages and search summaries before a catalogue can look empty."""
    if not text.lstrip().startswith("# "):
        raise SourceError(f"official document did not return Markdown; source: {url}")
    table_header = {
        "/api/docs/pricing": r"^\|\s*(?:Model|Model name)\s*\|",
        "/api/docs/deprecations": r"^\|\s*Shutdown date\s*\|[^\n]*Model",
    }.get(urlsplit(url).path)
    if table_header and not re.search(table_header, text, re.I | re.M):
        raise SourceError(f"official document published no readable model table; source: {url}")
    return text


class _OpenAIDocuments:
    """Reuse logical documents and switch only a proven blocked direct channel."""

    def __init__(self, client: HttpClient) -> None:
        self.client = client
        self.mcp = _DocsMCP(client)
        self.documents: dict[str, str] = {}
        self.failures: dict[str, SourceError] = {}
        self.evidence: dict[str, dict[str, str]] = {}
        self.direct_failure: SourceError | None = None

    def read(self, canonical: str) -> str:
        if canonical in self.documents:
            return self.documents[canonical]
        if canonical in self.failures:
            raise self.failures[canonical].with_traceback(None)
        try:
            return self._read(canonical)
        except SourceError as exc:
            self.failures[canonical] = exc
            raise

    def _read(self, canonical: str) -> str:
        direct_error = self.direct_failure
        if direct_error is None:
            try:
                text = _validate_document(canonical, self.client.request(canonical + ".md"))
            except HttpSourceError as exc:
                if exc.verification != "edge" and exc.status not in RETRYABLE_HTTP_STATUSES:
                    raise
                direct_error = exc
                if exc.verification == "edge":
                    self.direct_failure = exc
            except SourceError as exc:
                direct_error = exc
            else:
                return self._remember(canonical, text, "markdown", canonical + ".md")
        try:
            text = _validate_document(canonical, self.mcp.fetch(canonical))
        except SourceError as exc:
            raise SourceError(
                f"OpenAI document unavailable via Markdown and Docs MCP: {direct_error}; "
                f"MCP: {exc}; source: {canonical}"
            ) from exc
        return self._remember(canonical, text, "docs_mcp", OPENAI_DOCS_MCP_URL)

    def _remember(self, url: str, text: str, transport: str, endpoint: str) -> str:
        self.documents[url] = text
        self.evidence[url] = {
            "document_url": url,
            "transport": transport,
            "transport_url": endpoint,
            "document_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "retrieved_at": now_iso(),
        }
        return text


class _DocsMCP:
    """Read full pages from OpenAI's public stateless documentation server only."""

    def __init__(self, client: HttpClient) -> None:
        self.client = client
        self.ready = False
        self.identifier = 0
        self.failure: SourceError | None = None

    def fetch(self, url: str) -> str:
        if self.failure is not None:
            raise self.failure.with_traceback(None)
        if not self.ready:
            try:
                result = self._rpc("initialize", {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "model-price", "version": "1"},
                })
                if result.get("protocolVersion") != MCP_PROTOCOL_VERSION:
                    raise SourceError("OpenAI Docs MCP returned an unsupported protocol version")
                self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            except SourceError as exc:
                self.failure = exc
                raise
            self.ready = True
        result = self._rpc("tools/call", {
            "name": "fetch_openai_doc", "arguments": {"url": url},
        }, idempotent=True)
        content = result.get("content")
        if (
            result.get("isError") or not isinstance(content, list) or not content
            or any(
                not isinstance(item, dict) or item.get("type") != "text"
                or not isinstance(item.get("text"), str) or not item["text"].strip()
                for item in content
            )
        ):
            raise SourceError(f"OpenAI Docs MCP returned no complete document; source: {url}")
        return "\n".join(item["text"] for item in content)

    def _send(self, payload: dict[str, Any], *, idempotent: bool = False) -> str:
        try:
            return self.client.request(
                OPENAI_DOCS_MCP_URL, method="POST", data=json.dumps(payload).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
                },
                idempotent=idempotent,
            )
        except SourceError as exc:
            self.failure = SourceError(f"OpenAI Docs MCP transport failed: {exc}")
            raise self.failure from exc

    def _rpc(
        self, method: str, params: dict[str, Any], *, idempotent: bool = False,
    ) -> dict[str, Any]:
        self.identifier += 1
        identifier = self.identifier
        raw = self._send({
            "jsonrpc": "2.0", "id": identifier, "method": method, "params": params,
        }, idempotent=idempotent)
        try:
            for message in _rpc_messages(raw):
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise ValueError("invalid JSON-RPC envelope")
                if "id" not in message and "method" in message:
                    continue
                if type(message.get("id")) is not int or message["id"] != identifier:
                    raise ValueError("mismatched JSON-RPC response id")
                if "error" in message or not isinstance(message.get("result"), dict):
                    raise ValueError("JSON-RPC call failed")
                return message["result"]
            raise ValueError("missing JSON-RPC response")
        except (ValueError, TypeError) as exc:
            self.failure = SourceError(f"OpenAI Docs MCP protocol error: {exc}")
            raise self.failure from exc


def _rpc_messages(raw: str) -> Iterator[Any]:
    """Decode JSON or SSE data frames without accepting notifications as replies."""
    if raw.lstrip().startswith("{"):
        yield json.loads(raw)
        return
    data: list[str] = []
    for line in [*raw.splitlines(), ""]:
        if line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
        elif not line and data:
            yield json.loads("\n".join(data))
            data = []
