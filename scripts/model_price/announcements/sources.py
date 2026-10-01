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
from ..parsing import date_value
from .parsing import (
    RELEASE_WORDS,
    article_details,
    feed_entries,
    markdown_text,
    model_names,
    official_link,
    page_data,
    published_date,
    release_facts,
    mentions_model,
    publication_names,
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
    "kimi": NewsSource(
        "https://www.kimi.com/blog",
        "https://www.kimi.com/blog",
        r"Kimi|kimi",
        "html",
        article_path=r"/(?:en/)?blog/[^/]+/?$",
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
        "https://docs.cloud.google.com/vertex-ai/generative-ai/docs/release-notes",
        "https://cloud.google.com/static/feeds/generative-ai-on-vertex-ai-release-notes.xml",
        r"Gemini|Gemma|Imagen|Veo|Lyria|Claude|Llama|Mistral",
        transport="atom",
    ),
}
INVENTORY_SOURCES = {
    "zhipu": ZhipuDescriptionSource,
    "xiaomi": XiaomiDescriptionSource,
    "kling": KlingDescriptionSource,
}
CATALOGUE_ONLY_POLICIES = {
    "openrouter": "该渠道仅监控其公开模型目录；原厂公告不证明该托管渠道已上架。",
    "ant-ling": "该渠道读取公开价格目录及下架文档；尚未登记独立的官方模型发布来源。",
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


def _news_entries(spec: NewsSource, client: Any) -> list[dict[str, Any]]:
    text = client.get_text(spec.index_url)
    if spec.transport in ("rss", "atom"):
        entries = feed_entries(text, spec.url)
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
        if spec.article_path and not re.search(spec.article_path, url):
            continue
        if not model_names(title, spec.brands):
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
    for entry in _news_entries(spec, client):
        if not _recent(entry["published_at"], reference):
            continue
        title = entry["title"]
        if re.search(r"bug bounty|customer story|case study", title, re.I):
            continue
        names = publication_names(title, entry["summary"], spec.brands)
        if not names and spec.transport == "atom":
            # Atom release notes name their models in the dated entry body.
            names = model_names(entry["summary"], spec.brands)
        if not names:
            continue
        if spec.required_context and not re.search(
            spec.required_context, title + " " + entry["summary"], re.I
        ):
            continue
        model_subject = any(
            title == name
            or title.startswith(name + ":")
            or title.startswith(name + " Tech")
            for name in names
        )
        if not model_subject and not RELEASE_WORDS.search(
            title + " " + entry["summary"]
        ):
            continue
        details = article_details(entry["body"]) if entry["body"] else {}
        detail_error = ""
        if spec.transport == "html" or spec.index_url.endswith(("rss/", "rss.xml")):
            try:
                details = article_details(client.get_text(entry["url"]))
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


def _read_seed(client: Any, reference: datetime) -> list[dict[str, Any]]:
    text = client.get_text(SEED_MODELS_URL)
    match = re.search(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", text, re.S)
    if not match:
        raise SourceError(
            "Seed official model inventory did not publish its router data"
        )
    data = json.loads(match.group(1))
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
        for item in models
        if (url := official_link(SEED_MODELS_URL, item.get("linkEn") or ""))
    ]


CUSTOM_SOURCES: dict[str, tuple[str, Callable[..., list[dict[str, Any]]]]] = {
    "deepseek": (DEEPSEEK_UPDATES_URL, _read_deepseek),
    "tencent": (TENCENT_BLOG_URL, _read_tencent),
    "aliyun": (QWEN_BLOG_URL, _read_qwen),
    "volcengine": (SEED_MODELS_URL, _read_seed),
}


def discovery_policy(provider_id: str) -> dict[str, Any]:
    """Keep coverage and provenance visible even when the source cannot be read."""
    if provider_id in NEWS_SOURCES:
        spec = NEWS_SOURCES[provider_id]
        url, coverage = spec.url, "recent_official_news"
    elif provider_id in INVENTORY_SOURCES:
        source = INVENTORY_SOURCES[provider_id]
        url, coverage = source.source_url, "official_model_inventory"
    elif provider_id in CUSTOM_SOURCES:
        url, _ = CUSTOM_SOURCES[provider_id]
        coverage = (
            "official_model_inventory"
            if provider_id == "volcengine"
            else "recent_official_news"
        )
    else:
        return {
            "source": None,
            "coverage": "catalogue_only",
            "note": CATALOGUE_ONLY_POLICIES.get(
                provider_id, "该渠道尚未登记独立的官方模型发布来源。"
            ),
        }
    return {
        "source": {"url": url, "kind": "official_model_discovery"},
        "coverage": coverage,
        "note": f"新闻读取最近 {NEWS_WINDOW_DAYS} 天的官方发布；已收录历史与当前能力目录保留。",
    }


def read_publications(
    provider_id: str, client: Any, reference: datetime
) -> dict[str, Any]:
    """Report coverage explicitly rather than treating an unmonitored blog as quiet."""
    policy = discovery_policy(provider_id)
    if policy["coverage"] == "catalogue_only":
        return {**policy, "status": "catalogue_only", "models": []}
    errors: list[str] = []
    try:
        if provider_id in NEWS_SOURCES:
            models, errors = _read_news(NEWS_SOURCES[provider_id], client, reference)
        elif provider_id in INVENTORY_SOURCES:
            models = _read_inventory(INVENTORY_SOURCES[provider_id], client, reference)
        else:
            _, reader = CUSTOM_SOURCES[provider_id]
            models = reader(client, reference)
    except Exception as exc:
        models, errors = [], [str(exc)]
    return {
        **policy,
        "status": "source_error" if errors else "available",
        "models": models,
        "errors": errors,
    }
