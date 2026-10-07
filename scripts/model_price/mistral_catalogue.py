"""Read Mistral's public documentation data without executing its JavaScript.

The rendered catalogue omits API aliases and some lifecycle fields. Its own
versioned model-data bundle publishes those fields alongside the English prose;
all consumers share that capture instead of fetching one detail page per model.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from .errors import SourceError
from .models import normalize_model
from .parsing import headed_document_tables
from .text import clean_text

MISTRAL_MODELS_URL = "https://docs.mistral.ai/models"
MISTRAL_PRICING_URL = "https://docs.mistral.ai/inference/pricing"
MISTRAL_LIFECYCLE_URL = "https://docs.mistral.ai/inference/model-lifecycle.md"
MISTRAL_NEWS_URL = "https://mistral.ai/news/"

# The docs currently put their model-data module in this shared chunk. Resolve
# its deployment hash from the page, and reject a changed layout rather than
# continuing with stale IDs or following every unrelated application bundle.
MODEL_BUNDLE_PATH = re.compile(r"/_next/static/chunks/2894-[^/?]+\.js(?:\?[^\"]*)?")
PRICING_BUNDLE_PATH = re.compile(
    r"/_next/static/chunks/app/[^\"]*/inference/pricing/"
    r"page-[^\"]+\.js(?:\?[^\"]*)?"
)
STRING_LITERAL = r'"(?:\\.|[^"\\])*"'
MODEL_START = re.compile(r"\{\s*name\s*:\s*(" + STRING_LITERAL + r"),\s*describe\s*:")
LITERAL_TOKEN = re.compile(
    STRING_LITERAL + r"|void\s+0|![01]|[{}\[\]:,]|"
    r"-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|[A-Za-z_$][\w$]*"
)
PROPERTY_TOKEN = re.compile(STRING_LITERAL + r"|[{}\[\]()]|[A-Za-z_$][\w$]*")
MODEL_FIELDS = (
    "slug", "releaseDate", "status", "tags",
    "contextLength", "outputTokenLimit", "identifiers", "capabilities", "metadata",
)
IDENTITY_FIELDS = frozenset({"slug", "status", "identifiers", "tags"})


def literal_at(text: str, position: int = 0) -> Any:
    """Decode the data-only subset of bundle literals, refusing executable values."""
    tokens: list[str] = []
    depth = 0
    while position < len(text):
        while position < len(text) and text[position].isspace():
            position += 1
        match = LITERAL_TOKEN.match(text, position)
        if not match:
            raise SourceError("Mistral documentation contains an unsupported data literal")
        token = match.group()
        position = match.end()
        if token.startswith('"'):
            # Minifiers use JavaScript's hex escapes in otherwise JSON strings.
            token = re.sub(r"\\x([0-9a-fA-F]{2})", r"\\u00\1", token)
        elif token in ("!0", "!1"):
            token = "true" if token == "!0" else "false"
        elif token.startswith("void"):
            token = "null"
        elif token not in ("null", "true", "false") and re.match(r"[A-Za-z_$]", token):
            following = text[position:].lstrip()
            if not following.startswith(":"):
                raise SourceError("Mistral documentation data is not a literal")
            token = json.dumps(token)
        elif token.startswith("."):
            token = "0" + token
        elif token.startswith("-."):
            token = "-0" + token[1:]
        tokens.append(token)
        if token in ("{", "["):
            depth += 1
        elif token in ("}", "]"):
            depth -= 1
        if depth == 0:
            break
    try:
        return json.loads("".join(tokens), parse_float=str)
    except (ValueError, TypeError) as exc:
        raise SourceError("Mistral documentation data could not be decoded") from exc


def model_entries(bundle: str) -> list[dict[str, Any]]:
    """Read declared model properties, including generations unknown to the code."""
    starts = list(MODEL_START.finditer(bundle))
    entries = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(bundle)
        body = bundle[start.start():end]
        entry: dict[str, Any] = {"name": literal_at(start[1])}
        stack: list[str] = []
        for token in PROPERTY_TOKEN.finditer(body):
            value = token.group()
            if value.startswith('"'):
                continue
            if value in ("{", "[", "("):
                stack.append(value)
            elif value in ("}", "]", ")"):
                if not stack:
                    raise SourceError("Mistral model data has unbalanced properties")
                stack.pop()
                if not stack:
                    break
            elif stack == ["{"] and value in MODEL_FIELDS:
                colon = re.match(r"\s*:\s*", body[token.end():])
                if colon:
                    try:
                        entry[value] = literal_at(body, token.end() + colon.end())
                    except SourceError as exc:
                        if value in IDENTITY_FIELDS:
                            raise
                        # An unreadable introduction or notice property must
                        # not suppress independently readable price identities.
                        entry.setdefault("field_errors", {})[value] = str(exc)
        description = re.search(
            r"\bdescription:\w+\.text\(\s*(" + STRING_LITERAL + ")", body
        )
        if description:
            entry["description"] = literal_at(description[1])
        aliases = (entry.get("identifiers") or {}).get("apiNames")
        if (
            not isinstance(entry.get("slug"), str)
            or not isinstance(entry.get("status"), str)
            or not isinstance(aliases, list)
        ):
            raise SourceError("Mistral model data omitted its documented identity")
        if any(not isinstance(name, str) or not name for name in aliases):
            raise SourceError("Mistral model data published an unreadable API identifier")
        entries.append(entry)
    if not entries:
        raise SourceError("Mistral documentation published no readable model data")
    return entries


class ModelLinks(HTMLParser):
    """Keep only explicitly linked model slugs and script assets on the docs host."""

    def __init__(self, document: str) -> None:
        super().__init__()
        self.slugs: set[str] = set()
        self.scripts: list[str] = []
        self.feed(document)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        raw = attributes.get("src" if tag == "script" else "href") or ""
        url = urljoin(MISTRAL_MODELS_URL, raw)
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != "docs.mistral.ai":
            return
        if tag == "script" and attributes.get("src"):
            self.scripts.append(url)
        elif tag == "a" and re.fullmatch(r"/models/[^/]+", parts.path):
            self.slugs.add(parts.path.rsplit("/", 1)[-1])


def script_url(document: str, pattern: re.Pattern[str]) -> str:
    matches = [url for url in ModelLinks(document).scripts if pattern.search(url)]
    if len(matches) != 1:
        raise SourceError("Mistral documentation's public data bundle was not found")
    return matches[0]


def retirement_api_ids(document: str) -> set[str]:
    """Read the schedule's API column, including its separate header/body tables."""
    found: set[str] = set()
    model_column: int | None = None
    section: tuple[str, ...] = ()
    for headings, rows in headed_document_tables(document):
        if not rows or not re.search(r"deprecated|retired", " ".join(headings), re.I):
            continue
        headers = [clean_text(cell).lower() for cell in rows[0]]
        is_header = "api" in headers and "model" in headers
        if is_header:
            model_column = headers.index("api")
            section = tuple(headings)
        elif section != tuple(headings):
            model_column = None
        if model_column is None:
            continue
        for row in rows[1:] if is_header else rows:
            if len(row) > model_column and (value := clean_text(row[model_column])):
                found.add(normalize_model(value))
    return found


class MistralCatalogue:
    """Carry one verified model inventory across price, notice, and prose readers."""

    def __init__(self, client: Any) -> None:
        document = client.get_text(MISTRAL_MODELS_URL)
        self.bundle_url = script_url(document, MODEL_BUNDLE_PATH)
        self.entries = model_entries(client.get_text(self.bundle_url))
        self.slugs = ModelLinks(document).slugs
        self.retirement_ids = retirement_api_ids(document)
        if not self.slugs:
            raise SourceError("Mistral's model index published no model links")

    def linked_entries(self, document: str) -> list[dict[str, Any]]:
        slugs = ModelLinks(document).slugs
        indexed = {entry["slug"]: entry for entry in self.entries}
        missing = slugs - indexed.keys()
        if missing:
            raise SourceError(
                "Mistral's model links are absent from its data: "
                + ", ".join(sorted(missing))
            )
        return [entry for entry in self.entries if entry["slug"] in slugs]

    def current_entries(self) -> list[dict[str, Any]]:
        return [
            entry
            for entry in self.entries
            if entry["slug"] in self.slugs
            and entry.get("status") not in ("Deprecated", "Retired")
        ]

    def for_api(self, model_id: str) -> dict[str, Any] | None:
        key = normalize_model(model_id)
        matches = [
            entry
            for entry in self.entries
            if key in {
                normalize_model(name) for name in entry["identifiers"]["apiNames"]
            }
        ]
        current = [
            entry for entry in matches
            if entry.get("status") not in ("Deprecated", "Retired")
        ]
        candidates = current or matches
        if len(candidates) > 1:
            raise SourceError(
                "Mistral's documentation names multiple revisions for API identifier "
                + model_id
            )
        return candidates[0] if candidates else None


def read_catalogue(client: Any) -> MistralCatalogue:
    """Reuse parsed data only within the client's run, never across fresh scans."""
    catalogue = getattr(client, "_mistral_catalogue", None)
    if catalogue is None:
        catalogue = MistralCatalogue(client)
        client._mistral_catalogue = catalogue
    return catalogue


def model_url(entry: dict[str, Any]) -> str:
    return f"{MISTRAL_MODELS_URL}/{entry['slug']}"


def model_label(value: str) -> str:
    return clean_text(value.split("↗", 1)[0])
