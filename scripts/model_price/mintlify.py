"""Recover literal page tables when Mintlify's Markdown export omits components."""

from __future__ import annotations

import json
import re
from typing import Any, Iterator
from urllib.parse import urlsplit

from .errors import SourceError
from .literals import STRING_LITERAL, code_matches, literal_at, value_end

FLIGHT_PUSH = re.compile(r"\bself\.__next_f\.push\(\s*")
COMPONENT_TABLE = re.compile(r"\b_jsxs?\(\s*DocTable\s*,\s*(?=\{)")
RAW_TABLE = re.compile(r"<DocTable\b(?P<body>.*?)/>", re.S)
JSX_CALL = re.compile(r"\b_jsxs?\s*\(")


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


def _visible_text(value: Any) -> str:
    """Flatten the literal text a component's ``children`` prop publishes."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_visible_text(item) for item in value)
    if isinstance(value, dict):
        return _visible_text(value["children"]) if "children" in value else ""
    if value is None or isinstance(value, bool):
        return ""
    return str(value)


def _argument_boundary(source: str) -> int:
    """Index of the comma after a component expression, ignoring nested data."""
    depth = 0
    position = 0
    while position < len(source):
        character = source[position]
        if character in "\"'`":
            literal = STRING_LITERAL.match(source, position)
            if not literal:
                raise SourceError("official document contains an unterminated string")
            position = literal.end()
            continue
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif character == "," and depth == 0:
            return position
        position += 1
    raise SourceError("official component call has no literal props")


def _call_end(source: str, start: int) -> int:
    """Index just past the ``)`` that closes the call opened at ``start``."""
    depth = 0
    position = start
    while position < len(source):
        character = source[position]
        if character in "\"'`":
            literal = STRING_LITERAL.match(source, position)
            if not literal:
                raise SourceError("official document contains an unterminated string")
            position = literal.end()
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return position + 1
        position += 1
    raise SourceError("official component call is incomplete")


def _jsx_text(source: str, position: int) -> tuple[str, int]:
    """Read one component call's visible text without running the component.

    Mintlify compiles a table title or a linked model cell to
    ``_jsx(Component,{...})``. Only the literal ``children`` prop is visible in
    that cell; the component itself is never executed. A computed child still
    reaches ``literal_at`` and is rejected there.
    """
    start = position
    while start < len(source) and source[start].isspace():
        start += 1
    if start >= len(source) or source[start] != "(":
        raise SourceError("official component call is not a literal")
    end = _call_end(source, start)
    inner = source[start + 1:end - 1]
    comma = _argument_boundary(inner)
    props = inner[comma + 1:].lstrip()
    if not props.startswith("{"):
        raise SourceError("official component call has no literal props")
    props_end = value_end(props, 0)
    fields = literal_at(_replace_jsx_calls(props[:props_end]))
    if not isinstance(fields, dict):
        raise SourceError("official component props are not a literal object")
    return _visible_text(fields.get("children")), end


def _replace_jsx_calls(source: str) -> str:
    """Replace literal component calls with their visible text, keeping code inert."""
    pieces: list[str] = []
    position = 0
    while position < len(source):
        character = source[position]
        if character in "\"'`":
            literal = STRING_LITERAL.match(source, position)
            if not literal:
                raise SourceError("official document contains an unterminated string")
            pieces.append(source[position:literal.end()])
            position = literal.end()
            continue
        call = JSX_CALL.match(source, position)
        if call:
            text, position = _jsx_text(source, call.end() - 1)
            pieces.append(json.dumps(text, ensure_ascii=False))
            continue
        pieces.append(character)
        position += 1
    return "".join(pieces)


def _literal_with_jsx(text: str, position: int) -> Any:
    """Decode one literal value, allowing component wrappers inside it."""
    end = value_end(text, position)
    return literal_at(_replace_jsx_calls(text[position:end]))


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
            fields[key] = _literal_with_jsx(body, start.end())
        tables.append(_table(fields["columns"], fields["rows"]))
    if tables or "<script" not in document.lower():
        return tables
    source = document_source(document, url)
    for match in code_matches(source, COMPONENT_TABLE):
        fields = _literal_with_jsx(source, match.end())
        tables.append(_table(fields.get("columns"), fields.get("rows")))
    if not tables:
        raise SourceError("official document MDX source published no literal DocTable data")
    return tables
