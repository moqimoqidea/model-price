"""Read public blog data and article bundles without executing JavaScript.

Some official blogs publish an HTML shell and compile their MDX prose into the
linked module. Only literal article metadata and JSX children are evidence;
runtime expressions and unrelated application bundles cannot supply claims.
Compiled blog cards and their route metadata retain the same literal-only rule.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from ..errors import SourceError
from ..literals import (
    STRING_LITERAL,
    code_matches as _code_matches,
    string_value as _literal,
    value_end as _value_end,
)
from ..text import clean_text
from .parsing import article_details, official_link, page_data, published_date

CHILD_PROPERTY = re.compile(r"\bchildren\s*:\s*")
DATE_PROPERTY = re.compile(r"\bdate\s*:\s*")


def _children(expression: str) -> str:
    expression = expression.strip()
    literal = STRING_LITERAL.fullmatch(expression)
    if literal:
        return _literal(literal[0])
    if expression.startswith("[") and expression.endswith("]"):
        parts = []
        position = 1
        while position < len(expression) - 1:
            end = _value_end(expression, position)
            parts.append(_children(expression[position:end]))
            position = end + 1
        return "".join(parts)
    position = 0
    while position < len(expression):
        literal = STRING_LITERAL.match(expression, position)
        if literal:
            position = literal.end()
            continue
        key = CHILD_PROPERTY.match(expression, position)
        if key:
            start = key.end()
            return _children(expression[start:_value_end(expression, start)])
        position += 1
    # Images and empty JSX elements have no prose children. A variable-valued
    # child does, but its rendered value is unavailable without executing code.
    if expression and not re.search(r"\.jsx[s]?\)\(", expression):
        raise SourceError("official article contains nonliteral prose children")
    return ""


def compiled_article_details(text: str) -> dict[str, Any]:
    """Extract the compiled MDX article, ignoring attributes, scripts, and images."""
    metadata: dict[str, str] = {}
    for date_key in _code_matches(text, DATE_PROPERTY):
        date = STRING_LITERAL.match(text, date_key.end())
        title_key = re.match(r"\s*,\s*title\s*:\s*", text[date.end():]) if date else None
        title = STRING_LITERAL.match(text, date.end() + title_key.end()) if date and title_key else None
        if date and title:
            metadata = {"date": _literal(date[0]), "title": _literal(title[0])}
    when = published_date(metadata.get("date", ""))
    if not metadata.get("title") or not when:
        raise SourceError("official article module omitted its title or publication date")
    paragraphs: list[str] = []
    block_end = 0
    pattern = re.compile(r"\.jsx[s]?\)\(\w+\.(?:p|li|blockquote)\s*,\s*\{")
    for match in _code_matches(text, pattern):
        if match.start() < block_end:
            continue
        start = match.end() - 1
        block_end = _value_end(text, start)
        value = clean_text(_children(text[start:block_end]))
        if value:
            paragraphs.append(value)
    if not paragraphs:
        raise SourceError("official article module published no literal article prose")
    return {
        "title": metadata["title"],
        "summary": paragraphs[0],
        "published_at": when,
        "paragraphs": paragraphs,
    }


def read_public_article(client: Any, url: str) -> dict[str, Any]:
    """Read the shell's explicit same-host article frame or compiled MDX module."""
    text = client.get_text(url)
    details = article_details(text)
    if details["title"] and details["paragraphs"]:
        return details
    page = page_data(text)
    frames = list(dict.fromkeys(
        linked for raw in page.frames if (linked := official_link(url, raw))
    ))
    if frames:
        if len(frames) != 1 or frames[0] == url:
            raise SourceError("official article shell did not link one readable article frame")
        framed = article_details(client.get_text(frames[0]))
        if not framed["title"] or not framed["paragraphs"]:
            raise SourceError("official article frame published no readable article prose")
        return framed
    modules = list(dict.fromkeys(
        linked
        for raw in page.module_scripts
        if (linked := official_link(url, raw))
    ))
    if len(modules) != 1:
        raise SourceError("official article shell did not link one readable article module")
    return compiled_article_details(client.get_text(modules[0]))


def _string_fields(text: str, names: tuple[str, ...]) -> dict[str, str]:
    """Reject computed values for the named metadata fields we use as evidence."""
    fields: dict[str, str] = {}
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\s*:\s*")
    for key in _code_matches(text, pattern):
        value = text[key.end():_value_end(text, key.end())].strip()
        if not STRING_LITERAL.fullmatch(value):
            raise SourceError("official blog metadata contains a nonliteral value")
        fields[key[1]] = _literal(value)
    return fields


def compiled_blog_entries(text: str, base_url: str) -> list[dict[str, Any]]:
    """Only literal cards in the compiled blogs array supply article links."""
    entries: dict[str, dict[str, Any]] = {}
    for array in _code_matches(text, re.compile(r"\bblogs\s*:\s*(?=\[)")):
        body = text[array.end():_value_end(text, array.end())].strip()
        position = 1
        while position < len(body) - 1:
            end = _value_end(body, position)
            card = body[position:end].strip()
            if not card.startswith("{") or not card.endswith("}"):
                raise SourceError("official blog index contains a nonliteral article card")
            fields = _string_fields(card, ("title", "link", "desc", "date"))
            url = official_link(base_url, fields.get("link") or "")
            if not fields.get("title") or not fields.get("link"):
                raise SourceError("official blog card omitted its title or article link")
            if url:
                entries[url] = {
                    "title": fields["title"], "url": url,
                    "summary": fields.get("desc") or "", "body": "",
                    "published_at": published_date(fields.get("date") or ""),
                }
            position = end + 1
    if not entries:
        raise SourceError("official blog index published no readable literal article cards")
    return list(entries.values())


def _compiled_page_dates(text: str, base_url: str) -> dict[str, str]:
    """A frontmatter date belongs only to its declared article route."""
    dates: dict[str, str] = {}
    # Rspress serializes each page's route and frontmatter in the same object.
    collection = re.search(r"\bpages:\s*(?=\[\{title:)", text)
    if not collection:
        return dates
    pages = text[collection.end():_value_end(text, collection.end())]
    pattern = re.compile(
        r'\{title:(?:' + STRING_LITERAL.pattern + r'),routePath:('
        + STRING_LITERAL.pattern + r'),lang:"en"'
    )
    for match in _code_matches(pages, pattern):
        url = official_link(base_url, _literal(match[1]))
        if not url:
            continue
        body = pages[match.start():_value_end(pages, match.start())]
        frontmatter = next(_code_matches(body, re.compile(r"\bfrontmatter\s*:\s*")), None)
        if not frontmatter:
            continue
        metadata = body[frontmatter.end():_value_end(body, frontmatter.end())]
        when = published_date(_string_fields(metadata, ("date",)).get("date") or "")
        if when:
            dates[url.rstrip("/")] = when
    return dates


def read_rspress_blog_entries(
    client: Any, url: str, *, asset_base: str, file_path: str
) -> list[dict[str, Any]]:
    """Resolve the declared page chunk through its linked route and hash manifests."""
    page = page_data(client.get_text(url))
    # The official page explicitly delegates to its vendor CDN. Keep that exact
    # deployment prefix, and follow no unrelated runtime or third-party assets.
    scripts = list(dict.fromkeys(
        target for raw in page.script_sources
        if (target := urljoin(url, raw)).startswith(asset_base + "static/js/")
    ))
    runtimes = [
        target for target in scripts
        if re.fullmatch(r"index\.[\w-]+\.js", urlsplit(target).path.rsplit("/", 1)[-1])
    ]
    manifests = [
        target for target in scripts
        if re.fullmatch(r"\d+\.[\w-]+\.js", urlsplit(target).path.rsplit("/", 1)[-1])
    ]
    if len(runtimes) != 1 or len(manifests) != 1:
        raise SourceError("official blog page did not link its route and asset manifests")
    manifest = client.get_text(manifests[0])
    route = next(_code_matches(manifest, re.compile(
        r'\bfilePath:"' + re.escape(file_path) + r'",preload:(.*?),lang:"en"'
    )), None)
    chunks = re.findall(r'\w+\.e\("(\d+)"\)', route[1]) if route else []
    if not chunks:
        raise SourceError("official blog route manifest omitted its page chunk")
    runtime = client.get_text(runtimes[0])
    hashes = re.search(
        r'\.u=\w+=>"static/js/async/"\+\w+\+"\."\+\(\{([^}]*)\}\)\[\w+\]\+"\.js"', runtime
    )
    deployment = re.search(r'\.p=(' + STRING_LITERAL.pattern + ')', runtime)
    digest = (
        re.search(r'(?<!\d)' + re.escape(chunks[-1]) + r':"([a-f0-9]+)"', hashes[1])
        if hashes else None
    )
    if not digest or not deployment or _literal(deployment[1]) != asset_base:
        raise SourceError("official blog asset manifest did not identify its page deployment")
    bundle_url = asset_base + f"static/js/async/{chunks[-1]}.{digest[1]}.js"
    entries = compiled_blog_entries(client.get_text(bundle_url), url)
    dates = _compiled_page_dates(manifest, url)
    for entry in entries:
        route_url = entry["url"].rstrip("/").removesuffix("/index.html")
        entry["published_at"] = dates.get(route_url) or entry["published_at"]
    return entries
