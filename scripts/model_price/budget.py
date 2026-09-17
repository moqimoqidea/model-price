"""Whether a message fits the character budget of the channel that carries it.

An instant messenger delivers a report as one message, so the message has to fit
before it is sent. Two things this module deliberately does not do are cut the
text and choose a shorter one: a report shortened by dropping a block, or by
stopping mid-amount, is a report that lies about what it covers, and the reader
has no way to tell what was left out.

So fitting is someone else's job. This module measures the message, reports how
much it overruns, and says that the message is not ready to send — the tool takes
no credentials and has no model to ask, so the summarization that brings a report
under its limit is done by whoever writes the message out.
"""

from __future__ import annotations

# A DingTalk text message holds 5120 characters, which is the channel this tool
# writes for. A report is kept well inside that: the number of providers only
# grows, and one message has to stay one message.
DEFAULT_MAX_CHARS = 3000


def overage(text: str, limit: int) -> int:
    """How many characters ``text`` must shed to fit ``limit``, 0 when it fits."""
    return max(0, len(text) - limit)


def fits_within(text: str, limit: int) -> bool:
    """Whether a message may be sent as it stands."""
    return overage(text, limit) == 0
