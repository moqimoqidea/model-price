"""DeepSeek's official update log, read once for every consumer of its dates.

The vendor dates one entry per release on a single page, newest first. Reading it
here keeps three readers off three separate routes to the same dates — the price
adapter's official update stamp, the retirement audit's notice evidence, and the
model introductions — and off guessing a date-shaped news URL per release, which
only ever covered the releases someone had remembered to add.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any, NamedTuple

from .errors import SourceError
from .models import canonical_model
from .text import clean_zero_width_text

DEEPSEEK_UPDATES_URL = "https://api-docs.deepseek.com/zh-cn/updates/"
# A route that does not exist answers HTTP 200 with the documentation home page, so
# a response counts only when the page says which document it is. The site serves
# the log with and without a trailing slash, and names only one as canonical.
DEEPSEEK_UPDATES_MARKS = tuple(
    f'rel="canonical" href="{url}"'
    for url in (DEEPSEEK_UPDATES_URL, DEEPSEEK_UPDATES_URL.rstrip("/"))
)

# An entry opens with its own date, then the release's title, then its prose.
ENTRY_DATE_RE = re.compile(r"^时间\s*[：:]\s*(?P<date>20\d\d-\d{2}-\d{2})$")
# The title states what the vendor did ("DeepSeek-V4.1-Flash 发布"); only the part
# before that verb names the model.
TITLE_VERB_RE = re.compile(r"\s*(?:发布|更新|上线|公测|开放|版本更新)\s*$")
# A model id is a dotted or dashed token. Release prose also carries benchmark
# names and parameter counts, so only a title is ever read for one.
MODEL_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)+")
DATED_BUILD_RE = re.compile(r"\d{4,8}")

# The log says a model shipped. It never says what became of it afterwards: the
# 2026-08-21 vision experiment was withdrawn eleven days after it was announced,
# and the 2026-04-24 preview is still a preview. Those later states are therefore
# curated by the date that announced the model, not inferred from a release verb.
ANNOUNCED_LIFECYCLE = {
    "2026-08-21": "retired",
    "2026-04-24": "preview",
}
DEFAULT_LIFECYCLE = "active"


class UpdateEntry(NamedTuple):
    """One dated entry of the official log, as the vendor published it."""

    published_at: str
    title: str
    summary: str
    body: str


def is_updates_page(page: str) -> bool:
    """Say whether a response is the update log rather than another document."""
    return any(mark in page for mark in DEEPSEEK_UPDATES_MARKS)


def update_entries(document: str) -> list[UpdateEntry]:
    """Read every dated entry, in the newest-first order the log publishes them.

    A date heading opens a section and each title under it is one entry of that
    date, so a day that announced two models yields two entries rather than one
    whose summary belongs to only one of them.
    """
    entries: list[UpdateEntry] = []
    published_at = ""
    for tag, text in _log_blocks(document):
        if tag == "h2":
            match = ENTRY_DATE_RE.match(text)
            published_at = match["date"] if match else ""
            continue
        if not published_at:
            continue
        if tag == "h3":
            entries.append(UpdateEntry(published_at, text, "", ""))
            continue
        if not entries:
            continue
        entry = entries[-1]
        entries[-1] = entry._replace(
            summary=entry.summary or text, body=f"{entry.body} {text}".strip()
        )
    return entries


def read_updates(client: Any) -> list[UpdateEntry]:
    """Fetch the log and read it, refusing any other document it answers with."""
    page = client.get_text(DEEPSEEK_UPDATES_URL)
    if not is_updates_page(page):
        raise SourceError("DeepSeek update log answered with another document")
    entries = update_entries(page)
    if not entries:
        raise SourceError("DeepSeek update log published no readable entry")
    return entries


def latest_published_at(entries: list[UpdateEntry]) -> str | None:
    """The newest date the log publishes, or ``None`` when it dates nothing."""
    return max((entry.published_at for entry in entries), default="") or None


def entry_model_label(entry: UpdateEntry) -> str:
    """The model name an entry's own title carries, without its release wording."""
    return TITLE_VERB_RE.sub("", entry.title).strip() or entry.title


def entry_lifecycle(entry: UpdateEntry) -> str:
    """The state this entry's model is in, curated where the log cannot say."""
    return ANNOUNCED_LIFECYCLE.get(entry.published_at, DEFAULT_LIFECYCLE)


def entry_for_model(
    entries: list[UpdateEntry], model_id: str
) -> UpdateEntry | None:
    """The newest entry whose own title names this model, if the log has one."""
    for entry in entries:
        if any(
            _names_model(name, model_id)
            for name in MODEL_NAME_RE.findall(entry_model_label(entry))
        ):
            return entry
    return None


def _names_model(entry_name: str, model_id: str) -> bool:
    """Say whether a title's model name is the model being asked about.

    The vendor spells a model its own way in a title, so the documented spelling
    aliases are followed. A dated build of the named model is the same model with
    its build pinned, and the match stays one-way: a live model never absorbs the
    entry of a superseded generation, which is how a Vision-Exp article would
    otherwise be attached to a separately priced preview build.
    """
    name = canonical_model(entry_name)
    key = canonical_model(model_id)
    if not name or not key:
        return False
    if name == key:
        return True
    tail = key[len(name) :]
    return key.startswith(f"{name}-") and bool(DATED_BUILD_RE.fullmatch(tail))


class _LogBlockParser(HTMLParser):
    """Collect the log's headings and paragraphs in the order they are published."""

    BLOCK_TAGS = ("h2", "h3", "p")

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[tuple[str, str]] = []
        self._tag: str | None = None
        self._words: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.BLOCK_TAGS and self._tag is None:
            self._tag = tag
            self._words = []

    def handle_data(self, data: str) -> None:
        if self._tag is not None:
            self._words.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != self._tag:
            return
        # A heading carries the permalink anchor's zero-width space with it.
        text = clean_zero_width_text(" ".join(self._words))
        if text:
            self.blocks.append((tag, text))
        self._tag = None
        self._words = []


def _log_blocks(document: str) -> list[tuple[str, str]]:
    parser = _LogBlockParser()
    parser.feed(document.replace("\x00", ""))
    return parser.blocks
