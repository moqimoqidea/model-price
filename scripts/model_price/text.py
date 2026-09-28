"""Normalisation helpers for text scraped out of vendor documents."""

from __future__ import annotations

import html
import re

MARKDOWN_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!~|>$])")

# Vendors stack several values in one cell, one per line ("ERNIE-5.0<br>ERNIE-5.0-
# Thinking-Preview"). The first is the model itself and the rest are variants the
# same price covers, so the two halves have to stay separable. Both the rendered
# HTML and the Markdown these documents are served as spell the break this way.
CELL_BREAK_RE = re.compile(r"<br\s*/?>", re.I)


def first_cell_line(value: str) -> str:
    """Return the first of the values a vendor stacked in one cell."""
    return clean_text(CELL_BREAK_RE.split(value, maxsplit=1)[0])


def clean_text(value: str) -> str:
    """Strip markup and collapse whitespace in a scraped cell or label."""
    value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    value = value.replace("**", "").replace("~~", "")
    value = unescape_markdown(value)
    return re.sub(r"\s+", " ", value).strip()


ZERO_WIDTH_SPACE = "\u200b"


def clean_zero_width_text(value: str) -> str:
    """Normalise text a documentation site writes with a zero-width space in it.

    A generated permalink anchor puts one beside every heading. It comes off before
    whitespace is collapsed: collapsing first leaves behind the space the mark was
    separating from, and a caller matching a whole line then sees a trailing blank
    the published text does not have.
    """
    return clean_text(value.replace(ZERO_WIDTH_SPACE, ""))


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
