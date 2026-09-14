"""Normalisation helpers for text scraped out of vendor documents."""

from __future__ import annotations

import html
import re


def clean_text(value: str) -> str:
    """Strip markup and collapse whitespace in a scraped cell or label."""
    value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    value = value.replace("**", "").replace("~~", "")
    return re.sub(r"\s+", " ", value).strip()


def numeric_values(value: str) -> list[str]:
    """Return every bare number in ``value``, ignoring digits inside words."""
    return re.findall(r"(?<![\w.])\d+(?:\.\d+)?", clean_text(value))
