"""Table readers for vendor documents published as HTML or Markdown."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

from .text import clean_text, numeric_values


class TextTableParser(HTMLParser):
    """Collect HTML tables along with the nearest preceding heading."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self.table_headings: list[str] = []
        self.heading = ""
        self.heading_tag: str | None = None
        self.heading_text: list[str] = []
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h1", "h2", "h3", "h4"} and self.table is None:
            self.heading_tag = tag
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
        if self.heading_tag:
            self.heading_text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == self.heading_tag:
            self.heading = clean_text(" ".join(self.heading_text))
            self.heading_tag = None
        elif tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None and self.table is not None:
            if self.row:
                self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table_headings.append(self.heading)
            self.table = None


def split_markdown_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def markdown_tables(text: str) -> list[tuple[str, list[list[str]]]]:
    """Return Markdown tables with their nearest preceding heading."""
    tables: list[tuple[str, list[list[str]]]] = []
    heading = ""
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line.startswith("#"):
            heading = clean_text(line.lstrip("# "))
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
            tables.append((heading, rows))
            continue
        index += 1
    return tables


def markdown_link_text(value: str) -> str:
    """Flatten ``[label](url)`` into ``label``."""
    return clean_text(re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", value))


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


def headed_document_tables(document: str) -> list[tuple[str, list[list[str]]]]:
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


def token_price_kind(header: str) -> str | None:
    """Map English and Chinese token-price headers without model allowlists."""
    value = clean_text(header).lower().replace("-", "").replace(" ", "")
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
        label in value
        for label in ("input", "read", "hit", "输入", "读取", "命中")
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
