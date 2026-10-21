"""Recover literal page tables when Mintlify's Markdown export omits components."""

from __future__ import annotations

import json
import re
from typing import Any, Iterator
from urllib.parse import urlsplit

from .errors import SourceError
from .literals import code_matches, literal_at

FLIGHT_PUSH = re.compile(r"\bself\.__next_f\.push\(\s*")
COMPONENT_TABLE = re.compile(r"\b_jsxs?\(\s*DocTable\s*,\s*(?=\{)")
RAW_TABLE = re.compile(r"<DocTable\b(?P<body>.*?)/>", re.S)


def _flight_records(document: str) -> dict[str, Any]:
    """Decode the page's own streamed records, including byte-counted text."""
    chunks: list[bytes] = []
    decoder = json.JSONDecoder()
    for script in re.findall(r"<script\b[^>]*>(.*?)</script\s*>", document, re.I | re.S):
        for match in code_matches(script, FLIGHT_PUSH):
            try:
                payload, _ = decoder.raw_decode(script, match.end())
            except ValueError as exc:
                raise SourceError("official document contains invalid page data") from exc
            if isinstance(payload, list) and len(payload) == 2 and payload[0] == 1 and isinstance(payload[1], str):
                chunks.append(payload[1].encode("utf-8"))
    stream = b"".join(chunks)
    records: dict[str, Any] = {}
    position = 0
    while position < len(stream):
        if stream[position:position + 1] == b"\n":
            position += 1
            continue
        header = re.match(rb"([0-9a-f]*):", stream[position:])
        if not header:
            raise SourceError("official document page data lost its record boundary")
        key = header[1].decode("ascii")
        position += header.end()
        if stream[position:position + 1] == b"T":
            size = re.match(rb"T([0-9a-f]+),", stream[position:])
            if not size:
                raise SourceError("official document contains an invalid text record")
            position += size.end()
            end = position + int(size[1], 16)
            if end > len(stream):
                raise SourceError("official document contains an incomplete text record")
            try:
                records[key] = stream[position:end].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SourceError("official document text record has an invalid byte length") from exc
            position = end
        else:
            end = stream.find(b"\n", position)
            end = len(stream) if end < 0 else end
            try:
                value = json.loads(stream[position:end])
                if key:
                    records[key] = value
            except (ValueError, UnicodeDecodeError):
                # Import hints and other typed Flight records do not own MDX.
                pass
            position = end
    return records


def _objects(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def document_source(document: str, url: str) -> str:
    """Only the requested page's declared MDX reference can supply table data."""
    records = _flight_records(document)
    path = urlsplit(url).path.removeprefix("/docs").removesuffix(".md")
    references: set[str] = set()
    for record in records.values():
        for item in _objects(record):
            source = item.get("mdxSource")
            if not isinstance(source, dict):
                continue
            metadata = (source.get("scope") or {}).get("pageMetadata") or {}
            if metadata.get("href") == path and isinstance(source.get("compiledSource"), str):
                references.add(source["compiledSource"])
    if len(references) != 1:
        raise SourceError("official document did not identify one page-owned MDX source")
    reference = references.pop()
    source = records.get(reference[1:]) if reference.startswith("$") else reference
    if not isinstance(source, str) or not source:
        raise SourceError("official document omitted its referenced MDX source")
    return source


def _table(columns: Any, rows: Any) -> list[list[str]]:
    if (
        not isinstance(columns, list) or not columns
        or any(not isinstance(column, dict) or not isinstance(column.get("title"), str) for column in columns)
        or not isinstance(rows, list)
        or any(not isinstance(row, list) or not row or any(not isinstance(cell, str) for cell in row) for row in rows)
    ):
        raise SourceError("official document published an unreadable component table")
    return [[column["title"] for column in columns], *rows]


def document_tables(document: str, url: str) -> list[list[list[str]]]:
    """Read literal DocTable properties in source MDX or its owned compiled form."""
    tables = []
    for match in RAW_TABLE.finditer(document):
        body = match["body"]
        fields: dict[str, Any] = {}
        for key in ("columns", "rows"):
            start = re.search(r"\b" + key + r"\s*=\s*\{\s*(?=\[)", body)
            if not start:
                raise SourceError("official component table omitted its columns or rows")
            fields[key] = literal_at(body, start.end())
        tables.append(_table(fields["columns"], fields["rows"]))
    if tables or "<script" not in document.lower():
        return tables
    source = document_source(document, url)
    for match in code_matches(source, COMPONENT_TABLE):
        fields = literal_at(source, match.end())
        tables.append(_table(fields.get("columns"), fields.get("rows")))
    if not tables:
        raise SourceError("official document MDX source published no literal DocTable data")
    return tables
