"""Exercise release discovery independently of API pricing, using official shapes.

The fixtures preserve the evidence that matters: phased release, separate future
rates, and two models in the same article with different access restrictions.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.announcements.core import compare_announcements, scan_announcements
from model_price.announcements.documents import (
    compiled_article_details,
    compiled_blog_entries,
    read_public_article,
    read_rspress_blog_entries,
)
from model_price.announcements.parsing import (
    article_details,
    feed_entries,
    model_names,
    mentions_model,
    publication_names,
    release_facts,
)
from model_price.announcements.sources import (
    CATALOGUE_ONLY_POLICIES,
    CUSTOM_SOURCES,
    GOOGLE_BLOG_URL,
    INVENTORY_SOURCES,
    NEWS_SOURCES,
    QWEN_BLOG_API,
    KIMI_BLOG_URL,
    SEED_MODELS_URL,
    ZHIPU_BLOG_URL,
    ZHIPU_UPDATES_URL,
    XIAOMI_UPDATES_URL,
    XIAOMI_BLOG_URL,
    XIAOMI_BLOG_ASSETS,
    discovery_policy,
    read_publications,
    seed_blog_url,
)
from model_price.changes import changed_model_groups
from model_price.delta import scan_providers
from model_price.descriptions.core import DescriptionSource
from model_price.descriptions.resolver import DescriptionResolver
from model_price.errors import SourceError
from model_price.messages import scan_message
from model_price.paths import ANNOUNCEMENT_SCHEMA_VERSION
from model_price.pricing import make_record, price_item
from model_price.providers import ALL_PROVIDERS
from model_price.reporting import channel_status
from model_price.snapshots import SnapshotStore, parse_baseline_selection

ARGON_URL = f"{GOOGLE_BLOG_URL}gemini-4-argon/"
INTRODUCTORY = (
    "Argon will launch at an introductory price of $2 per million input tokens "
    "and $10 per million output tokens, with cached input tokens priced at 95% off input token price."
)
REGULAR = "After the introductory period expires, the price of $4 per 1M input tokens and $20 per 1M output tokens will apply."
ARGON_PAGE = f"""<html><head><script type="application/ld+json">{{
"@type":"NewsArticle", "headline":"Gemini 4 Argon: our next era of frontier intelligence",
"datePublished":"2026-09-30T20:00:00+00:00",
"description":"Announcing Gemini 4 Argon, our frontier model for real-world coding, enterprise knowledge work, and cyber defense, rolling out soon."
}}</script></head><body><nav><p>Available to all API users</p></nav>
<h1>Gemini 4 Argon: our next era of frontier intelligence</h1>
<p>Today, we’re announcing our new frontier model, Gemini 4 Argon, which is rolling out to a set of trusted cyber defenders through our Fairwind Program.</p>
<p>We’ll continue to gather feedback from early testers before making Argon available to developers, enterprises, and consumers as soon as possible.</p>
<p>{INTRODUCTORY}</p>
<p>{REGULAR}</p><footer><p>Other Model 99 is available now.</p></footer></body></html>"""


def rss(
    entries: list[tuple[str, str, str]],
    *,
    when: str = "Wed, 30 Sep 2026 20:00:00 +0000",
) -> str:
    return (
        "<rss><channel>"
        + "".join(
            f"<item><title>{title}</title><link>{url}</link><pubDate>{when}</pubDate>"
            f"<description><![CDATA[{summary}]]></description></item>"
            for title, url, summary in entries
        )
        + "</channel></rss>"
    )


ARGON_FEED = rss(
    [
        (
            "Gemini 4 Argon: our next era of frontier intelligence",
            ARGON_URL,
            "Announcing Gemini 4 Argon, rolling out soon.",
        )
    ]
)

FIXTURES = Path(__file__).parent / "fixtures"
LEGAL_MODULE_URL = "https://z.ai/blog/assets/legal-current.js"
LEGAL_SHELL = (FIXTURES / "zai-article-shell.html").read_text(encoding="utf-8")
LEGAL_MODULE = (FIXTURES / "zai-article-module.js").read_text(encoding="utf-8")
ZHIPU_OVERVIEW = "# 模型概览\n\n| 模型 | 特点 |\n| --- | --- |\n| GLM-9 | 通用文本模型 |\n"
ZHIPU_UPDATES = '<Update label="2026-10-09" description=" GLM-9">A model for reasoning.</Update>'


class MappingClient:
    """Record every attempted read and reject any unprovided URL."""

    def __init__(self, pages: dict[str, Any]) -> None:
        self.pages = pages
        self.calls: list[str] = []
        self.posts: list[dict[str, Any]] = []

    def get_text(self, url: str) -> str:
        self.calls.append(url)
        result = self.pages[url]
        if isinstance(result, Exception):
            raise result
        return result

    def request(self, url: str, **arguments: Any) -> str:
        self.posts.append(arguments)
        return self.get_text(url)


class Adapter:
    provider_id = "google"
    provider_name = "Google Gemini"
    source_url = "https://ai.google.dev/gemini-api/docs/pricing"
    source_kind = "test"

    def __init__(self, records: list[dict[str, Any]] | Exception | None = None) -> None:
        self.records = records if records is not None else [record("gemini-3.8-flash")]

    def catalog_records(self) -> list[dict[str, Any]]:
        if isinstance(self.records, Exception):
            raise self.records
        return self.records


def record(model: str, *, priced: bool = True) -> dict[str, Any]:
    return make_record(
        "google",
        "Google Gemini",
        model,
        model,
        "Global",
        (
            [
                {
                    "name": "standard",
                    "conditions": {},
                    "prices": [
                        price_item("input", "输入", "0.125", "USD_per_million_tokens")
                    ],
                }
            ]
            if priced
            else []
        ),
        Adapter.source_url,
        "test",
        "2026-10-01T12:00:00+08:00",
    )


class ReleaseParsingTests(unittest.TestCase):
    def test_argon_access_and_each_published_rate_keep_their_scope(self) -> None:
        details = article_details(ARGON_PAGE)
        self.assertEqual(details["published_at"], "2026-09-30T20:00:00+00:00")
        facts = release_facts(
            "Gemini 4 Argon", ["Gemini 4 Argon"], details["paragraphs"]
        )
        access = facts["access"]
        self.assertEqual(
            (access["status"], access["developers"], access["consumers"]),
            ("limited", "pending", "pending"),
        )
        self.assertNotIn("Available to all API users", " ".join(access["statements"]))
        offers = facts["announced_offers"]
        self.assertEqual(
            [[p["amount"] for p in o["prices"]] for o in offers],
            [["2", "10"], ["4", "20"]],
        )
        self.assertEqual(
            [o["conditions"]["published_terms"] for o in offers],
            [INTRODUCTORY, REGULAR],
        )
        self.assertFalse(
            any(
                "effective_until" in p or p["type"] == "cache_hit"
                for o in offers
                for p in o["prices"]
            )
        )

    def test_one_article_does_not_share_mythos_restrictions_or_fable_prices(
        self,
    ) -> None:
        names = ["Claude Fable 5.1", "Claude Mythos 5.1"]
        paragraphs = [
            "Claude Fable 5.1 and Claude Mythos 5.1 have different safeguards. Fable 5.1 is generally available, while Mythos 5.1 is available only through our trusted access programs.",
            "Fable 5.1’s pricing is $10 per million input tokens and $50 per million output tokens.",
        ]
        fable = release_facts(names[0], names, paragraphs)
        mythos = release_facts(names[1], names, paragraphs)
        self.assertEqual(fable["access"]["status"], "public")
        self.assertEqual(mythos["access"]["status"], "limited")
        self.assertEqual(mythos["announced_offers"], [])
        self.assertEqual(
            [p["amount"] for p in fable["announced_offers"][0]["prices"]], ["10", "50"]
        )

    def test_coming_weights_and_reports_do_not_close_an_open_api(self) -> None:
        facts = release_facts(
            "Kimi K3",
            ["Kimi K3"],
            [
                "Kimi K3 is available today on Kimi.com and the Kimi API. The full model weights will be released next month. More technical details will be available in our coming report."
            ],
        )
        self.assertEqual(facts["access"]["status"], "public")
        self.assertEqual(facts["access"]["developers"], "public")
        self.assertEqual(facts["access"]["consumers"], "unknown")

    def test_new_names_and_explicit_coordinated_variants_need_no_name_list(
        self,
    ) -> None:
        self.assertEqual(
            model_names("Introducing Gemini 9 Quartz", "Gemini"), ["Gemini 9 Quartz"]
        )
        self.assertEqual(model_names("Introducing Hy9 preview", "Hy"), ["Hy9 preview"])
        self.assertEqual(
            publication_names(
                "Introducing Gemini 3.8 Flash and 3.8 Flash Cyber",
                "Gemini 3.8 Flash and Gemini 3.8 Flash Cyber are our new models.",
                "Gemini",
            ),
            ["Gemini 3.8 Flash", "Gemini 3.8 Flash Cyber"],
        )
        self.assertEqual(
            publication_names(
                "Introducing Claude Opus 5.5",
                "Opus 5.5 performs at the level of Claude Fable 5.1.",
                "Claude",
            ),
            ["Claude Opus 5.5"],
        )

    def test_coordinated_shorthand_keeps_a_cyber_variant_and_its_prices_separate(
        self,
    ) -> None:
        names = publication_names(
            "Introducing Gemini 3.8 Flash and 3.8 Flash Cyber",
            "Gemini 3.8 Flash and 3.8 Flash Cyber are our newest models.",
            "Gemini",
        )
        self.assertEqual(names, ["Gemini 3.8 Flash", "Gemini 3.8 Flash Cyber"])
        paragraphs = [
            "Gemini 3.8 Flash: our workhorse model. It is available at $0.75 per million input tokens and $3.75 per million output tokens.",
            "Gemini 3.8 Flash Cyber is available to trusted defenders through our Fairwind Program.",
            "Consumers: 3.8 Flash is available to Google AI Pro and Ultra subscribers.",
        ]
        flash = release_facts(names[0], names, paragraphs)
        cyber = release_facts(names[1], names, paragraphs)
        self.assertEqual(flash["access"]["status"], "public")
        self.assertEqual(flash["access"]["consumers"], "public")
        self.assertEqual(cyber["access"]["status"], "limited")
        self.assertEqual(cyber["announced_offers"], [])
        self.assertEqual(
            [p["amount"] for p in flash["announced_offers"][0]["prices"]],
            ["0.75", "3.75"],
        )

    def test_feature_restrictions_and_early_testimonials_do_not_close_the_model(
        self,
    ) -> None:
        name = "Claude Opus 5.5"
        facts = release_facts(
            name,
            [name],
            [
                "Claude Opus 5.5 results come from our customer's evaluation during early access.",
                "Custom avatar creation is available only through enterprise allowlisting.",
                "To use Opus 5.5 for specialized research, vetted organizations can get access to expanded safeguards.",
                "Claude Opus 5.5 is now available on all platforms.",
            ],
        )
        self.assertEqual(facts["access"]["status"], "public")
        self.assertFalse(
            any(
                "avatar" in text or "evaluation" in text
                for text in facts["access"]["statements"]
            )
        )

    def test_a_public_api_and_a_private_hosting_preview_can_coexist(self) -> None:
        name = "Gemini Robotics ER 2"
        facts = release_facts(
            name,
            [name],
            [
                "Gemini Robotics ER 2 is now publicly available to developers via the Gemini API, and in private preview on Gemini Enterprise Agent Platform."
            ],
        )
        self.assertEqual(facts["access"]["status"], "public")
        self.assertEqual(facts["access"]["developers"], "public")
        self.assertIn("private preview", facts["access"]["statements"][0])

    def test_chinese_framework_preview_is_not_the_models_api_status(self) -> None:
        name = "DeepSeek-V4-Flash"
        facts = release_facts(
            name,
            [name],
            [
                "DeepSeek-V4-Flash 正式版 API 上线公测，模型名设置为 deepseek-v4-flash 即可使用最新版本。注1：测试框架极简模式即将发布。"
            ],
        )
        self.assertEqual(facts["access"]["developers"], "public")

    def test_feed_rejects_soft_html_and_foreign_article_links(self) -> None:
        with self.assertRaises(ValueError):
            feed_entries("<html><p>News</p></html>", GOOGLE_BLOG_URL)
        with self.assertRaises(ValueError):
            feed_entries(
                rss(
                    [
                        (
                            "Introducing Gemini 9 Quartz",
                            "https://elsewhere.example/model",
                            "Announcing a model.",
                        )
                    ]
                ),
                GOOGLE_BLOG_URL,
            )

    def test_negative_availability_and_comparison_rates_never_become_current_facts(
        self,
    ) -> None:
        facts = release_facts(
            "Gemini 9 Quartz",
            ["Gemini 9 Quartz"],
            [
                "Gemini 9 Quartz is not available today to developers or consumers.",
                "Gemini 8 Quartz is available today and costs $1 per million input tokens.",
                "GPT-8.1 costs $3 per million input tokens.",
            ],
        )
        self.assertEqual(facts["access"]["status"], "pending")
        self.assertEqual(facts["access"]["developers"], "pending")
        self.assertEqual(facts["announced_offers"], [])

    def test_atom_dates_and_relative_rss_links_keep_official_precision(self) -> None:
        atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>New model</title><link rel="alternate" href="https://docs.cloud.google.com/vertex-ai/generative-ai/docs/release-notes#new"/><updated>2026-09-30</updated><content type="html">Introducing Gemini 9 Quartz.</content></entry></feed>"""
        entry = feed_entries(atom, NEWS_SOURCES["google-cloud"].url)[0]
        self.assertEqual(entry["published_at"], "2026-09-30")
        entries = feed_entries(
            rss([("New model", "/blog/posts/model/", "A model.")]),
            "https://ernie.baidu.com/blog/",
        )
        self.assertEqual(entries[0]["url"], "https://ernie.baidu.com/blog/posts/model/")


class DiscoverySourceTests(unittest.TestCase):
    def test_google_discovers_argon_from_the_official_feed(self) -> None:
        spec = NEWS_SOURCES["google"]
        client = MappingClient({spec.index_url: ARGON_FEED, ARGON_URL: ARGON_PAGE})
        result = read_publications(
            "google", client, datetime.fromisoformat("2026-10-01T12:00:00+08:00")
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual([m["model_id"] for m in result["models"]], ["Gemini 4 Argon"])
        self.assertEqual(client.calls, [spec.index_url, ARGON_URL])

    def test_consumer_stories_and_competitors_are_not_new_models(self) -> None:
        spec = NEWS_SOURCES["google"]
        feed = rss(
            [
                (
                    "See what builders are making with Gemini 9 Quartz",
                    ARGON_URL,
                    "Customers are building websites with our model.",
                )
            ]
        )
        client = MappingClient({spec.index_url: feed})
        result = read_publications(
            "google", client, datetime.fromisoformat("2026-10-01T12:00:00+08:00")
        )
        self.assertEqual(result["models"], [])
        self.assertEqual(client.calls, [spec.index_url])

    def test_rolling_scope_does_not_fetch_old_releases(self) -> None:
        spec = NEWS_SOURCES["google"]
        feed = rss(
            [("Introducing Gemini 1 Old", ARGON_URL, "Introducing our model.")],
            when="Wed, 01 Jan 2025 00:00:00 +0000",
        )
        client = MappingClient({spec.index_url: feed})
        result = read_publications(
            "google", client, datetime.fromisoformat("2026-10-01T12:00:00+08:00")
        )
        self.assertEqual(result["models"], [])
        self.assertEqual(client.calls, [spec.index_url])

    def test_html_index_dates_and_linked_pages_not_navigation_drive_discovery(
        self,
    ) -> None:
        spec = NEWS_SOURCES["xai"]
        url = "https://x.ai/news/grok-9"
        index = '<a href="/news/grok-9"><span>Sep 30, 2026</span><h2>Introducing Grok 9</h2></a><a href="/news/old"><span>Jan 1, 2025</span><h2>Introducing Grok 1</h2></a>'
        page = "<h1>Introducing Grok 9</h1><p>Grok 9 is a model for coding.</p>"
        client = MappingClient({spec.index_url: index, url: page})
        result = read_publications(
            "xai", client, datetime.fromisoformat("2026-10-01T12:00:00+08:00")
        )
        self.assertEqual([m["model_id"] for m in result["models"]], ["Grok 9"])
        self.assertEqual(client.calls, [spec.index_url, url])

    def test_soft_404_keeps_only_the_official_index_evidence_and_reports_failure(
        self,
    ) -> None:
        spec = NEWS_SOURCES["google"]
        client = MappingClient(
            {
                spec.index_url: ARGON_FEED,
                ARGON_URL: "<h1>Documentation</h1><p>A generic home page.</p>",
            }
        )
        result = read_publications(
            "google", client, datetime.fromisoformat("2026-10-01T12:00:00+08:00")
        )
        self.assertEqual(result["status"], "source_error")
        self.assertEqual(result["models"][0]["source_url"], spec.index_url)
        self.assertNotIn("generic home", result["models"][0]["summary"])

    def test_overview_models_are_discovered_without_any_price_request(self) -> None:
        source = INVENTORY_SOURCES["zhipu"]
        client = MappingClient(
            {
                source.source_url: "# 模型概览\n\n| 模型 | 特点 |\n| --- | --- |\n| [GLM-9](/cn/guide/models/glm-9) | 复杂推理，尚未公开开放 |\n"
            }
        )
        result = read_publications(
            "zhipu", client, datetime.fromisoformat("2026-10-01T12:00:00+08:00")
        )
        self.assertEqual(result["models"][0]["model_id"], "GLM-9")
        self.assertEqual(result["models"][0]["summary"], "复杂推理，尚未公开开放")
        self.assertIn(source.source_url, client.calls)
        self.assertFalse(any("price" in url for url in client.calls))

    def test_seed_uses_the_official_models_group_not_the_research_link_list(
        self,
    ) -> None:
        data = {
            "loaderData": {
                "layout": {
                    "footer_config": [
                        {
                            "titleEn": "Models",
                            "content": [{"labelEn": "Seed9", "linkEn": "/en/seed9"}],
                        },
                        {
                            "titleEn": "Learn More",
                            "content": [
                                {"labelEn": "Seed Research", "linkEn": "/en/papers"}
                            ],
                        },
                    ]
                }
            }
        }
        client = MappingClient(
            {
                SEED_MODELS_URL: f"<script>window._ROUTER_DATA = {json.dumps(data)}</script>"
            }
        )
        result = read_publications(
            "volcengine", client, datetime.fromisoformat("2026-10-01T12:00:00+08:00")
        )
        self.assertEqual([m["model_id"] for m in result["models"]], ["Seed9"])
        self.assertEqual(result["models"][0]["summary"], "")

    def test_tencent_uses_the_anonymous_read_only_post_and_paginates(self) -> None:
        class Client(MappingClient):
            def request(self, url: str, **arguments: Any) -> str:
                self.posts.append(arguments)
                number = json.loads(arguments["data"])["pageNum"]
                return json.dumps(
                    {
                        "code": 0,
                        "data": {
                            "totalNum": 101,
                            "list": [
                                {
                                    "id": number,
                                    "title": f"Introducing Hy{number + 8}",
                                    "desc": "A new model",
                                    "content": "A new model",
                                    "publishedAt": "2026-09-30",
                                }
                            ],
                        },
                    }
                )

        client = Client({})
        result = read_publications(
            "tencent", client, datetime.fromisoformat("2026-10-01T12:00:00+08:00")
        )
        self.assertEqual([m["model_id"] for m in result["models"]], ["Hy9", "Hy10"])
        self.assertTrue(
            all(p["method"] == "POST" and p["idempotent"] for p in client.posts)
        )
        self.assertTrue(
            all(set(p["headers"]) == {"Content-Type"} for p in client.posts)
        )

    def test_all_registered_channels_have_an_explicit_discovery_policy(self) -> None:
        registered = (
            set(NEWS_SOURCES)
            | set(INVENTORY_SOURCES)
            | set(CUSTOM_SOURCES)
            | set(CATALOGUE_ONLY_POLICIES)
        )
        self.assertEqual(registered, {p.provider_id for p in ALL_PROVIDERS})
        result = read_publications(
            "openrouter",
            MappingClient({}),
            datetime.fromisoformat("2026-10-01T12:00:00+08:00"),
        )
        self.assertEqual(result["status"], "catalogue_only")

    def test_qwen_reads_the_verified_public_retrieval_contract(self) -> None:
        payload = {
            "success": True,
            "data": {
                "articles": [
                    {
                        "id": "internal-uuid",
                        "path": "qwen9-max-preview",
                        "title": "Introducing Qwen9-Max-Preview",
                        "content": "<h1>Qwen9-Max-Preview</h1><p>Qwen9-Max-Preview is available only to approved developers.</p>",
                        "extra": {
                            "date": "2026-09-30T04:00:00+08:00",
                            "description": "A reasoning model.",
                        },
                    }
                ]
            },
        }
        result = read_publications(
            "aliyun",
            MappingClient({QWEN_BLOG_API: json.dumps(payload)}),
            datetime.fromisoformat("2026-10-01T12:00:00+08:00"),
        )
        item = result["models"][0]
        self.assertEqual(
            item["source_url"], "https://qwen.ai/blog?id=qwen9-max-preview"
        )
        self.assertEqual(item["published_at"], "2026-09-30T04:00:00+08:00")
        self.assertEqual(item["access"]["developers"], "limited")

    def test_failed_source_keeps_its_coverage_and_provenance(self) -> None:
        spec = NEWS_SOURCES["google"]
        result = read_publications(
            "google",
            MappingClient({spec.index_url: SourceError("offline")}),
            datetime.fromisoformat("2026-10-01T12:00:00+08:00"),
        )
        self.assertEqual(result["status"], "source_error")
        self.assertEqual(result["source"]["url"], spec.url)
        self.assertEqual(result["coverage"], "recent_official_news")
        self.assertEqual(result["models"], [])


class BlogDiscoveryRegressionTests(unittest.TestCase):
    reference = datetime.fromisoformat("2026-10-13T12:00:00+08:00")

    def zhipu_client(self) -> MappingClient:
        return MappingClient({
            ZHIPU_BLOG_URL: LEGAL_SHELL,
            LEGAL_MODULE_URL: LEGAL_MODULE,
            INVENTORY_SOURCES["zhipu"].source_url: ZHIPU_OVERVIEW,
            ZHIPU_UPDATES_URL: ZHIPU_UPDATES,
        })

    def xiaomi_blog_client(self) -> MappingClient:
        return MappingClient(json.loads((FIXTURES / "xiaomi-blog-documents.json").read_text()))

    def test_unversioned_dashed_names_are_literal_subjects_not_compared_versions(self) -> None:
        self.assertEqual(model_names("GLM-Legal: Legal model beta", "GLM"), ["GLM-Legal"])
        self.assertEqual(model_names("Introducing GLM-Image and GLM-OCR", "GLM"), ["GLM-Image", "GLM-OCR"])
        self.assertEqual(model_names("How GLM Built Its Own Infrastructure", "GLM"), [])
        self.assertEqual(publication_names(
            "GLM-Legal: Legal model beta", "Built on GLM-5.3-Flash.", "GLM",
        ), ["GLM-Legal"])

    def test_coordinated_version_and_brand_shorthand_needs_no_model_list(self) -> None:
        self.assertEqual(publication_names(
            "GPT-9 Quartz, Opal, and Jade: For production agents",
            "Our latest models.", "GPT",
        ), ["GPT-9 Quartz", "GPT-9 Opal", "GPT-9 Jade"])
        self.assertEqual(publication_names(
            "GPT-9 Quartz and $1B in funding", "Our latest model.", "GPT",
        ), ["GPT-9 Quartz"])

    def test_compiled_article_keeps_inline_text_but_not_attributes_or_comparison_rates(self) -> None:
        details = compiled_article_details(LEGAL_MODULE)
        self.assertEqual(details["published_at"], "2026-10-12")
        self.assertIn("It combines legal reasoning with tools and document analysis.", details["paragraphs"])
        facts = release_facts("GLM-Legal", ["GLM-Legal"], details["paragraphs"])
        self.assertEqual(facts["access"]["developers"], "limited")
        self.assertEqual(facts["announced_offers"], [])

    def test_a_body_only_sibling_does_not_lend_its_access_to_the_title_subject(self) -> None:
        facts = release_facts("Kling 9.0", ["Kling 9.0"], [
            "Kling 9.0 is entering early access. Kling 9.0 Flash will be available to subscribers next month.",
        ])
        self.assertEqual(facts["access"]["status"], "limited")
        self.assertEqual(facts["access"]["consumers"], "unknown")
        self.assertNotIn("Flash", " ".join(facts["access"]["statements"]))

    def test_article_module_comes_from_the_shell_and_shared_runtime_is_never_fetched(self) -> None:
        client = self.zhipu_client()
        self.assertEqual(read_public_article(client, ZHIPU_BLOG_URL)["title"], "GLM-Legal: Legal model beta")
        self.assertEqual(client.calls, [ZHIPU_BLOG_URL, LEGAL_MODULE_URL])

    def test_compiled_article_rejects_dynamic_children_and_fake_metadata_inside_prose(self) -> None:
        for body in (
            LEGAL_MODULE.replace("children:`GLM-Legal is our new model for legal workflows.`", "children:runtimeClaim"),
            LEGAL_MODULE.replace("legal workflows.", "legal ${runtimeClaim} workflows."),
            'const fake = "date:`2026-10-12`,title:`GLM-Legal`";',
            '// date:`2026-10-12`,title:`GLM-Legal`\n',
        ):
            with self.subTest(body=body[:70]), self.assertRaises(SourceError):
                compiled_article_details(body)

    def test_quoted_jsx_examples_cannot_supply_article_paragraphs(self) -> None:
        example = 'const example = ".jsx)(t.p,{children:`GLM-Legal is available to all API users.`})";'
        details = compiled_article_details(LEGAL_MODULE + example)
        self.assertFalse(any("available to all" in paragraph for paragraph in details["paragraphs"]))

    def test_article_shell_rejects_foreign_modules_and_unreadable_bodies(self) -> None:
        for body in ("<h1>Documentation</h1>", LEGAL_SHELL.replace("/blog/assets/legal-current.js", "https://foreign.example/claim.js")):
            client = MappingClient({ZHIPU_BLOG_URL: body})
            with self.subTest(body=body), self.assertRaises(SourceError):
                read_public_article(client, ZHIPU_BLOG_URL)
            self.assertEqual(client.calls, [ZHIPU_BLOG_URL])

    def test_zhipu_blog_updates_and_inventory_are_independent_and_all_registered(self) -> None:
        client = self.zhipu_client()
        result = read_publications("zhipu", client, self.reference)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["coverage"], "official_news_and_inventory")
        legal = next(item for item in result["models"] if item["model_id"] == "GLM-Legal")
        self.assertEqual(legal["source_url"], ZHIPU_BLOG_URL)
        self.assertEqual(legal["identity_kind"], "published_name")
        self.assertEqual(legal["published_at"], "2026-10-12")
        self.assertEqual(legal["summary"], "GLM-Legal is our new model for legal workflows.")
        self.assertEqual(set(result["source"]["urls"]), {
            ZHIPU_BLOG_URL, ZHIPU_UPDATES_URL, INVENTORY_SOURCES["zhipu"].source_url,
        })
        self.assertEqual(len(client.calls), 4)

    def test_a_broken_component_keeps_all_other_readable_publications(self) -> None:
        for broken in (ZHIPU_BLOG_URL, ZHIPU_UPDATES_URL, INVENTORY_SOURCES["zhipu"].source_url):
            client = self.zhipu_client()
            client.pages[broken] = SourceError("offline")
            result = read_publications("zhipu", client, self.reference)
            with self.subTest(source=broken):
                self.assertEqual(result["status"], "source_error")
                self.assertTrue(result["models"])
                self.assertIn(broken, result["errors"][0])
                if broken != ZHIPU_BLOG_URL:
                    self.assertIn("GLM-Legal", [item["model_id"] for item in result["models"]])

    def test_new_google_cloud_feed_separates_hosted_launches_from_other_notes(self) -> None:
        spec = NEWS_SOURCES["google-cloud"]
        client = MappingClient({spec.index_url: (FIXTURES / "google-cloud-announcements.xml").read_text()})
        result = read_publications("google-cloud", client, self.reference)
        self.assertEqual(result["status"], "available")
        self.assertEqual([item["model_id"] for item in result["models"]], ["Claude Haiku 9.5", "Grok 9.7"])
        self.assertEqual(client.calls, ["https://docs.cloud.google.com/feeds/gemini-enterprise-agent-platform-release-notes.xml"])
        for item in result["models"]:
            self.assertEqual(item["published_at"], "2026-10-07T00:00:00-07:00")
            self.assertEqual(item["source_url"], spec.url + "#October_07_2026")
            self.assertNotIn("fine-tuning", " ".join(item["access"]["statements"]))

    def test_google_cloud_coordinated_and_availability_titles_are_model_publications(self) -> None:
        spec = NEWS_SOURCES["google-cloud"]
        feed = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>October 12, 2026</title>
        <link href="https://docs.cloud.google.com/gemini-enterprise-agent-platform/release-notes"/>
        <updated>2026-10-12</updated><content type="html"><![CDATA[
        <h3>Feature</h3><p>Gemini 9.6 Flash and 9.5 Flash-Lite are generally available</p>
        <p>Gemini 9.6 Flash and Gemini 9.5 Flash-Lite are available for production.</p>
        <h3>Feature</h3><p>Anthropic's Claude Fable 9.1 is now available</p><p>Claude Fable 9.1 is available in Model Garden.</p>
        ]]></content></entry></feed>'''
        result = read_publications("google-cloud", MappingClient({spec.index_url: feed}), self.reference)
        self.assertEqual([item["model_id"] for item in result["models"]], ["Gemini 9.6 Flash", "Gemini 9.5 Flash-Lite", "Claude Fable 9.1"])

    def test_rss_full_content_owns_access_even_when_its_teaser_is_generic(self) -> None:
        spec = NEWS_SOURCES["azure"]
        feed = '''<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item>
        <title>GPT-9 Quartz: In Microsoft Foundry</title><link>https://azure.microsoft.com/en-us/blog/quartz/</link>
        <pubDate>Mon, 12 Oct 2026 12:00:00 +0000</pubDate><description>Our latest Azure model.</description>
        <content:encoded><![CDATA[<p>GPT-9 Quartz is available only to selected API developers through Foundry.</p>]]></content:encoded>
        </item></channel></rss>'''
        result = read_publications("azure", MappingClient({spec.index_url: feed}), self.reference)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["models"][0]["access"]["developers"], "limited")

    def test_ant_blog_reads_structured_dates_and_release_tags_without_guest_posts(self) -> None:
        spec = NEWS_SOURCES["ant-ling"]
        posts = [{"post": post} for post in (
            {"slug": "ling-9-release", "title": "Ling-9-flash: More Useful Work", "date": "2026-10-12", "tags": ["Model Release"], "summary": "A new model."},
            {"slug": "guest", "title": "Ling-9-flash for AI Agents", "date": "2026-10-12", "tags": ["Insights"], "summary": "A guest deployment guide."},
        )]
        index = '<script>self.__next_f.push([1,' + json.dumps(json.dumps(posts)) + '])</script>'
        url = spec.url + "ling-9-release/"
        client = MappingClient({spec.index_url: index, url: "<h1>Ling-9-flash</h1><p>Ling-9-flash is a reasoning model.</p>"})
        result = read_publications("ant-ling", client, self.reference)
        self.assertEqual(result["status"], "available")
        self.assertEqual([item["model_id"] for item in result["models"]], ["Ling-9-flash"])
        self.assertEqual(result["models"][0]["published_at"], "2026-10-12")
        self.assertEqual(client.calls, [spec.index_url, url])

    def test_seed_blog_paginates_past_old_pinned_posts_and_reuses_its_inventory(self) -> None:
        model_url = "https://seed.bytedance.com/en/blog/spatial-release"
        def entry(title: str, slug: str, when: str) -> dict[str, Any]:
            return {
                "ArticleMeta": {"StatusEn": 2, "PublishDate": int(datetime.fromisoformat(when).timestamp() * 1000)},
                "ArticleSubContentEn": {"Title": title, "TitleKey": slug, "Abstract": "A model."},
            }
        fresh = entry("Introducing SeedSpatial", "spatial-release", "2026-10-12T00:00:00+08:00")
        inventory = {"loaderData": {"layout": {"footer_config": [{"titleEn": "Models", "content": [{"labelEn": "SeedSpatial", "linkEn": "/en/spatial"}]}]}}}
        article = {"loaderData": {"(locale$)/blog/(id)/page": {"data": {"article": {
            "SubArticle": fresh, "ContentEn": "<p>Today we introduce SeedSpatial for spatial reasoning.</p>",
        }}}}}
        client = MappingClient({
            SEED_MODELS_URL: '<script>window._ROUTER_DATA = ' + json.dumps(inventory) + '</script>',
            seed_blog_url(): json.dumps({"BaseResp": {"StatusCode": 0}, "sub_article_list": [entry("Introducing Seed1", "old", "2025-01-01T00:00:00+08:00")], "has_more": True, "next_page_token": "20"}),
            seed_blog_url("20"): json.dumps({"BaseResp": {"StatusCode": 0}, "sub_article_list": [fresh], "has_more": True, "next_page_token": "40"}),
            seed_blog_url("40"): json.dumps({"BaseResp": {"StatusCode": 0}, "has_more": False, "next_page_token": ""}),
            model_url: '<script>window._ROUTER_DATA = ' + json.dumps(article) + '</script>',
        })
        result = read_publications("volcengine", client, self.reference)
        self.assertEqual(result["status"], "available")
        blog = next(item for item in result["models"] if item["source_url"] == model_url)
        self.assertEqual(blog["model_id"], "SeedSpatial")
        self.assertEqual(blog["published_at"], "2026-10-11T16:00:00+00:00")
        self.assertEqual(client.calls.count(SEED_MODELS_URL), 1)
        self.assertNotIn("https://seed.bytedance.com/en/blog/old", client.calls)

    def test_seed_missing_initial_rows_or_repeated_pagination_are_source_errors(self) -> None:
        for payload in (
            {"BaseResp": {"StatusCode": 0}, "has_more": False},
            {"BaseResp": {"StatusCode": 0}, "sub_article_list": [{"ArticleMeta": {"StatusEn": 1}}], "has_more": True, "next_page_token": "0"},
        ):
            client = MappingClient({seed_blog_url(): json.dumps(payload)})
            result = read_publications("volcengine", client, self.reference)
            with self.subTest(payload=payload):
                self.assertEqual(result["status"], "source_error")
                self.assertTrue(any("Seed official blog" in error for error in result["errors"]))

    def test_seed_uses_the_official_subject_label_instead_of_headline_descriptors(self) -> None:
        article_url = "https://seed.bytedance.com/en/blog/audio-release"
        entry = {
            "ArticleMeta": {"StatusEn": 2, "PublishDate": int(self.reference.timestamp() * 1000)},
            "ArticleSubContentEn": {"Title": "Introducing Seed Audio 9.0 Audio Creation Model", "ImageTitle": "Seed Audio 9.0", "TitleKey": "audio-release"},
        }
        article = {"loaderData": {"(locale$)/blog/(id)/page": {"data": {"article": {
            "SubArticle": entry, "ContentEn": "<p>Seed Audio 9.0 creates speech and sound.</p>",
        }}}}}
        client = MappingClient({
            seed_blog_url(): json.dumps({"BaseResp": {"StatusCode": 0}, "sub_article_list": [entry], "has_more": False}),
            article_url: '<script>window._ROUTER_DATA = ' + json.dumps(article) + '</script>',
            SEED_MODELS_URL: SourceError("inventory unavailable"),
        })
        result = read_publications("volcengine", client, self.reference)
        self.assertEqual([item["model_id"] for item in result["models"]], ["Seed Audio 9.0"])
        self.assertEqual(result["status"], "source_error")

    def test_xiaomi_update_bullets_publish_literal_variants_without_overview_or_prices(self) -> None:
        updates = '''<h1>Model Release</h1><h2>2026-10-12 MiMo-V9 Series Release</h2>
        <ul><li>mimo-v9-pro: A reasoning model.</li><li>mimo-v9-flash: A fast multimodal model.</li></ul>
        <h2>2025-01-01 MiMo-V1 Release</h2><p>An older release.</p>'''
        client = MappingClient({
            XIAOMI_UPDATES_URL: updates,
            INVENTORY_SOURCES["xiaomi"].source_url: SourceError("overview unavailable"),
        })
        result = read_publications("xiaomi", client, self.reference)
        self.assertEqual([item["model_id"] for item in result["models"]], ["mimo-v9-pro", "mimo-v9-flash"])
        self.assertTrue(all(item["published_at"] == "2026-10-12" for item in result["models"]))
        self.assertTrue(all(item["announced_offers"] == [] for item in result["models"]))
        self.assertEqual(result["status"], "source_error")

    def test_xiaomi_blog_finds_unpriced_model_and_preserves_independent_inventory_failure(self) -> None:
        client = self.xiaomi_blog_client()
        client.pages.update({
            INVENTORY_SOURCES["xiaomi"].source_url: SourceError("overview unavailable"),
            XIAOMI_UPDATES_URL: '<h2>2026-10-11 MiMo-V8 Release</h2><p>MiMo-V8 is a text model.</p>',
        })
        result = read_publications("xiaomi", client, self.reference)
        self.assertEqual([item["model_id"] for item in result["models"]], ["MiMo-V9", "MiMo-V8"])
        self.assertEqual(result["status"], "source_error")
        self.assertEqual(len(result["errors"]), 1)
        model = result["models"][0]
        self.assertEqual(model["source_url"], XIAOMI_BLOG_URL + "blog/new")
        self.assertEqual(model["published_at"], "2026-10-12")
        self.assertEqual(model["access"]["developers"], "public")
        self.assertEqual(model["announced_offers"], [])
        self.assertEqual(model["summary"], "MiMo-V9 is a reasoning model.")
        self.assertEqual(set(result["source"]["urls"]), {
            XIAOMI_BLOG_URL, INVENTORY_SOURCES["xiaomi"].source_url, XIAOMI_UPDATES_URL,
        })
        self.assertFalse(any("old" in url or "tutorial" in url or "lib-react" in url for url in client.calls))
        self.assertEqual(len(client.calls), len(set(client.calls)))

    def test_xiaomi_blog_success_composes_with_overview_and_model_updates(self) -> None:
        client = self.xiaomi_blog_client()
        client.pages.update({
            INVENTORY_SOURCES["xiaomi"].source_url: '<table><tr><th>模型 ID</th><th>能力支持</th></tr><tr><td>mimo-v8</td><td>Text reasoning.</td></tr></table>',
            XIAOMI_UPDATES_URL: '<h2>2026-10-11 MiMo-V8 Release</h2><p>MiMo-V8 is a text model.</p>',
        })
        result = read_publications("xiaomi", client, self.reference)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["coverage"], "official_news_and_inventory")
        self.assertEqual([item["model_id"] for item in result["models"]], ["MiMo-V9", "mimo-v8", "MiMo-V8"])

    def test_compiled_blog_cards_ignore_quotes_comments_and_foreign_links(self) -> None:
        text = '''const example = 'blogs:[{title:"Introducing MiMo-V1",link:"/fake"}]';
        // blogs:[{title:"Introducing MiMo-V2",link:"/fake"}]
        const index = {blogs:[{title:"Introducing MiMo-V9",link:"/blog/current",desc:"A reasoning model."},
        {title:"A paper",link:"https://foreign.example/paper",desc:"Research"}]};'''
        entries = compiled_blog_entries(text, XIAOMI_BLOG_URL)
        self.assertEqual([entry["title"] for entry in entries], ["Introducing MiMo-V9"])
        self.assertIsNone(entries[0]["published_at"])

    def test_compiled_blog_cards_reject_computed_metadata_and_unreadable_card_lists(self) -> None:
        for body in (
            'const index={blogs:[{title:"MiMo-V9"+runtimeTitle,link:"/blog/current"}]};',
            'const index={blogs:[{title:"MiMo-V9",link:`/blog/${runtimePath}`}]};',
            'const index={blogs:[runtimeCard]};',
            'const index={blogs:[{title:"MiMo-V9"}]};',
        ):
            with self.subTest(body=body), self.assertRaises(SourceError):
                compiled_blog_entries(body, XIAOMI_BLOG_URL)

    def test_rspress_blog_dates_are_scoped_to_article_routes_and_assets_read_once(self) -> None:
        client = self.xiaomi_blog_client()
        entries = read_rspress_blog_entries(
            client, XIAOMI_BLOG_URL, asset_base=XIAOMI_BLOG_ASSETS, file_path="en/index.mdx",
        )
        self.assertEqual([entry["published_at"] for entry in entries], ["2026-10-12", "2025-01-01", None])
        self.assertEqual(len(client.calls), 4)
        self.assertEqual(len(client.calls), len(set(client.calls)))

    def test_rspress_blog_rejects_a_changed_deployment_or_foreign_asset_host(self) -> None:
        for mode in ("deployment", "script_host", "chunk"):
            client = self.xiaomi_blog_client()
            runtime = XIAOMI_BLOG_ASSETS + "static/js/index.current.js"
            if mode == "deployment":
                client.pages[runtime] = client.pages[runtime].replace(XIAOMI_BLOG_ASSETS, "https://foreign.example/")
            elif mode == "script_host":
                client.pages[XIAOMI_BLOG_URL] = client.pages[XIAOMI_BLOG_URL].replace(XIAOMI_BLOG_ASSETS, "https://foreign.example/")
            else:
                client.pages[runtime] = client.pages[runtime].replace('222:"cd34"', '333:"cd34"')
            with self.subTest(mode=mode), self.assertRaises(SourceError):
                read_rspress_blog_entries(client, XIAOMI_BLOG_URL, asset_base=XIAOMI_BLOG_ASSETS, file_path="en/index.mdx")
            self.assertFalse(any("foreign.example" in url for url in client.calls))

    def test_public_article_rejects_foreign_recursive_and_unreadable_frames(self) -> None:
        url = XIAOMI_BLOG_URL + "blog/current"
        for frame in ("https://foreign.example/article.html", url, "/article.html"):
            client = MappingClient({url: '<iframe src="' + frame + '"></iframe>', XIAOMI_BLOG_URL + "article.html": "<p>No model article</p>"})
            with self.subTest(frame=frame), self.assertRaises(SourceError):
                read_public_article(client, url)
            self.assertFalse(any("foreign.example" in target for target in client.calls))

    def test_kimi_ai_uses_dated_article_cards_and_ignores_navigation_and_external_papers(self) -> None:
        items = [
            {"title": "Kimi K9", "href": "/blog/kimi-k9", "date": "2026-10-12"},
            {"title": "Kimi K1", "href": "/blog/kimi-k1", "date": "2025-01-01"},
            {"title": "Kimi Research Paper", "href": "https://papers.example/research", "date": "2026-10-12"},
        ]
        payload = {"articleList": {"items": items}}
        index = '<nav><a href="/blog/kimi-k1">Kimi K1</a></nav><script>self.__next_f.push([1,' + json.dumps(json.dumps(payload)) + '])</script>'
        url = "https://www.kimi.ai/blog/kimi-k9"
        client = MappingClient({KIMI_BLOG_URL: index, url: "<h1>Kimi K9</h1><p>Kimi K9 is a reasoning model.</p>"})
        result = read_publications("kimi", client, self.reference)
        self.assertEqual(result["status"], "available")
        self.assertEqual([item["model_id"] for item in result["models"]], ["Kimi K9"])
        self.assertEqual(result["models"][0]["published_at"], "2026-10-12")
        self.assertEqual(client.calls, [KIMI_BLOG_URL, url])

    def test_kling_reads_only_its_capability_maps_and_never_requests_the_blog(self) -> None:
        # The blog index is readable but every article behind it is a client-rendered
        # shell, so the registered release source could never prove a subject. Kling
        # therefore keeps the two capability maps and no news index at all.
        self.assertNotIn("kling", NEWS_SOURCES)
        source = INVENTORY_SOURCES["kling"]
        video, image = source.source_urls
        client = MappingClient({
            video: "| 模型 | 描述 |\n| --- | --- |\n| Kling 1.0 | 旧模型 |\n",
            image: "| 模型 | 描述 |\n| --- | --- |\n| Kling Image 1.0 | 旧图像模型 |\n",
        })
        result = read_publications("kling", client, self.reference)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["coverage"], "official_model_inventory")
        self.assertEqual(
            sorted(item["model_id"] for item in result["models"]),
            ["Kling 1.0", "Kling Image 1.0"],
        )
        self.assertEqual(result["source"]["url"], source.source_url)
        self.assertNotIn("https://klingai.com/global/blog", client.calls)

    def test_a_channel_note_names_only_the_sources_it_actually_reads(self) -> None:
        inventory_only = discovery_policy("kling")
        self.assertEqual(inventory_only["coverage"], "official_model_inventory")
        self.assertNotIn("新闻读取", inventory_only["note"])
        self.assertIn("能力目录", inventory_only["note"])
        self.assertIn("新闻读取", discovery_policy("openai")["note"])

    def test_partial_composite_failure_preserves_archive_then_reports_recovered_access(self) -> None:
        class ZhipuAdapter(Adapter):
            provider_id = "zhipu"
            provider_name = "智谱"
        client = self.zhipu_client()
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            def scan(at: str) -> dict[str, Any]:
                return scan_announcements(
                    ZhipuAdapter(), client, [], store, at,
                    parse_baseline_selection(None), datetime.fromisoformat(at),
                )
            first = scan("2026-10-13T12:00:00+08:00")
            self.assertEqual(first["status"], "baseline_created")
            self.assertEqual(first["model_count"], 2)
            legal = next(item for item in first["models"] if item["model_id"] == "GLM-Legal")
            self.assertEqual(legal["catalog_status"], "not_listed")
            client.pages[LEGAL_MODULE_URL] = SourceError("offline")
            failed = scan("2026-10-13T13:00:00+08:00")
            self.assertEqual(failed["status"], "source_error")
            self.assertEqual(store.read("announcements-zhipu")["captured_at"], "2026-10-13T12:00:00+08:00")
            self.assertIn("GLM-Legal", [item["model_id"] for item in failed["models"]])
            client.pages[LEGAL_MODULE_URL] = LEGAL_MODULE.replace("Apply for API access: ", "GLM-Legal is generally available via its API. ")
            recovered = scan("2026-10-13T14:00:00+08:00")
            self.assertEqual(recovered["status"], "changed")
            self.assertEqual([change["kind"] for change in recovered["changes"]], ["access_changed"])


class AdditionalEvidenceTests(unittest.TestCase):
    def test_the_summary_identifies_a_variant_abbreviated_in_the_title(self) -> None:
        self.assertEqual(
            publication_names(
                "Introducing GPT-6 Sol and Luna",
                "GPT-6 Sol and GPT-6 Luna are our new models.",
                "GPT",
            ),
            ["GPT-6 Sol", "GPT-6 Luna"],
        )

    def test_an_imprecise_promotion_period_remains_a_sentence(self) -> None:
        note = "Grok 9 promotional pricing is available at least through December 31, 2026."
        facts = release_facts("Grok 9", ["Grok 9"], [note])
        self.assertEqual(facts["pricing_notes"], [note])
        self.assertEqual(facts["announced_offers"], [])

    def test_available_training_data_and_weights_do_not_establish_api_access(
        self,
    ) -> None:
        name = "Qwen-Drive-1.0"
        facts = release_facts(
            name,
            [name],
            [
                "Qwen-Drive-1.0 is a research model. Its training uses publicly available data.",
                "Qwen-Drive-1.0 weights are available on Hugging Face.",
                "The complete project trace is publicly available in a repository.",
            ],
        )
        self.assertEqual(facts["access"]["status"], "unknown")
        self.assertEqual(facts["access"]["statements"], [])

    def test_publication_dates_read_the_public_indexes_epoch_seconds(self) -> None:
        from model_price.announcements.parsing import published_date

        self.assertEqual(published_date(1783440000), "2026-07-07T16:00:00+00:00")

    def test_blog_trailer_cannot_override_a_limited_access_program(self) -> None:
        page = "<h1>GPT-6 Astra</h1><p>GPT-6 Astra begins rolling out today through our Limited Access Program.</p><p>The post GPT-6 Astra: now generally available appeared first on Microsoft Azure Blog.</p>"
        facts = release_facts(
            "GPT-6 Astra", ["GPT-6 Astra"], article_details(page)["paragraphs"]
        )
        self.assertEqual(facts["access"]["status"], "limited")

    def test_openai_uses_canonical_article_paths_with_typeset_model_names(self) -> None:
        spec = NEWS_SOURCES["openai"]
        url = "https://openai.com/index/model-release"
        feed = rss([("Introducing GPT-9.1", url, "Introducing GPT-9.1, available now.")])
        page = '<h1>Introducing GPT‑9.1</h1><p>GPT‑9.1 is available today.</p>'
        client = MappingClient({spec.index_url: feed, url + "/": page})
        result = read_publications(
            "openai", client, datetime.fromisoformat("2026-10-01T12:00:00+08:00")
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(client.calls, [spec.index_url, url + "/"])
        self.assertEqual(result["models"][0]["access"]["status"], "public")
        self.assertFalse(mentions_model("GPT‑9.1‑Mini is available.", "GPT-9.1"))

    def test_an_unversioned_customer_story_does_not_release_its_compared_models(
        self,
    ) -> None:
        spec = NEWS_SOURCES["openai"]
        feed = rss(
            [
                (
                    "Replit",
                    "https://openai.com/index/replit/",
                    "Replit launches apps using GPT-6 Astra.",
                )
            ]
        )
        client = MappingClient({spec.index_url: feed})
        result = read_publications(
            "openai", client, datetime.fromisoformat("2026-10-01T12:00:00+08:00")
        )
        self.assertEqual(result["models"], [])
        self.assertEqual(client.calls, [spec.index_url])

    def test_a_comparison_list_does_not_add_a_shorter_generation_subject(self) -> None:
        self.assertEqual(
            publication_names(
                "Qwen3.8-Flash-Next: A New Architecture",
                "Our earlier design was used across Qwen3.7 and Qwen3.8 series.",
                "Qwen",
            ),
            ["Qwen3.8-Flash-Next"],
        )

    def test_currency_case_never_relabels_a_usd_quote_as_cny(self) -> None:
        facts = release_facts(
            "Grok 9", ["Grok 9"], ["Grok 9 pricing is usd 2 per million input tokens."]
        )
        self.assertEqual(
            facts["announced_offers"][0]["prices"][0]["unit"], "USD_per_million_tokens"
        )


class AnnouncementScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = SnapshotStore(Path(self.temporary.name))
        self.spec = NEWS_SOURCES["google"]
        self.client = MappingClient(
            {self.spec.index_url: ARGON_FEED, ARGON_URL: ARGON_PAGE}
        )
        self.now = "2026-10-01T12:00:00+08:00"

    def scan(
        self, adapter: Adapter | None = None, *, at: str | None = None, **options: Any
    ) -> dict[str, Any]:
        return scan_providers(
            [adapter or Adapter()],
            self.store,
            captured_at=at or self.now,
            announcement_client=self.client,
            **options,
        )

    def test_first_scan_shows_announced_argon_capabilities_and_future_prices(
        self,
    ) -> None:
        payload = self.scan()
        report = payload["providers"][0]
        self.assertEqual(report["status"], "baseline_created")
        self.assertEqual(report["announcements"]["status"], "baseline_created")
        self.assertEqual(report["model_availability"]["Gemini 4 Argon"], "announced")
        self.assertEqual(report["changes"]["models_added"], [])
        message = scan_message(payload, max_chars=100000)
        self.assertLess(message.index("【模型能力】"), message.index("【模型价格】"))
        for text in [
            "官方公布·首次收录",
            "开发者：尚待开放",
            "普通用户：尚待开放",
            "输入 2 美元/百万 tokens",
            "输入 4 美元/百万 tokens",
            "95% off",
            ARGON_URL,
        ]:
            self.assertIn(text, message)
        self.assertNotIn("新增上架", message)
        stored = self.store.read("announcements-google")
        self.assertEqual(
            stored["announcement_schema_version"], ANNOUNCEMENT_SCHEMA_VERSION
        )
        self.assertNotIn("gemini-4-argon", self.store.read("google")["models"])

    def test_existing_price_baseline_cannot_hide_a_first_announcement_read(
        self,
    ) -> None:
        scan_providers([Adapter()], self.store, captured_at="2026-09-29T12:00:00+08:00")
        report = self.scan()["providers"][0]
        self.assertEqual(report["status"], "unchanged")
        self.assertEqual(channel_status(report), "changed")
        self.assertEqual(
            report["announcements"]["observations"][0]["kind"],
            "announcement_observed",
        )
        self.assertEqual(report["announcements"]["changes"], [])

    def test_unchanged_release_and_rolling_index_omission_never_reannounce_or_delist(
        self,
    ) -> None:
        self.scan()
        second = self.scan(at="2026-10-01T13:00:00+08:00")["providers"][0]
        self.assertEqual(second["announcements"]["status"], "unchanged")
        self.client.pages[self.spec.index_url] = rss(
            [("Company news", "https://blog.google/company-news/new/", "News.")]
        )
        third = self.scan(at="2026-10-01T14:00:00+08:00")["providers"][0]
        self.assertEqual(third["announcements"]["changes"], [])
        self.assertEqual(
            third["announcements"]["models"][0]["model_id"], "Gemini 4 Argon"
        )

    def test_price_publication_and_public_access_are_separate_simultaneous_causes(
        self,
    ) -> None:
        self.scan()
        opened = ARGON_PAGE.replace(
            "rolling out to a set of trusted cyber defenders through our Fairwind Program",
            "available today to all developers and consumers",
        )
        opened = opened.replace(
            "We’ll continue to gather feedback from early testers before making Argon available to developers, enterprises, and consumers as soon as possible.",
            "Gemini 4 Argon is available today to all developers and consumers.",
        )
        self.client.pages[ARGON_URL] = opened
        adapter = Adapter([record("gemini-3.8-flash"), record("gemini-4-argon")])
        report = self.scan(adapter, at="2026-10-02T12:00:00+08:00")["providers"][0]
        groups = changed_model_groups(report)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["model_id"], "gemini-4-argon")
        self.assertEqual(
            set(groups[0]["change_kinds"]),
            {"models_added", "access_changed", "announcement_listing_changed"},
        )
        self.assertEqual(report["model_availability"]["gemini-4-argon"], "listed")
        self.assertEqual(
            self.store.read("announcements-google")["models"]["gemini-4-argon"][
                "access"
            ]["status"],
            "public",
        )

    def test_unpriced_listing_is_preserved_separately_from_published_announcement(
        self,
    ) -> None:
        self.scan()
        adapter = Adapter(
            [record("gemini-3.8-flash"), record("gemini-4-argon", priced=False)]
        )
        report = self.scan(adapter, at="2026-10-02T12:00:00+08:00")["providers"][0]
        self.assertEqual(
            report["changes"]["models_added"][0]["price_status"], "unknown"
        )
        self.assertEqual(report["changes"]["models_added"][0]["offers"], [])
        self.assertTrue(report["announcements"]["models"][0]["announced_offers"])

    def test_source_failure_retains_release_history_but_price_scans_keep_advancing(
        self,
    ) -> None:
        self.scan()
        before = copy.deepcopy(self.store.read("announcements-google"))
        self.client.pages[self.spec.index_url] = SourceError("503 Service Unavailable")
        payload = self.scan(at="2026-10-02T12:00:00+08:00")
        report = payload["providers"][0]
        self.assertEqual(report["status"], "unchanged")
        self.assertEqual(report["announcements"]["status"], "source_error")
        self.assertEqual(self.store.read("announcements-google"), before)
        self.assertEqual(
            self.store.read("google")["captured_at"], "2026-10-02T12:00:00+08:00"
        )
        self.assertIn("模型发布来源：读取失败", scan_message(payload))
        self.assertEqual(payload["summary"]["announcement_source_errors"], 1)

    def test_price_failure_does_not_suppress_readable_announcement_or_assume_listing(
        self,
    ) -> None:
        payload = self.scan(Adapter(SourceError("pricing unavailable")))
        report = payload["providers"][0]
        self.assertEqual(report["status"], "source_error")
        self.assertEqual(report["model_availability"]["Gemini 4 Argon"], "unknown")
        self.assertIsNone(self.store.read("google"))
        self.assertIsNotNone(self.store.read("announcements-google"))
        self.assertIn("Gemini 4 Argon", scan_message(payload))

    def test_partial_article_failure_does_not_erase_index_evidence_or_write_a_baseline(
        self,
    ) -> None:
        self.client.pages[ARGON_URL] = SourceError("article unavailable")
        payload = self.scan()
        report = payload["providers"][0]
        self.assertEqual(report["announcements"]["status"], "source_error")
        self.assertIsNone(self.store.read("announcements-google"))
        self.assertIsNotNone(self.store.read("google"))
        self.assertIn("Gemini 4 Argon", scan_message(payload))
        self.assertEqual(
            report["model_descriptions"][0]["source"]["url"], self.spec.index_url
        )

    def test_historical_selection_without_a_release_baseline_does_not_invent_a_delta(
        self,
    ) -> None:
        report = self.scan(baseline=parse_baseline_selection("2026-09-01"))[
            "providers"
        ][0]
        self.assertEqual(report["announcements"]["status"], "baseline_not_found")
        self.assertEqual(report["announcements"]["changes"], [])
        self.assertEqual(
            report["announcements"]["observations"][0]["kind"], "announcement_observed"
        )
        payload = {"providers": [report], "summary": {"providers": 1}}
        message = scan_message(payload, max_chars=100000)
        self.assertIn("Gemini 4 Argon", message)
        self.assertIn("官方公布·首次收录", message)
        self.assertIn("输入 2 美元/百万 tokens", message)
        self.assertNotIn("新增公布", message)
        self.assertIsNotNone(self.store.read("announcements-google"))

    def test_a_missing_historical_baseline_does_not_repeat_recorded_observations(
        self,
    ) -> None:
        baseline = parse_baseline_selection("2026-09-01")
        self.scan(baseline=baseline)
        report = self.scan(baseline=baseline, at="2026-10-02T12:00:00+08:00")[
            "providers"
        ][0]
        self.assertEqual(report["announcements"]["status"], "baseline_not_found")
        self.assertEqual(report["announcements"]["changes"], [])
        self.assertEqual(report["announcements"]["observations"], [])

    def test_direct_announcement_introduction_never_asks_an_api_reader_to_guess_a_page(
        self,
    ) -> None:
        class RejectingSource(DescriptionSource):
            source_id = "google"
            source_name = "Google"
            source_url = "https://ai.google.dev/models"
            source_kind = "test"

            def describe(self, *args: Any, **kwargs: Any) -> None:
                raise AssertionError("must not guess an API ID from a published name")

        resolver = DescriptionResolver({"google": RejectingSource(object())})
        report = self.scan(descriptions=resolver)["providers"][0]
        self.assertEqual(report["model_descriptions"][0]["status"], "available")
        self.assertEqual(report["model_descriptions"][0]["provider"]["id"], "google")

    def test_message_budget_does_not_remove_announced_models_prices_or_terms(
        self,
    ) -> None:
        payload = self.scan()
        full = scan_message(payload, max_chars=100000)
        limited = scan_message(payload, max_chars=100)
        self.assertTrue(limited.startswith(full))
        self.assertIn(REGULAR, limited)
        self.assertIn("发送前需总结", limited)

    def test_price_terms_rewording_does_not_report_a_price_movement(self) -> None:
        self.scan()
        before = copy.deepcopy(self.store.read("announcements-google"))
        current = copy.deepcopy(before["models"])
        event = current["gemini-4-argon"]
        event["pricing_notes"] = ["The same promotion explained differently."]
        event["announced_offers"][0]["conditions"][
            "published_terms"
        ] = "The same rate in different words."
        self.assertEqual(compare_announcements(before, current), [])

    def test_an_older_repeat_cannot_roll_a_newer_access_statement_backward(
        self,
    ) -> None:
        self.scan()
        self.client.pages[ARGON_URL] = ARGON_PAGE.replace(
            "2026-09-30", "2026-09-20"
        ).replace("trusted cyber defenders", "all developers and consumers")
        report = self.scan(at="2026-10-02T12:00:00+08:00")["providers"][0]
        self.assertEqual(report["announcements"]["changes"], [])
        self.assertEqual(
            report["announcements"]["models"][0]["access"]["status"], "limited"
        )

    def test_catalogue_removal_is_not_relabelled_as_a_first_announcement(self) -> None:
        self.scan(Adapter([record("gemini-4-argon")]))
        report = self.scan(Adapter(), at="2026-10-02T12:00:00+08:00")["providers"][0]
        self.assertEqual(report["model_availability"]["gemini-4-argon"], "delisted")
        removed = next(
            model
            for model in changed_model_groups(report)
            if model["model_id"] == "gemini-4-argon"
        )
        self.assertIn("models_removed", removed["change_kinds"])

    def test_price_only_change_keeps_the_known_restricted_access_evidence(self) -> None:
        current = record("gemini-4-argon")
        self.scan(Adapter([current]))
        changed = copy.deepcopy(current)
        changed["offers"][0]["prices"][0]["amount"] = "3"
        payload = self.scan(Adapter([changed]), at="2026-10-02T12:00:00+08:00")
        report = payload["providers"][0]
        self.assertEqual(report["announcements"]["changes"], [])
        message = scan_message(payload, max_chars=100000)
        self.assertIn("限定对象开放", message)
        self.assertIn("开发者：尚待开放", message)
        self.assertIn("输入 0.125 美元/百万 tokens → 3 美元/百万 tokens", message)


if __name__ == "__main__":
    unittest.main()
