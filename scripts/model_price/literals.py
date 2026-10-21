"""Read published JavaScript data without evaluating code or interpolated text."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from .errors import SourceError

STRING_LITERAL = re.compile(r"`(?:\\[\s\S]|[^`\\])*`|\"(?:\\[\s\S]|[^\"\\])*\"|'(?:\\[\s\S]|[^'\\])*'")
ESCAPES = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
LITERAL_TOKEN = re.compile(
    STRING_LITERAL.pattern + r"|void\s+0|![01]|[{}\[\]:,]|"
    r"-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|[A-Za-z_$][\w$]*"
)


def string_value(value: str) -> str:
    """Decode string escapes while refusing a template's runtime substitutions."""
    if value.startswith("`"):
        for match in re.finditer(r"\$\{", value):
            prefix = value[:match.start()]
            if (len(prefix) - len(prefix.rstrip("\\"))) % 2 == 0:
                raise SourceError("official document contains dynamic template content")

    def unescape(match: re.Match[str]) -> str:
        token = match[1]
        if token.startswith(("u", "x")):
            return chr(int(token[1:].strip("{}"), 16))
        if token in ("\n", "\r\n"):
            return ""
        return ESCAPES.get(token, token)

    return re.sub(
        r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\r?\n|.)",
        unescape,
        value[1:-1],
    )


def code_matches(text: str, pattern: re.Pattern[str]) -> Iterator[re.Match[str]]:
    """Only code tokens count; quoted examples and comments remain inert."""
    position = 0
    while position < len(text):
        literal = STRING_LITERAL.match(text, position)
        if literal:
            position = literal.end()
            continue
        if text.startswith("//", position):
            end = text.find("\n", position + 2)
            position = len(text) if end < 0 else end + 1
            continue
        if text.startswith("/*", position):
            end = text.find("*/", position + 2)
            if end < 0:
                raise SourceError("official document contains an unterminated comment")
            position = end + 2
            continue
        match = pattern.match(text, position)
        if match:
            yield match
            position = match.end()
        else:
            position += 1


def value_end(text: str, start: int) -> int:
    """Balance an expression while keeping delimiters inside strings inert."""
    stack: list[str] = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    position = start
    while position < len(text):
        character = text[position]
        if character in "\"'`":
            literal = STRING_LITERAL.match(text, position)
            if not literal:
                raise SourceError("official document contains an unterminated string")
            position = literal.end()
            continue
        if character in pairs:
            stack.append(pairs[character])
        elif character in ")]}":
            if not stack:
                return position
            if stack.pop() != character:
                raise SourceError("official document contains unbalanced component content")
        elif character == "," and not stack:
            return position
        position += 1
    if stack:
        raise SourceError("official document contains incomplete component content")
    return position


def literal_at(text: str, position: int = 0) -> Any:
    """Decode data-only objects and arrays, refusing calls and computed values."""
    tokens: list[str] = []
    depth = 0
    while position < len(text):
        while position < len(text) and text[position].isspace():
            position += 1
        match = LITERAL_TOKEN.match(text, position)
        if not match:
            raise SourceError("official document contains an unsupported data literal")
        token = match.group()
        position = match.end()
        if token.startswith(('"', "'", "`")):
            token = json.dumps(string_value(token))
        elif token in ("!0", "!1"):
            token = "true" if token == "!0" else "false"
        elif re.fullmatch(r"void\s+0", token):
            token = "null"
        elif token not in ("null", "true", "false") and re.match(r"[A-Za-z_$]", token):
            if not text[position:].lstrip().startswith(":"):
                raise SourceError("official document data is not a literal")
            token = json.dumps(token)
        elif token.startswith("."):
            token = "0" + token
        elif token.startswith("-."):
            token = "-0" + token[1:]
        tokens.append(token)
        if token in ("{", "["):
            depth += 1
        elif token in ("}", "]"):
            depth -= 1
        if depth == 0:
            break
    following = text[position:].lstrip()
    if following and following[0] not in ",})];":
        raise SourceError("official document data contains a computed expression")
    # A trailing comma is valid in the published JavaScript data, unlike JSON.
    tokens = [
        token for index, token in enumerate(tokens)
        if not (token == "," and index + 1 < len(tokens) and tokens[index + 1] in ("}", "]"))
    ]
    try:
        return json.loads("".join(tokens), parse_float=str)
    except (ValueError, TypeError) as exc:
        raise SourceError("official document data could not be decoded") from exc
