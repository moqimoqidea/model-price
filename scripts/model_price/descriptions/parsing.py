"""Small parsers shared by official model-description sources."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any

from ..models import normalize_model
from ..parsing import markdown_link_text
from ..text import CELL_BREAK_RE, clean_text

FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*\n", re.S)
MARKDOWN_LINK_RE = re.compile(r"\[([^]]+)]\(([^)]+)\)")
MARKDOWN_DECORATION_RE = re.compile(r"[*~`]+")
HTML_TAG_RE = re.compile(r"<[^>]+>")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)
META_RE = re.compile(
    r'<meta\s+[^>]*(?:name|property)=["\'](?P<name>[^"\']+)["\'][^>]*'
    r'content=["\'](?P<content>[^"\']*)["\'][^>]*>',
    re.I,
)
META_RE_REVERSED = re.compile(
    r'<meta\s+[^>]*content=["\'](?P<content>[^"\']*)["\'][^>]*'
    r'(?:name|property)=["\'](?P<name>[^"\']+)["\'][^>]*>',
    re.I,
)


def markdown_text(value: str) -> str:
    """Reduce short Markdown/MDX fragments to readable plain text."""
    value = CELL_BREAK_RE.sub("；", value)
    value = MARKDOWN_LINK_RE.sub(lambda match: match.group(1), value)
    value = HTML_TAG_RE.sub(" ", value)
    value = MARKDOWN_DECORATION_RE.sub("", value)
    return clean_text(html.unescape(value)).strip(" |-:")


def frontmatter(text: str) -> dict[str, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    values: dict[str, str] = {}
    for line in match.group("body").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"\'')
    return values


def section(text: str, heading: str) -> str:
    """Return one Markdown section body, matched case-insensitively."""
    headings = list(HEADING_RE.finditer(text))
    for index, match in enumerate(headings):
        if markdown_text(match.group(2)).lower() != heading.lower():
            continue
        level = len(match.group(1))
        end = len(text)
        for following in headings[index + 1 :]:
            if len(following.group(1)) <= level:
                end = following.start()
                break
        return text[match.end() : end].strip()
    return ""


def prose_paragraphs(text: str) -> list[str]:
    """Return prose paragraphs, excluding headings, tables, code, and index notes."""
    paragraphs = []
    in_fence = False
    for block in re.split(r"\n\s*\n", text):
        value = block.strip()
        if value.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not value:
            continue
        if value.startswith(("#", "|", "<", "[", "- ", "* ")):
            continue
        plain = markdown_text(value.lstrip("> "))
        if not plain or "documentation index" in plain.lower():
            continue
        paragraphs.append(plain)
    return paragraphs


def markdown_summary(text: str) -> str:
    """Pick the page's own model summary, preferring its Overview section."""
    overview = prose_paragraphs(section(text, "Overview"))
    if overview:
        return overview[0]
    blockquotes = [
        markdown_text(line[1:])
        for line in text.splitlines()
        if line.startswith(">") and "documentation index" not in line.lower()
    ]
    if blockquotes:
        return blockquotes[0]
    paragraphs = prose_paragraphs(FRONTMATTER_RE.sub("", text, count=1))
    if paragraphs:
        return paragraphs[0]
    return frontmatter(text).get("description", "")


def markdown_capabilities(text: str) -> list[str]:
    """Read explicit feature lists or capability tables without inference."""
    features = section(text, "Supported features")
    if features:
        return [
            markdown_text(line[2:])
            for line in features.splitlines()
            if line.startswith("- ") and markdown_text(line[2:])
        ]
    capabilities = section(text, "Capabilities")
    result = []
    for line in capabilities.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [markdown_text(cell) for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] not in {"Feature", "Property", "---"}:
            if not set(cells[0]) <= {"-", ":"}:
                result.append(f"{cells[0]}：{cells[1]}")
    return result


def markdown_specifications(text: str) -> dict[str, str]:
    """Keep concise, explicitly published model limits."""
    result: dict[str, str] = {}
    labels = {
        "context window": "context_window",
        "input token limit": "input_token_limit",
        "output token limit": "output_token_limit",
        "max output": "max_output",
        "knowledge cutoff": "knowledge_cutoff",
        "input modalities": "input_modalities",
        "output modalities": "output_modalities",
    }
    for line in text.splitlines():
        bullet = re.match(r"-\s*([^:]+):\s*(.+)", line)
        if bullet:
            label = markdown_text(bullet.group(1)).lower()
            key = labels.get(label)
            if key:
                result[key] = markdown_text(bullet.group(2))
    for label, key in labels.items():
        pattern = re.compile(
            rf"\|\s*{re.escape(label)}\s*\|\s*(.*?)\s*\|", re.I
        )
        match = pattern.search(text)
        if match:
            result.setdefault(key, markdown_text(match.group(1)))
    return result


def lifecycle_from_text(text: str) -> str:
    normalized = markdown_text(text).lower()
    if any(word in normalized for word in ("已下线", "retired", "deprecated")):
        return "retired"
    if any(
        word in normalized for word in ("preview", "预览", "experimental", "实验")
    ):
        return "preview"
    if any(word in normalized for word in ("legacy", "历史模型", "旧版")):
        return "legacy"
    if any(word in normalized for word in ("active", "stable", "latest", "正式")):
        return "active"
    return "unknown"


def meta_tags(page: str) -> dict[str, str]:
    """Read title/description metadata from a rendered official page."""
    tags = {
        match.group("name").lower(): html.unescape(match.group("content"))
        for pattern in (META_RE, META_RE_REVERSED)
        for match in pattern.finditer(page.replace("\x00", ""))
    }
    return tags


def model_link(cell: str) -> tuple[str, str]:
    """Return a table cell's model label and its first link, if present."""
    match = MARKDOWN_LINK_RE.search(cell)
    return markdown_text(markdown_link_text(cell)), match.group(2) if match else ""


class TextCollector(HTMLParser):
    """Collect visible text from one short rendered document fragment."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = clean_text(data)
        if value:
            self.parts.append(value)


def visible_text(page: str) -> str:
    parser = TextCollector()
    parser.feed(page.replace("\x00", ""))
    return clean_text(" ".join(parser.parts))


def model_mentioned(text: str, *names: str) -> bool:
    haystack = normalize_model(markdown_text(text))
    return any(
        normalized and normalized in haystack
        for normalized in (normalize_model(name) for name in names)
    )
