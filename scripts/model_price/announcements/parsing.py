"""Read official release evidence without guessing API IDs or access from prices.

A release title names the model; the article supplies the claims. Benchmarks and
competitors mentioned in its body never become models discovered on this channel.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from ..descriptions.parsing import markdown_text, meta_tags
from ..models import normalize_model
from ..pricing import price_item
from ..parsing import date_value
from ..text import clean_text

RELEASE_WORDS = re.compile(
    r"introduc|announc|releas|launch|\bmeet\b|\bpreview\b|now available|"
    r"发布|推出|上线|开放|公测|首发",
    re.I,
)
ACCESS_WORDS = re.compile(
    r"availab|access|rolling out|rollout|public preview|"
    r"\byou can try (?:the )?(?:preview )?API\b|开源|开放|公测|内测|灰度|体验|即可使用",
    re.I,
)
PENDING_WORDS = re.compile(
    r"rolling out soon|coming soon|before (?:making|we release)|"
    r"will (?:be (?:available|released)|launch|release)|not (?:yet |currently )?(?:generally |publicly )?available|not yet|即将|尚未|后续开放",
    re.I,
)
LIMITED_WORDS = re.compile(
    r"(?:available|rolling out|access (?:is )?limited)\s+(?:only\s+)?to\s+"
    r"(?:a set of\s+)?(?:trusted|vetted|selected|approved)|"
    r"available only|only (?:available |through )|limited (?:access|preview)|"
    r"private preview|allowlist|available (?:in|through) early.access|"
    r"仅(?:对|向)|受邀|白名单|内测|定向开放|灰度",
    re.I,
)
PUBLIC_WORDS = re.compile(
    r"generally available|publicly available|available (?:today|now|to all|via|on)|now available|"
    r"(?:is|are) (?:now |currently )?available(?:\s|[,.])|"
    r"available to [^.!?]*\b(?:users|developers|subscribers)\b|"
    r"\byou can try (?:the )?(?:preview )?API\b|"
    r"public preview|公开(?:开放|公测)|正式上线|上线公测|即可使用",
    re.I,
)
PRICE_WORDS = re.compile(r"pric(?:e|ing)|\$\s*\d|USD\s*\d|定价|价格|折扣|优惠", re.I)
TOKEN_RATE = re.compile(
    r"(?P<currency>\$|USD|¥|￥|CNY)\s*(?P<amount>\d+(?:\.\d+)?)\s*"
    r"(?:per|/)\s*(?:million|1\s*M|百万)\s*"
    r"(?P<charge>(?:cached\s+)?input|output|输入|输出)\s*tokens?",
    re.I,
)
ACCESS_PRIORITY = {"unknown": 0, "pending": 1, "limited": 2, "public": 3}
NON_SERVICE_AVAILABILITY = re.compile(
    r"\b(?:weights?|data(?:sets?)?|scores?|scripts?|technical (?:details|report)|"
    r"project trace|repository)\b[^.!?]*\bavailab|"
    r"\bavailable\s+(?:data(?:sets?)?|scores?|scripts?|technical (?:details|report))\b|"
    r"model weights|模型权重",
    re.I,
)


class PageParser(HTMLParser):
    """Keep article paragraphs and index link titles out of scripts and navigation."""

    def __init__(self, *, exclude_navigation: bool = True) -> None:
        super().__init__()
        self.blocks: list[tuple[str, str]] = []
        self.links: list[tuple[str, str]] = []
        self.scripts: list[str] = []
        self.link_dates: dict[str, str | None] = {}
        self._ignored_tags = (
            ("script", "style", "nav", "footer")
            if exclude_navigation
            else ("script", "style")
        )
        self._skip = 0
        self._block: str | None = None
        self._words: list[str] = []
        self._link: str | None = None
        self._link_words: list[str] = []
        self._link_heading: list[str] = []
        self._script_words: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in self._ignored_tags:
            self._skip += 1
        if tag == "script" and attributes.get("type") == "application/ld+json":
            self._script_words = []
        if self._skip:
            return
        if tag == "a" and self._link is None:
            self._link = attributes.get("href") or ""
            self._link_words = []
            self._link_heading = []
        if tag in ("h1", "h2", "h3", "h4", "p", "li") and self._block is None:
            self._block = tag
            self._words = []

    def handle_data(self, data: str) -> None:
        if self._script_words is not None:
            self._script_words.append(data)
        if self._skip:
            return
        if self._block:
            self._words.append(data)
        if self._link is not None:
            self._link_words.append(data)
            if self._block and self._block.startswith("h"):
                self._link_heading.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script_words is not None:
            self.scripts.append("".join(self._script_words))
            self._script_words = None
        if tag in self._ignored_tags:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == self._block:
            value = clean_text(" ".join(self._words))
            if value:
                self.blocks.append((tag, value))
            self._block = None
        if tag == "a" and self._link is not None:
            title = clean_text(" ".join(self._link_heading or self._link_words))
            if title:
                self.links.append((self._link, title))
                self.link_dates[self._link] = date_value(
                    " ".join(self._link_words)
                ) or self.link_dates.get(self._link)
            self._link = None


def page_data(text: str, *, exclude_navigation: bool = True) -> PageParser:
    page = PageParser(exclude_navigation=exclude_navigation)
    page.feed(text.replace("\x00", ""))
    return page


def official_link(base: str, value: str) -> str | None:
    """A feed may link only to the same official host, including its www spelling."""
    url = urljoin(base, value)
    parsed = urlsplit(url)
    host = (parsed.hostname or "").removeprefix("www.")
    expected = (urlsplit(base).hostname or "").removeprefix("www.")
    return url if parsed.scheme == "https" and host == expected else None


def published_date(value: str | int | float) -> str | None:
    """Preserve ISO precision, converting an RSS date only when it gives a clock."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")
    value = value.strip()
    if not value:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(value).isoformat(timespec="seconds")
    except (ValueError, TypeError, OverflowError):
        return None


def feed_entries(text: str, base_url: str) -> list[dict[str, Any]]:
    """Read RSS and Atom, retaining full summaries rather than cutting feed content."""
    root = ET.fromstring(text)
    if root.tag.rsplit("}", 1)[-1] not in ("rss", "feed"):
        raise ValueError("official release feed did not return RSS or Atom")
    entries = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
            continue
        values = {child.tag.rsplit("}", 1)[-1]: child for child in element}

        def value(key: str) -> str:
            return "".join(values[key].itertext()) if key in values else ""

        link = values.get("link")
        raw_url = (link.get("href") or link.text or "") if link is not None else ""
        url = official_link(base_url, raw_url)
        if not url:
            continue
        summary = value("description") or value("summary") or value("content")
        entries.append(
            {
                "title": markdown_text(value("title")),
                "url": url,
                "summary": markdown_text(summary),
                "body": summary,
                "published_at": published_date(
                    value("pubDate") or value("published") or value("updated")
                ),
            }
        )
    if not entries:
        raise ValueError("official release feed published no readable entry")
    return entries


def model_names(text: str, brands: str) -> list[str]:
    """Read versioned model subjects, allowing new generations without an ID list."""
    # Lower-case narrative ends a spaced name. Dashed IDs and capitalized
    # qualifiers belong to the name; a title's release prose does not.
    pattern = re.compile(
        rf"(?<![\w-])(?:{brands})(?:[- ](?:[A-Z][a-zA-Z]*))*[- ]?"
        rf"(?:[A-Za-z]*\d[\w.-]*)(?:[- ](?:[A-Z][\w.-]*|preview|experimental))*"
    )
    names = []
    for match in pattern.finditer(text.replace("‑", "-")):
        name = re.split(
            r"\s+(?:API|Tech|Technical|Tops|NEW|Officially|Becomes|A|The)\b",
            match.group(),
        )[0]
        if name and normalize_model(name) not in {
            normalize_model(item) for item in names
        }:
            names.append(name)
    return names


def publication_names(title: str, summary: str, brands: str) -> list[str]:
    """Read explicitly coordinated variants when a title abbreviates the brand."""
    names = model_names(title, brands)
    if names:
        prefix = re.split(r"[A-Za-z]*\d", names[0], maxsplit=1)[0].rstrip(" -")
        expanded = re.sub(
            r"(\band\s+|、|和)(?=[A-Za-z]*\d)",
            lambda match: match.group() + prefix + " ",
            title,
        )
        names = model_names(expanded, brands)
    detailed = model_names(summary, brands)
    if len(names) == 1 and re.search(r"\d$", names[0]):
        variants = [
            name
            for name in detailed
            if normalize_model(name).startswith(normalize_model(names[0]) + "-")
        ]
        if variants:
            return variants
    for name in detailed:
        # A comparison to an older model is not the announcement of that model.
        prefix, _, suffix = normalize_model(name).rpartition("-")
        coordinated_title = any(
            normalize_model(subject).rpartition("-")[0] == prefix for subject in names
        ) and re.search(rf"(?:\band\s+|、|和){re.escape(suffix)}\b", title, re.I)
        if name not in names and (
            coordinated_title
            or any(
                re.search(
                    rf"{re.escape(subject)}\s*(?:\band\s+|、|和){re.escape(name)}",
                    summary,
                )
                for subject in names
            )
        ):
            names.append(name)
    return names


def article_details(text: str) -> dict[str, Any]:
    """Use an article's own structured metadata and paragraphs, excluding link cards."""
    page = page_data(text)
    metadata = meta_tags(text)
    article: dict[str, Any] = {}
    for script in page.scripts:
        try:
            data = json.loads(script)
        except (ValueError, TypeError):
            continue
        objects = data if isinstance(data, list) else [data]
        for item in objects:
            if isinstance(item, dict):
                objects.extend(item.get("@graph") or [])
                if item.get("@type") in ("NewsArticle", "BlogPosting", "Article"):
                    article = item
    title = article.get("headline") or next(
        (text for tag, text in page.blocks if tag == "h1"), ""
    )
    paragraphs = [
        text
        for tag, text in page.blocks
        if tag in ("p", "li") and not re.search(r"^The post .* appeared first on", text)
    ]
    summary = (
        article.get("description")
        or metadata.get("description")
        or metadata.get("og:description")
    )
    return {
        "title": title,
        "summary": markdown_text(summary or (paragraphs[0] if paragraphs else "")),
        "published_at": published_date(
            str(
                article.get("datePublished")
                or metadata.get("article:published_time")
                or ""
            )
        ),
        "paragraphs": paragraphs,
    }


def mentions_model(text: str, name: str) -> bool:
    return bool(_model_mentions(text, name))


def _model_mentions(text: str, name: str) -> list[tuple[int, int]]:
    candidates = [normalize_model(name)]
    # Articles abbreviate their own names ("Mythos 5.1") in adjacent clauses.
    if " " in name:
        candidates.append(normalize_model(name.split(" ", 1)[1]))
    return [
        match.span()
        for candidate in candidates
        for match in re.finditer(
            r"(?<![\w.])"
            # A typeset hyphen and its nonbreaking spelling identify the same
            # literal model, but a suffixed variant still owns its own evidence.
            + r"[-_\s\u2010\u2011]+".join(
                re.escape(part) for part in re.split(r"[-\u2010\u2011]", candidate)
            )
            + r"(?!\w|\.\w|[-_\u2010\u2011]\w)",
            text,
            re.I,
        )
    ]


def referenced_models(text: str, names: list[str]) -> list[str]:
    """A longer named variant owns its mention, never the shorter base model."""
    spans = [
        (start, end, name)
        for name in names
        for start, end in _model_mentions(text, name)
    ]
    return [
        name
        for name in names
        if any(
            subject == name
            and not any(
                other_start <= start
                and end <= other_end
                and other_end - other_start > end - start
                for other_start, other_end, _ in spans
            )
            for start, end, subject in spans
        )
    ]


def access_state(clause: str) -> str:
    """A public API and another surface's private preview can coexist."""
    if not ACCESS_WORDS.search(clause) or NON_SERVICE_AVAILABILITY.search(clause):
        return "unknown"
    if PENDING_WORDS.search(clause):
        return "pending"
    if re.search(
        r"generally available|publicly available|available to all|available on all platforms",
        clause,
        re.I,
    ):
        return "public"
    if LIMITED_WORDS.search(clause):
        return "limited"
    return "public" if PUBLIC_WORDS.search(clause) else "unknown"


def release_facts(name: str, names: list[str], paragraphs: list[str]) -> dict[str, Any]:
    """Scope access and price claims to one model, keeping the official evidence."""
    access = {
        "status": "unknown",
        "developers": "unknown",
        "consumers": "unknown",
        "statements": [],
    }
    pricing_notes: list[str] = []
    offers = []
    for paragraph in paragraphs:
        scope: list[str] = []
        clauses = re.split(
            r"(?<=[.!?])\s+(?=[A-Z])|(?<=[。！？])\s*|\s+(?:while|whereas)\s+|[；;]|注\d+[:：]",
            paragraph,
        )
        for clause in clauses:
            mentioned = referenced_models(clause, names)
            if mentioned:
                scope = mentioned
            if not mentioned:
                # A single-model article can still compare other generations or
                # competitors. Their explicitly named rates and access are not
                # anaphoric claims about the article's subject.
                brand = re.match(r"[A-Za-z]+", name)
                namespace = (
                    model_names(clause, re.escape(brand.group())) if brand else []
                )
                foreign = [
                    subject
                    for subject in model_names(clause, r"[A-Z][A-Za-z]+")
                    if re.search(r"\d\.\d|[-_]\d", subject)
                ]
                if namespace or foreign:
                    scope = []
                    continue
            if name not in (mentioned or scope or (names if len(names) == 1 else [])):
                continue
            state = access_state(clause)
            if state != "unknown":
                if (
                    not mentioned
                    and not scope
                    and not re.search(
                        r"\b(?:model|API|developers?|consumers?|subscribers?)\b|开发者|普通用户|模型",
                        clause,
                        re.I,
                    )
                ):
                    state = "unknown"
                if state != "unknown":
                    access["statements"].append(clean_text(clause))
                    if ACCESS_PRIORITY[state] >= ACCESS_PRIORITY[access["status"]]:
                        access["status"] = state
                    for audience, words in (
                        ("developers", r"developers?|API|开发者|开发人员"),
                        ("consumers", r"consumers?|subscribers?|公众|普通用户|消费者"),
                    ):
                        if re.search(words, clause, re.I):
                            access[audience] = state
            if PRICE_WORDS.search(clause):
                pricing_notes.append(clean_text(clause))
                prices = []
                for match in TOKEN_RATE.finditer(clause):
                    charge = match["charge"].lower()
                    kind = (
                        "cache_hit"
                        if "cached" in charge
                        else "input" if charge in ("input", "输入") else "output"
                    )
                    currency = (
                        "USD" if match["currency"].upper() in ("$", "USD") else "CNY"
                    )
                    prices.append(
                        price_item(
                            kind,
                            {
                                "input": "输入",
                                "output": "输出",
                                "cache_hit": "缓存输入",
                            }[kind],
                            match["amount"],
                            f"{currency}_per_million_tokens",
                            display=match.group(),
                        )
                    )
                if prices:
                    offers.append(
                        {
                            "name": "announcement",
                            "conditions": {"published_terms": clean_text(clause)},
                            "prices": prices,
                        }
                    )
    access["statements"] = list(dict.fromkeys(access["statements"]))
    return {
        "access": access,
        "announced_offers": offers,
        "pricing_notes": list(dict.fromkeys(pricing_notes)),
    }
