"""Table readers for vendor documents published as HTML or Markdown."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

from .text import clean_text, numeric_values

HEADING_TAGS = ("h1", "h2", "h3", "h4")

# Markdown escapes the punctuation it would otherwise interpret. Vendor cells keep
# those backslashes in the source (``deepseek\-v4\-flash正式版``, ``输入长度 \[0, 32K)``),
# so they have to be removed before a cell can be named or matched.
MARKDOWN_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+.!|~>-])")


def unescape_markdown(value: str) -> str:
    """Drop the backslash escapes Markdown puts in front of punctuation."""
    return MARKDOWN_ESCAPE_RE.sub(r"\1", value)


class TextTableParser(HTMLParser):
    """Collect HTML tables along with the heading path that precedes them."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self.table_headings: list[list[str]] = []
        self.path: list[str] = []
        self.heading_level: int | None = None
        self.heading_text: list[str] = []
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in HEADING_TAGS and self.table is None:
            self.heading_level = int(tag[1])
            self.heading_text = []
        elif tag == "table":
            self.table = []
        elif self.table is not None and tag == "tr":
            self.row = []
        elif self.row is not None and tag in ("td", "th"):
            self.cell = []
        elif self.cell is not None and tag == "br":
            self.cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self.heading_level is not None:
            self.heading_text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in HEADING_TAGS and self.heading_level == int(tag[1]):
            # A heading replaces everything from its own level down, so an h3
            # keeps the h2 above it while an h2 starts a new top-level section.
            self.path = self.path[: self.heading_level - 1] + [
                clean_text(" ".join(self.heading_text))
            ]
            self.heading_level = None
        elif tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None and self.table is not None:
            if self.row:
                self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table_headings.append(list(self.path))
            self.table = None


def split_markdown_row(line: str) -> list[str]:
    """Split a table row, keeping empty cells such as a carried model name."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [unescape_markdown(cell.strip()) for cell in stripped.split("|")]


def markdown_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    """Return Markdown tables with the heading path that precedes them."""
    tables: list[tuple[list[str], list[list[str]]]] = []
    path: list[str] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            title = unescape_markdown(clean_text(line.lstrip("# ")))
            path = path[: level - 1] + [title]
        if (
            line.startswith("|")
            and index + 1 < len(lines)
            and re.match(r"^\s*\|(?:\s*:?-+\s*\|)+\s*$", lines[index + 1])
        ):
            rows = [split_markdown_row(line)]
            index += 2
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                rows.append(split_markdown_row(lines[index]))
                index += 1
            tables.append((list(path), rows))
            continue
        index += 1
    return tables


def markdown_link_text(value: str) -> str:
    """Flatten ``[label](url)`` into ``label``."""
    flattened = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", unescape_markdown(value))
    return clean_text(flattened)


def markdown_json_rows(document: str) -> list[list[str]]:
    """Read the JSON-array rows some vendors embed directly in Markdown."""
    rows = []
    for line in document.splitlines():
        candidate = line.strip().rstrip(",")
        if not candidate.startswith("[") or not candidate.endswith("]"):
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list) and value and isinstance(value[0], str):
            rows.append(value)
    return rows


def headed_document_tables(document: str) -> list[tuple[list[str], list[list[str]]]]:
    """Read pricing tables from either rendered HTML or official Markdown."""
    parser = TextTableParser()
    parser.feed(document.replace("\x00", ""))
    if parser.tables:
        return list(zip(parser.table_headings, parser.tables))
    return markdown_tables(document)


# Headers that bill a non-token unit (audio duration, characters, calls) are not
# token prices, so a per-million-token adapter must never read them. Without this
# guard "输入音频时长" (input audio duration, billed per hour) looks like "input".
NON_TOKEN_BILLING_MARKERS = ("时长", "小时", "秒", "字符", "千次", "万次", "/次")

# Cache *storage* is billed per million tokens per hour, so it stays a token price
# even though its header names an hour. Detecting it first keeps the guard above
# from rejecting it along with genuine duration billing.
CACHE_STORAGE_MARKERS = ("缓存存储", "缓存空间")


def token_price_kind(header: str) -> str | None:
    """Map English and Chinese token-price headers without model allowlists."""
    value = clean_text(header).lower().replace("-", "").replace(" ", "")
    if any(marker in value for marker in CACHE_STORAGE_MARKERS) or (
        "cache" in value and "storage" in value
    ):
        return "cache_storage"
    if any(marker.replace(" ", "") in value for marker in NON_TOKEN_BILLING_MARKERS):
        return None
    cached = "cache" in value or "缓存" in value
    if cached and ("write" in value or "写入" in value or "创建" in value):
        return "cache_write"
    # A cache *miss* is billed at the regular input rate, so it has to be tested
    # before the hit branch: "输入（未命中缓存）" matches "缓存" and "输入" too.
    if "未命中" in value or "不命中" in value or "miss" in value:
        return "input"
    if cached and any(
        label in value for label in ("input", "read", "hit", "输入", "读取", "命中")
    ):
        return "cache_hit"
    if "input" in value or "prompt" in value or "输入" in value:
        return "input"
    if "output" in value or "completion" in value or "输出" in value:
        return "output"
    return None


def monetary_amount(value: str, header: str, currency: str) -> str | None:
    """Extract a monetary amount only when the cell or header names currency."""
    cell = clean_text(value).replace(",", "")
    heading = clean_text(header).lower()
    if re.search(r"\bfree\b", cell, re.I) or any(
        label in cell for label in ("免费", "不收费")
    ):
        return None
    if currency == "USD":
        match = re.search(r"\$\s*(\d+(?:\.\d+)?)", cell)
        if match:
            return match.group(1)
        currency_named = "usd" in cell.lower() or "usd" in heading
    else:
        match = re.search(r"(?:¥|￥)\s*(\d+(?:\.\d+)?)", cell)
        if match:
            return match.group(1)
        match = re.search(r"(\d+(?:\.\d+)?)\s*元", cell)
        if match:
            return match.group(1)
        currency_named = any(label in heading for label in ("元", "cny", "rmb"))
    if currency_named:
        values = numeric_values(cell)
        return values[0] if values else None
    return None
