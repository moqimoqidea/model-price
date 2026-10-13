"""Discover models from each host's own releases and capability inventories.

Sources declare transport and model-subject grammar, never a list of generations.
All release facts remain on their publishing channel; a creator's announcement
does not list the same model on a reseller's platform.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit, urlunsplit

from ..deepseek_updates import DEEPSEEK_UPDATES_URL, entry_model_label, read_updates
from ..descriptions.sources import (
    KlingDescriptionSource,
    XiaomiDescriptionSource,
    ZhipuDescriptionSource,
)
from ..descriptions.parsing import markdown_summary, prose_paragraphs
from ..errors import SourceError
from ..mistral_catalogue import MISTRAL_NEWS_URL, read_catalogue as read_mistral_catalogue
from ..parsing import date_value, models_named_in
from .documents import read_public_article, read_rspress_blog_entries
from .parsing import (
    RELEASE_WORDS,
    article_details,
    article_list_entries,
    embedded_blog_entries,
    feed_entries,
    markdown_text,
    model_names,
    official_link,
    page_data,
    published_date,
    release_facts,
    mentions_model,
    publication_names,
    release_note_entries,
)

# News feeds include years of old releases and customer stories. Discovery scans
# recent publications, while retained history and current capability inventories
# have no age cutoff. This is source scope, not a message length policy.
NEWS_WINDOW_DAYS = 90
GOOGLE_BLOG_URL = (
    "https://blog.google/innovation-and-ai/models-and-research/gemini-models/"
)
TENCENT_BLOG_URL = "https://hunyuan.tencent.com/"
TENCENT_BLOG_API = "https://api.hunyuan.tencent.com/api/blog/publicList"
QWEN_BLOG_URL = "https://qwen.ai/"
QWEN_BLOG_API = "https://qwen.ai/api/v2/article/retrieval?type=qwen_ai&language=en-US"
SEED_MODELS_URL = "https://seed.bytedance.com/en/blog"
SEED_BLOG_API = "https://seed.bytedance.com/api/get_article_list_v2"
ZHIPU_UPDATES_URL = "https://docs.z.ai/release-notes/new-released.md"
# Z.ai publishes these articles without a readable blog index or blog sitemap.
# Register verified article URLs as evidence, never their model names in a parser.
ZHIPU_BLOG_URL = "https://z.ai/blog/glm-legal"
ANT_BLOG_URL = "https://developer.ant-ling.com/en/blogs/"
XIAOMI_UPDATES_URL = "https://mimo.mi.com/docs/en-US/updates/model"
XIAOMI_BLOG_URL = "https://mimo.xiaomi.com/"
XIAOMI_BLOG_ASSETS = "https://cdn.cnbj1.fds.api.mi-img.com/aife/mimo-blog-fe/doc_build/"
KIMI_BLOG_URL = "https://www.kimi.ai/blog/"


@dataclass(frozen=True)
class NewsSource:
    """The official index, its transport, and the product families its titles name."""

    url: str
    index_url: str
    brands: str
    transport: str = "rss"
    required_context: str = ""
    article_path: str = ""
    article_trailing_slash: bool = False
    summary_from_body: bool = False
    subject_inventory: Callable[[Any], list[str]] | None = None
    entry_reader: Callable[[Any], list[dict[str, Any]]] | None = None
    article_reader: Callable[[Any, str], dict[str, Any]] | None = None
    requires_release_signal: bool = False


def mistral_subject_inventory(client: Any) -> list[str]:
    """Unversioned release subjects come from official data, never a name list."""
    return [entry["name"] for entry in read_mistral_catalogue(client).entries]


def seed_subject_inventory(client: Any) -> list[str]:
    return [item["labelEn"] for item in _seed_models(client)]


def xiaomi_news_entries(client: Any) -> list[dict[str, Any]]:
    """Use literal homepage blog cards, including those hidden behind More."""
    entries = read_rspress_blog_entries(
        client, XIAOMI_BLOG_URL, asset_base=XIAOMI_BLOG_ASSETS,
        file_path="en/index.mdx",
    )
    for entry in entries:
        entry["subject_title"] = re.sub(r"^Xiaomi\s+", "", entry["title"])
        # MiMo's gateway redirects /index.html through /index to the same
        # directory page. Read that verified canonical path in one request.
        parts = urlsplit(entry["url"])
        if parts.path.endswith("/index.html"):
            entry["url"] = urlunsplit(parts._replace(path=parts.path.removesuffix("/index.html")))
    return entries


NEWS_SOURCES = {
    "google": NewsSource(
        GOOGLE_BLOG_URL, f"{GOOGLE_BLOG_URL}rss/", r"Gemini|Gemma|Imagen|Veo|Lyria"
    ),
    "openai": NewsSource(
        "https://openai.com/news/",
        "https://openai.com/news/rss.xml",
        r"GPT|gpt|Sora|sora|o(?=\d)",
        article_trailing_slash=True,
    ),
    "baidu": NewsSource(
        "https://ernie.baidu.com/blog/",
        "https://ernie.baidu.com/blog/index.xml",
        r"ERNIE|PaddleOCR",
    ),
    "anthropic": NewsSource(
        "https://www.anthropic.com/news",
        "https://www.anthropic.com/news",
        r"Claude",
        "html",
    ),
    "xai": NewsSource("https://x.ai/news", "https://x.ai/news", r"Grok|grok", "html"),
    "mistral": NewsSource(
        MISTRAL_NEWS_URL, MISTRAL_NEWS_URL,
        r"Mistral|Ministral|Mixtral|Magistral|Pixtral|Codestral|Devstral|Voxtral|Leanstral|Shieldstral|OCR",
        "html", article_path=r"/news/[^/]+/$", summary_from_body=True,
        subject_inventory=mistral_subject_inventory,
    ),
    "kimi": NewsSource(
        KIMI_BLOG_URL,
        KIMI_BLOG_URL,
        r"Kimi|kimi",
        "html",
        article_path=r"/(?:en/)?blog/[^/]+/?$",
        entry_reader=lambda client: article_list_entries(client.get_text(KIMI_BLOG_URL), KIMI_BLOG_URL),
    ),
    "minimax": NewsSource(
        "https://www.minimax.io/news",
        "https://www.minimax.io/news",
        r"MiniMax|minimax",
        "html",
    ),
    "aws-bedrock": NewsSource(
        "https://aws.amazon.com/about-aws/whats-new/",
        "https://aws.amazon.com/about-aws/whats-new/recent/feed/",
        r"GPT|Claude|Grok|Gemma|Llama|Nova|Mistral|Qwen",
        required_context=r"Bedrock",
    ),
    "azure": NewsSource(
        "https://azure.microsoft.com/en-us/blog/",
        "https://azure.microsoft.com/en-us/blog/feed/",
        r"GPT|Claude|Grok|Phi|Llama|Mistral|Qwen",
        required_context=r"Foundry|Azure",
    ),
    "google-cloud": NewsSource(
        "https://docs.cloud.google.com/gemini-enterprise-agent-platform/release-notes",
        "https://docs.cloud.google.com/feeds/gemini-enterprise-agent-platform-release-notes.xml",
        r"Gemini|Gemma|Imagen|Veo|Lyria|Claude|Llama|Mistral|Grok|GLM|DeepSeek|Qwen|MiniMax|Kimi",
        transport="atom",
    ),
    "zhipu": NewsSource(
        ZHIPU_BLOG_URL, ZHIPU_BLOG_URL, r"GLM|CogView|CogVideo|AutoGLM",
        transport="article", summary_from_body=True,
        article_reader=read_public_article,
    ),
    "volcengine": NewsSource(
        SEED_MODELS_URL, SEED_BLOG_API, r"Seed|Seedance|Seedream|Doubao",
        transport="html", summary_from_body=True,
        subject_inventory=seed_subject_inventory,
        entry_reader=lambda client: _seed_news_entries(client),
        article_reader=lambda client, url: _read_seed_article(client, url),
    ),
    "kling": NewsSource(
        "https://klingai.com/global/blog", "https://klingai.com/global/blog",
        r"Kling|KLING", transport="html", article_path=r"/blog/[^/]+$",
        requires_release_signal=True,
    ),
    "ant-ling": NewsSource(
        ANT_BLOG_URL, ANT_BLOG_URL, r"Ling|Ring|Ming",
        transport="html",
        entry_reader=lambda client: embedded_blog_entries(client.get_text(ANT_BLOG_URL), ANT_BLOG_URL),
    ),
    "xiaomi": NewsSource(
        XIAOMI_BLOG_URL, XIAOMI_BLOG_URL, r"MiMo|mimo", transport="html",
        summary_from_body=True, entry_reader=xiaomi_news_entries,
        article_reader=read_public_article,
    ),
}
INVENTORY_SOURCES = {
    "zhipu": ZhipuDescriptionSource,
    "xiaomi": XiaomiDescriptionSource,
    "kling": KlingDescriptionSource,
}
CATALOGUE_ONLY_POLICIES = {
    "openrouter": "该渠道仅监控其公开模型目录；原厂公告不证明该托管渠道已上架。",
}


def publication(
    name: str,
    url: str,
    summary: str,
    published_at: str | None,
    paragraphs: list[str],
    names: list[str],
    *,
    source_name: str,
) -> dict[str, Any]:
    """Keep the literal official name until the catalogue itself supplies an ID."""
    return {
        "model_id": name,
        "display_name": name,
        "identity_kind": "published_name",
        "source_url": url,
        "source_name": source_name,
        "published_at": published_at,
        "summary": markdown_text(summary),
        **release_facts(name, names, paragraphs),
    }


def _recent(value: str | None, reference: datetime) -> bool:
    if not value:
        return True
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=reference.tzinfo)
    return reference - timedelta(days=NEWS_WINDOW_DAYS) <= moment <= reference


def _news_subjects(
    spec: NewsSource, title: str, summary: str, inventory: list[str]
) -> list[str]:
    names = publication_names(title, summary, spec.brands)
    # The inventory admits a title such as Introducing Voxtral TTS. Longest
    # literal names own their mentions; benchmark subjects in the body never do.
    named = models_named_in(title, inventory)
    return list(dict.fromkeys([*names, *(name for name in inventory if name in named)]))


def _news_entries(
    spec: NewsSource, client: Any, inventory: list[str] | None = None
) -> list[dict[str, Any]]:
    if spec.entry_reader:
        return spec.entry_reader(client)
    if spec.transport == "article":
        details = (spec.article_reader or read_public_article)(client, spec.url)
        return [{
            "title": details["title"], "url": spec.url,
            "summary": details["summary"], "body": "",
            "published_at": details["published_at"], "details": details,
        }]
    text = client.get_text(spec.index_url)
    if spec.transport in ("rss", "atom"):
        entries = feed_entries(text, spec.url)
        if spec.transport == "atom":
            entries = release_note_entries(entries, spec.url)
        if spec.article_trailing_slash:
            # OpenAI's feed omits the slash its article server redirects to.
            # Request the canonical path directly instead of spending a second
            # host-budget attempt on every article.
            for entry in entries:
                parts = urlsplit(entry["url"])
                entry["url"] = urlunsplit(parts._replace(path=parts.path.rstrip("/") + "/"))
        return entries
    page = page_data(text, exclude_navigation=False)
    if not page.links:
        raise SourceError("official news index published no readable links")
    entries: dict[str, dict[str, Any]] = {}
    for raw, title in page.links:
        url = official_link(spec.url, raw)
        if not url or url.rstrip("/") == spec.url.rstrip("/"):
            continue
        if spec.article_path and not re.search(spec.article_path, urlsplit(url).path):
            continue
        if not _news_subjects(spec, title, "", inventory or []):
            continue
        # Index cards sometimes repeat their heading and synopsis in one link.
        # The first entry is the featured heading rather than its repeated card.
        entry = entries.setdefault(
            url,
            {
                "title": title,
                "url": url,
                "summary": "",
                "body": "",
                "published_at": None,
            },
        )
        entry["published_at"] = (
            entry["published_at"] or page.link_dates.get(raw) or date_value(title)
        )
    return list(entries.values())


def _read_news(
    spec: NewsSource, client: Any, reference: datetime
) -> tuple[list[dict[str, Any]], list[str]]:
    found = []
    errors = []
    inventory: list[str] = []
    if spec.subject_inventory:
        try:
            inventory = spec.subject_inventory(client)
        except Exception as exc:
            # A missing catalogue cannot suppress independently readable news.
            # Versioned subjects still parse; incomplete unversioned coverage
            # remains a partial source error and cannot replace an archive.
            errors.append(f"official release subject inventory: {exc}")
    for entry in _news_entries(spec, client, inventory):
        if not _recent(entry["published_at"], reference):
            continue
        title = entry["title"]
        if re.search(r"bug bounty|customer story|case study", title, re.I):
            continue
        names = _news_subjects(spec, entry.get("subject_title") or title, entry["summary"], inventory)
        if not names and entry.get("subject_title"):
            names = _news_subjects(spec, title, entry["summary"], inventory)
        if not names and spec.transport == "atom":
            # Atom release notes name their models in the dated entry body.
            names = model_names(entry["summary"], spec.brands)
        if not names:
            if spec.transport == "article":
                raise SourceError("registered official model article did not identify a readable model subject")
            continue
        if spec.required_context and not re.search(
            spec.required_context, title + " " + entry["summary"], re.I
        ):
            continue
        subject_title = re.sub(r"^[\w -]+[’']s\s+", "", entry.get("subject_title") or title)
        model_subject = any(
            subject_title == name
            or subject_title.startswith(name + ":")
            or subject_title.startswith(name + ",")
            or subject_title.startswith(name + " Tech")
            or re.search(
                r"^" + re.escape(name) + r"\s+(?:and\b|(?:is|are)\s+(?:now\s+)?(?:available|generally\b|publicly\b))",
                subject_title,
                re.I,
            )
            for name in names
        ) or bool(re.search(r"^The following models are (?:now )?available", subject_title, re.I))
        if entry.get("model_subject_required") and not model_subject:
            continue
        release_signal = entry.get("is_release") or RELEASE_WORDS.search(title + " " + entry["summary"])
        if not release_signal and (spec.requires_release_signal or not model_subject):
            continue
        details = entry.get("details") or (article_details(entry["body"]) if entry["body"] else {})
        detail_error = ""
        if spec.transport == "html" or spec.index_url.endswith(("rss/", "rss.xml")):
            try:
                details = (
                    spec.article_reader(client, entry["url"])
                    if spec.article_reader
                    else article_details(client.get_text(entry["url"]))
                )
                # The indexed subject must occur in the article itself. A soft
                # 404 cannot lend an unrelated introduction to a model.
                proof = " ".join(
                    [
                        details.get("title", ""),
                        details.get("summary", ""),
                        *details.get("paragraphs", []),
                    ]
                )
                if not all(mentions_model(proof, name) for name in names):
                    raise SourceError(
                        "official release page did not identify its indexed model"
                    )
            except Exception as exc:
                detail_error = f"{entry['url']}: {exc}"
                errors.append(detail_error)
                details = {}
        when = details.get("published_at") or entry["published_at"]
        if not _recent(when, reference):
            continue
        summary = details.get("summary") or entry["summary"] or ""
        paragraphs = details.get("paragraphs") or [entry["summary"]]
        if spec.summary_from_body:
            # Some blog metadata is site-wide SEO. Keep the first model-scoped
            # paragraph instead of a generic introduction or a later example.
            summary = next((
                paragraph for paragraph in paragraphs
                if any(mentions_model(paragraph, name) for name in names)
                and paragraph not in [title, *names]
            ), "")
        for name in names:
            item = publication(
                name,
                spec.index_url if detail_error else entry["url"],
                summary,
                when,
                paragraphs,
                names,
                source_name="官方模型发布",
            )
            if detail_error:
                item.update(
                    {
                        "detail_status": "source_error",
                        "detail_error": detail_error,
                        "article_url": entry["url"],
                    }
                )
            found.append(item)
    return found, errors


def _read_inventory(
    source_class: Any, client: Any, reference: datetime
) -> list[dict[str, Any]]:
    source = source_class(client)
    return [
        {
            **publication(
                item["model_id"],
                item["source"]["url"],
                item["summary"],
                None,
                [item["summary"]],
                [item["model_id"]],
                source_name=source.source_name,
            ),
            "identity_kind": "document_model_id",
            "description": item,
        }
        for item in source.catalogue_descriptions()
    ]


def _read_deepseek(client: Any, reference: datetime) -> list[dict[str, Any]]:
    return [
        publication(
            entry_model_label(item),
            DEEPSEEK_UPDATES_URL,
            item.summary,
            item.published_at,
            [item.body],
            [entry_model_label(item)],
            source_name="DeepSeek 官方更新",
        )
        for item in read_updates(client)
        if _recent(item.published_at, reference)
    ]


def _read_tencent(client: Any, reference: datetime) -> list[dict[str, Any]]:
    found = []
    page_num = 1
    while True:
        # The site's own anonymous, read-only list endpoint publishes full article
        # text. No login, console capture, or authenticated detail API is involved.
        raw = client.request(
            TENCENT_BLOG_API,
            method="POST",
            data=json.dumps(
                {"pageNum": page_num, "pageSize": 100, "needFilter": True}
            ).encode(),
            headers={"Content-Type": "application/json"},
            idempotent=True,
        )
        payload = json.loads(raw)
        data = payload.get("data") or {}
        items = data.get("list")
        if payload.get("code") != 0 or not isinstance(items, list):
            raise SourceError(
                "Tencent public research index did not return a model list"
            )
        for item in items:
            names = model_names(item.get("title") or "", r"Hy|HY|Hunyuan|混元")
            when = published_date(
                item.get("displayPublishTime") or item.get("publishedAt") or ""
            )
            if not names or not _recent(when, reference):
                continue
            raw_body = item.get("content") or ""
            url = f"{TENCENT_BLOG_URL}research/{item.get('customUrl') or item['id']}"
            found.extend(
                publication(
                    name,
                    url,
                    item.get("desc") or markdown_summary(raw_body),
                    when,
                    prose_paragraphs(raw_body),
                    names,
                    source_name="腾讯混元官方研究",
                )
                for name in names
            )
        if page_num * 100 >= data.get("totalNum", len(items)):
            return found
        if not items:
            raise SourceError(
                "Tencent public research pagination ended before its total"
            )
        page_num += 1


def _read_qwen(client: Any, reference: datetime) -> list[dict[str, Any]]:
    payload = json.loads(client.get_text(QWEN_BLOG_API))
    articles = (payload.get("data") or {}).get("articles")
    if not isinstance(articles, list) or not articles:
        raise SourceError("Qwen official blog API published no readable articles")
    found = []
    for item in articles:
        extra = item.get("extra") or {}
        summary = extra.get("description") or extra.get("introduction") or ""
        names = publication_names(
            item.get("title") or "", markdown_text(summary), r"Qwen|QwQ|Wan"
        )
        when = published_date(str(extra.get("date") or ""))
        if not names or not _recent(when, reference):
            continue
        if not item.get("path"):
            raise SourceError("Qwen official blog entry published no article path")
        url = QWEN_BLOG_URL + "blog?" + urlencode({"id": item["path"]})
        details = article_details(item.get("content") or "")
        found.extend(
            publication(
                name,
                url,
                summary or details.get("summary") or "",
                when,
                details.get("paragraphs") or [markdown_text(summary)],
                names,
                source_name="Qwen 官方博客",
            )
            for name in names
        )
    return found


def _seed_router_data(text: str) -> dict[str, Any]:
    match = re.search(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", text, re.S)
    if not match:
        raise SourceError("Seed official document did not publish its router data")
    return json.loads(match.group(1))


def _seed_models(client: Any) -> list[dict[str, Any]]:
    cached = getattr(client, "_seed_model_inventory", None)
    if cached is not None:
        return cached
    data = _seed_router_data(client.get_text(SEED_MODELS_URL))
    layout = (data.get("loaderData") or {}).get("layout") or {}
    groups = layout.get("footer_config") or []
    models = [
        item
        for group in groups
        if group.get("titleEn") == "Models"
        for item in group.get("content") or []
    ]
    if not models:
        raise SourceError("Seed official model inventory contained no model entries")
    client._seed_model_inventory = models
    return models


def _read_seed(client: Any, reference: datetime) -> list[dict[str, Any]]:
    return [
        publication(
            item["labelEn"],
            url,
            "",
            None,
            [],
            [item["labelEn"]],
            source_name="字节 Seed 官方模型成果",
        )
        for item in _seed_models(client)
        if (url := official_link(SEED_MODELS_URL, item.get("linkEn") or ""))
    ]


def seed_blog_url(token: str = "0") -> str:
    return SEED_BLOG_API + "?" + urlencode({
        "article_type": 2, "count": 100, "order_desc": "true", "page_token": token,
    })


def _seed_news_entries(client: Any) -> list[dict[str, Any]]:
    """Read every public blog page; pinned older posts do not stop pagination."""
    entries = []
    token = "0"
    seen: set[str] = set()
    while token not in seen:
        seen.add(token)
        payload = json.loads(client.get_text(seed_blog_url(token)))
        items = payload.get("sub_article_list")
        # The official API omits the list on its successful terminal page.
        # Missing rows on an initial or continuing page remain a source error.
        if items is None and token != "0" and payload.get("has_more") is False:
            items = []
        if (payload.get("BaseResp") or {}).get("StatusCode") != 0 or not isinstance(items, list):
            raise SourceError("Seed official blog API published no readable article list")
        for item in items:
            metadata = item.get("ArticleMeta") or {}
            article = item.get("ArticleSubContentEn") or {}
            if metadata.get("StatusEn") != 2:
                continue
            url = official_link(SEED_MODELS_URL + "/", article.get("TitleKey") or "")
            if not article.get("Title") or not article.get("TitleKey") or not url:
                raise SourceError("Seed official blog entry omitted its title or article path")
            entries.append({
                "title": article["Title"], "url": url,
                "subject_title": article.get("ImageTitle") or "",
                "summary": article.get("Abstract") or "", "body": "",
                "published_at": published_date(metadata["PublishDate"] / 1000) if metadata.get("PublishDate") else None,
            })
        if not payload.get("has_more"):
            return entries
        if not items or not payload.get("next_page_token"):
            raise SourceError("Seed official blog pagination ended before its total")
        token = str(payload["next_page_token"])
    raise SourceError("Seed official blog pagination repeated its page token")


def _read_seed_article(client: Any, url: str) -> dict[str, Any]:
    """The article's own public router payload owns its body and publication date."""
    data = _seed_router_data(client.get_text(url))
    route = (data.get("loaderData") or {}).get("(locale$)/blog/(id)/page") or {}
    article = (route.get("data") or {}).get("article") or {}
    item = article.get("SubArticle") or {}
    metadata = item.get("ArticleMeta") or {}
    content = item.get("ArticleSubContentEn") or {}
    if content.get("TitleKey") != urlsplit(url).path.rsplit("/", 1)[-1] or not article.get("ContentEn"):
        raise SourceError("Seed official article payload did not identify its indexed article")
    return {
        **article_details(article["ContentEn"]),
        "title": content.get("Title") or "",
        "summary": content.get("Abstract") or "",
        "published_at": published_date(metadata["PublishDate"] / 1000) if metadata.get("PublishDate") else None,
    }


def _read_zhipu_updates(client: Any, reference: datetime) -> list[dict[str, Any]]:
    """Use literal subjects and dates from Z.ai's published Update blocks."""
    text = client.get_text(ZHIPU_UPDATES_URL)
    blocks = re.findall(r"<Update\s+([^>]+)>(.*?)</Update>", text, re.S)
    if not blocks:
        raise SourceError("Z.ai official update log published no readable model updates")
    found = []
    for attributes, body in blocks:
        values = dict(re.findall(r'(\w+)="([^"]*)"', attributes))
        name = markdown_text(values.get("description") or "")
        when = published_date(values.get("label") or "")
        if not name or not when:
            raise SourceError("Z.ai official model update omitted its subject or date")
        if _recent(when, reference):
            found.append(publication(
                name, ZHIPU_UPDATES_URL, markdown_summary(body), when,
                prose_paragraphs(body), [name], source_name="智谱 Z.ai 官方模型更新",
            ))
    return found


def _read_xiaomi_updates(client: Any, reference: datetime) -> list[dict[str, Any]]:
    """A dated release section names its models; labelled bullets own variants."""
    sections: list[tuple[str, str, list[str]]] = []
    for tag, value in page_data(client.get_text(XIAOMI_UPDATES_URL)).blocks:
        if tag == "h2":
            when = date_value(value)
            if when and RELEASE_WORDS.search(value):
                sections.append((value, when, []))
            else:
                sections.append((value, "", []))
        elif sections and tag in ("p", "li"):
            sections[-1][2].append(value)
    if not any(when for _, when, _ in sections):
        raise SourceError("Xiaomi official update log published no dated model releases")
    found = []
    for title, when, paragraphs in sections:
        if not when or not _recent(when, reference):
            continue
        subjects = {
            name: paragraph
            for paragraph in paragraphs
            if re.search(r"[:：]", paragraph)
            for name in model_names(re.split(r"[:：]", paragraph, maxsplit=1)[0], r"MiMo|mimo")
        }
        names = list(subjects) or model_names(title, r"MiMo|mimo")
        if not names:
            raise SourceError("Xiaomi official release section omitted its model subject")
        for name in names:
            found.append(publication(
                name, XIAOMI_UPDATES_URL,
                subjects.get(name) or " ".join(paragraphs), when,
                paragraphs, names, source_name="小米 MiMo 官方模型更新",
            ))
    return found


CUSTOM_SOURCES: dict[str, tuple[str, str, Callable[..., list[dict[str, Any]]]]] = {
    "deepseek": (DEEPSEEK_UPDATES_URL, "recent_official_news", _read_deepseek),
    "tencent": (TENCENT_BLOG_URL, "recent_official_news", _read_tencent),
    "aliyun": (QWEN_BLOG_URL, "recent_official_news", _read_qwen),
    "volcengine": (SEED_MODELS_URL, "official_model_inventory", _read_seed),
    "zhipu": (ZHIPU_UPDATES_URL, "recent_official_news", _read_zhipu_updates),
    "xiaomi": (XIAOMI_UPDATES_URL, "recent_official_news", _read_xiaomi_updates),
}


PublicationRead = list[dict[str, Any]] | tuple[list[dict[str, Any]], list[str]]
PublicationReader = Callable[[Any, datetime], PublicationRead]


def _registered_sources(provider_id: str) -> list[tuple[str, str, PublicationReader]]:
    """Compose independent official evidence instead of choosing one over another."""
    sources: list[tuple[str, str, PublicationReader]] = []
    if provider_id in NEWS_SOURCES:
        spec = NEWS_SOURCES[provider_id]
        sources.append((spec.url, "recent_official_news", partial(_read_news, spec)))
    if provider_id in INVENTORY_SOURCES:
        source = INVENTORY_SOURCES[provider_id]
        sources.append((source.source_url, "official_model_inventory", partial(_read_inventory, source)))
    if provider_id in CUSTOM_SOURCES:
        sources.append(CUSTOM_SOURCES[provider_id])
    return sources


def discovery_policy(provider_id: str) -> dict[str, Any]:
    """Keep coverage and provenance visible even when the source cannot be read."""
    sources = _registered_sources(provider_id)
    if not sources:
        return {
            "source": None,
            "coverage": "catalogue_only",
            "note": CATALOGUE_ONLY_POLICIES.get(
                provider_id, "该渠道尚未登记独立的官方模型发布来源。"
            ),
        }
    urls = list(dict.fromkeys(url for url, _, _ in sources))
    coverages = {coverage for _, coverage, _ in sources}
    coverage = "official_news_and_inventory" if len(coverages) > 1 else next(iter(coverages))
    source: dict[str, Any] = {"url": urls[0], "kind": "official_model_discovery"}
    if len(urls) > 1:
        source["urls"] = urls
    note = f"新闻读取最近 {NEWS_WINDOW_DAYS} 天的官方发布；已收录历史与当前能力目录保留。"
    news = NEWS_SOURCES.get(provider_id)
    if news and news.transport == "article":
        note += "该博客未提供可读总索引，独立文章仅覆盖已登记的官方链接。"
    return {
        "source": source,
        "coverage": coverage,
        "note": note,
    }


def read_publications(
    provider_id: str, client: Any, reference: datetime
) -> dict[str, Any]:
    """Report coverage explicitly rather than treating an unmonitored blog as quiet."""
    policy = discovery_policy(provider_id)
    if policy["coverage"] == "catalogue_only":
        return {**policy, "status": "catalogue_only", "models": []}
    errors: list[str] = []
    models: list[dict[str, Any]] = []
    for url, _, reader in _registered_sources(provider_id):
        try:
            result = reader(client, reference)
            found, failures = result if isinstance(result, tuple) else (result, [])
            models.extend(found)
            errors.extend(f"{url}: {error}" for error in failures)
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    return {
        **policy,
        "status": "source_error" if errors else "available",
        "models": models,
        "errors": errors,
    }
