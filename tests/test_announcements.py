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

from model_price.announcements.core import compare_announcements
from model_price.announcements.parsing import (
    article_details,
    feed_entries,
    model_names,
    publication_names,
    release_facts,
)
from model_price.announcements.sources import (
    CUSTOM_SOURCES,
    GOOGLE_BLOG_URL,
    INVENTORY_SOURCES,
    NEWS_SOURCES,
    QWEN_BLOG_API,
    SEED_MODELS_URL,
    read_publications,
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
        self.assertEqual(client.calls, [source.source_url])

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

    def test_all_eighteen_channels_have_an_explicit_discovery_policy(self) -> None:
        registered = (
            set(NEWS_SOURCES)
            | set(INVENTORY_SOURCES)
            | set(CUSTOM_SOURCES)
            | {"openrouter"}
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
            report["announcements"]["changes"][0]["kind"], "announcement_observed"
        )

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
