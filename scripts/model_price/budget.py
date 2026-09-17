"""Fit a rendered message into the character budget an instant messenger allows.

An instant messenger delivers a report as one message, so the message has to fit
before it is sent. Cutting it off at the limit is not an option: a price would
lose its decimal point and the reader would have no way to tell what was left
out. So the caller offers the same report at several densities, richest first,
and this picks the first that fits — and when not even the densest one fits, it
is still what gets sent, carrying its own note about what it left out.
"""

from __future__ import annotations

from typing import Callable, Iterable, TypeVar

# A DingTalk text message holds 5120 characters, which is the channel this tool
# writes for. A report is kept well inside that: the number of providers only
# grows, and one message has to stay one message.
DEFAULT_MAX_CHARS = 3000

Level = TypeVar("Level")


def fit_to_budget(
    levels: Iterable[Level], renders: Callable[[Level], str], limit: int
) -> str:
    """Return the richest of ``levels`` whose rendering fits within ``limit``.

    ``levels`` is ordered richest first, so the first rendering that fits is also
    the most detailed one that does. The densest level is returned even when it
    still exceeds the limit: it is the most honest form the caller can reach, and
    it says for itself which detail it had to leave out.
    """
    text = ""
    for level in levels:
        text = renders(level)
        if len(text) <= limit:
            return text
    return text
