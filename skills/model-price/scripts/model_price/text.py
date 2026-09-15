"""Normalisation helpers for text scraped out of vendor documents."""

from __future__ import annotations

import html
import re

MARKDOWN_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!~|>$])")


def clean_text(value: str) -> str:
    """Strip markup and collapse whitespace in a scraped cell or label."""
    value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    value = value.replace("**", "").replace("~~", "")
    value = unescape_markdown(value)
    return re.sub(r"\s+", " ", value).strip()


def unescape_markdown(value: str) -> str:
    """Remove the backslashes Markdown escapes punctuation with.

    Vendors run their docs through an exporter that escapes punctuation, so a
    model named ``deepseek-v4.1-flash`` reaches us as ``deepseek\\-v4.1\\-flash``.
    The escapes are presentation, not identity: they have to come off before a
    name is compared with a rule that names it in prose.
    """
    if not value:
        return ""
    return MARKDOWN_ESCAPE_RE.sub(r"\1", value)


def numeric_values(value: str) -> list[str]:
    """Return every bare number in ``value``, ignoring digits inside words."""
    return re.findall(r"(?<![\w.])\d+(?:\.\d+)?", clean_text(value))
