"""Model identity: normalisation, retired-name aliases, and family matching."""

from __future__ import annotations

import html
import re

from .text import clean_text

FOOTNOTE_MARKER_RE = re.compile(r"\s*[（(]\s*\d+\s*[)）]\s*$")

# Retired names that official docs still serve. Aliases only ever add matches;
# they never remove one, so family expansion for live names keeps working.
RETIRED_MODEL_ALIASES = {
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek/deepseek-v4-flash-vision-exp": "deepseek-flash",
    "deepseek-v4-flash-vision-exp": "deepseek-flash",
}


def strip_footnote_markers(value: str) -> str:
    """Drop trailing citation markers such as ``deepseek-flash(1)``.

    Vendor docs annotate model columns with footnote numbers. Those markers are
    presentation only, so they must never take part in model identity.
    """
    result = value
    while True:
        stripped = FOOTNOTE_MARKER_RE.sub("", result).strip()
        if stripped == result:
            return stripped
        result = stripped


def normalize_model(value: str) -> str:
    value = strip_footnote_markers(html.unescape(value))
    value = value.strip().lower().replace("_", "-")
    value = re.sub(r"\s+", "-", value)
    return re.sub(r"-+", "-", value)


def model_key(value: str) -> str:
    """Normalize a model name and follow documented retired-name aliases."""
    key = normalize_model(value)
    return RETIRED_MODEL_ALIASES.get(key, key)


def model_family(value: str) -> str:
    """Return a comparison key while preserving meaningful model versions."""
    value = clean_text(value).lower()
    value = re.sub(r"\b(?:原厂直供|正式版|预览版)\b", "", value)
    value = value.replace("原厂直供", "").replace("正式版", "").replace("预览版", "")
    return normalize_model(value).strip("-").rsplit("/", 1)[-1]


def _raw_model_matches(query: str, candidate: str, *, exact: bool = False) -> bool:
    query_key = normalize_model(query)
    candidate_key = normalize_model(candidate)
    if exact:
        return candidate_key == query_key
    family = model_family(candidate)
    query_family = model_family(query)
    keys = {candidate_key, family, family.rsplit("/", 1)[-1]}
    return any(
        key == query_key
        or key == query_family
        or key.startswith(f"{query_key}-")
        or key.startswith(f"{query_family}-")
        for key in keys
    )


def model_matches(query: str, candidate: str, *, exact: bool = False) -> bool:
    """Match a query against a candidate name, following retired-name aliases."""
    if _raw_model_matches(query, candidate, exact=exact):
        return True
    alias = RETIRED_MODEL_ALIASES.get(normalize_model(query))
    if not alias:
        return False
    return _raw_model_matches(alias, candidate, exact=exact)
