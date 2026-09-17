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

# Labels a vendor files the *live* model under, spelled differently from the
# first-party id ("DeepSeek-V4.1-Flash" is what Aliyun and Ark call the model
# DeepSeek serves as ``deepseek-flash``). These are matched on both sides, so one
# query reaches every platform serving that model. Unlike the retired names above
# they do not stand for a superseded generation, so they must never be used to
# pull an older model's prices into a live model's comparison.
CURRENT_MODEL_ALIASES = {
    "deepseek-v4.1-flash": "deepseek-flash",
    "deepseek-v4-1-flash": "deepseek-flash",
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


# Vendors annotate a label with something that is not part of the model's
# identity: a context tier, a retirement notice, the date a price applies from.
# Keeping the two halves apart lets the note survive as a condition instead of
# leaking into the model id, where it would stop the model being findable. This
# is the only reader of such an annotation: an adapter that wants just the name
# or just the note asks for that half rather than matching the punctuation itself.
TRAILING_PARENTHETICAL_RE = re.compile(r"[（(]([^（()）]*)[）)]\s*$")


def split_trailing_parenthetical(value: str) -> tuple[str, str]:
    """Separate a label's trailing parenthetical from the name it annotates."""
    text = clean_text(value)
    match = TRAILING_PARENTHETICAL_RE.search(text)
    if not match:
        return text, ""
    return text[: match.start()].strip(), clean_text(match.group(1))


def without_trailing_parenthetical(value: str) -> str:
    """Return a label's name once its trailing annotation is removed."""
    return split_trailing_parenthetical(value)[0]


def trailing_parenthetical(value: str) -> str:
    """Return the note a label's trailing annotation carries, or ``""``."""
    return split_trailing_parenthetical(value)[1]


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
    """Match a query against a candidate name, following documented aliases."""
    if _raw_model_matches(query, candidate, exact=exact):
        return True
    alias = RETIRED_MODEL_ALIASES.get(normalize_model(query))
    if alias and _raw_model_matches(alias, candidate, exact=exact):
        return True
    # Two ids that differ only by vendor spelling are the same model, so an
    # ``--exact`` lookup keeps insisting on the official id it was given.
    if exact:
        return False
    current = CURRENT_MODEL_ALIASES.get(normalize_model(query))
    if current and _raw_model_matches(current, candidate):
        return True
    candidate_current = CURRENT_MODEL_ALIASES.get(normalize_model(candidate))
    if candidate_current and _raw_model_matches(query, candidate_current):
        return True
    return False
