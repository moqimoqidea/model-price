"""Verify Mistral's public prices and independent publication/retirement evidence.

Reduced official captures exercise API aliases, the site's pricing controls, and
non-token charges offline, including missing rates and unavailable sources.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.announcements.sources import read_publications
from model_price.announcements.parsing import mentions_model
from model_price.caching import CacheStore, CachedPriceSource
from model_price.delta import scan_provider
from model_price.descriptions import DescriptionResolver
from model_price.descriptions.sources import MistralDescriptionSource
from model_price.errors import SourceError
from model_price.lifecycle import retired_model_ids
from model_price.lifecycle_sources import read_events
from model_price.messages import comparison_message, scan_message
from model_price.mistral_catalogue import (
    MISTRAL_LIFECYCLE_URL,
    MISTRAL_MODELS_URL,
    MISTRAL_NEWS_URL,
    MISTRAL_PRICING_URL,
    MODEL_BUNDLE_PATH,
    PRICING_BUNDLE_PATH,
    literal_at,
    model_entries,
    script_url,
)
from model_price.pricing import unit_code
from model_price.providers import ALL_PROVIDERS, OVERSEAS_PROVIDERS
from model_price.providers.mistral import MistralAdapter
from model_price.registry import (
    build_adapters,
    inferred_overseas_providers,
    query_adapters,
    select_catalog_providers,
    select_compare_providers,
)
from model_price.reporting import unit_label
from model_price.snapshots import SnapshotStore

FIXTURES = Path(__file__).with_name("fixtures")


def fixture(name: str) -> str:
    return (FIXTURES / f"mistral-{name}").read_text(encoding="utf-8")


MODELS = fixture("models.html")
PRICES = fixture("pricing.html")
MODEL_DATA = fixture("model-data.js")
CONTROLS = fixture("pricing-controls.js")
MODEL_DATA_URL = script_url(MODELS, MODEL_BUNDLE_PATH)
CONTROLS_URL = script_url(PRICES, PRICING_BUNDLE_PATH)
LARGE_4_NEWS_URL = f"{MISTRAL_NEWS_URL}mistral-large-4/"
NOW = "2026-10-07T12:00:00+08:00"


class DocumentClient:
    """Reject every unprovided URL so a test cannot silently access the network."""

    def __init__(self, responses: dict[str, str | Exception] | None = None) -> None:
        self.responses: dict[str, str | Exception] = {
            MISTRAL_MODELS_URL: MODELS,
            MISTRAL_PRICING_URL: PRICES,
            MODEL_DATA_URL: MODEL_DATA,
            CONTROLS_URL: CONTROLS,
            MISTRAL_LIFECYCLE_URL: fixture("lifecycle.md"),
            MISTRAL_NEWS_URL: fixture("news.html"),
            LARGE_4_NEWS_URL: fixture("large-4-news.html"),
        }
        self.responses.update(responses or {})
        self.requests: list[str] = []

    def get_text(self, url: str) -> str:
        self.requests.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


def offer(record: dict[str, Any], mode: str = "standard", scope: str = "default") -> dict[str, Any]:
    return next(
        item for item in record["offers"]
        if item["name"] == mode and item["conditions"]["inference_scope"] == scope
    )


class MistralPricingTests(unittest.TestCase):
    def test_catalogue_keeps_literal_api_aliases_and_reads_each_document_once(self) -> None:
        client = DocumentClient()
        adapter = MistralAdapter(client)
        models = adapter.list_models()
        self.assertIn("mistral-large-4", models)
        self.assertIn("mistral-large-4-0", models)
        self.assertIn("ministral-14b-2512", models)
        self.assertNotIn("ministral-3-14b", models)
        self.assertNotIn("zai-glm-5-2", models)
        self.assertNotIn("shieldstral-1-0", models)
        records = adapter.catalog_records()
        for record in records:
            self.assertEqual(adapter.query(record["model_id"])[0]["offers"], record["offers"])
        self.assertEqual(len(records), len(models))
        self.assertEqual(adapter.list_models("ministral-14b"), ["ministral-14b-2512", "ministral-14b-latest"])
        self.assertEqual(set(client.requests), {MISTRAL_PRICING_URL, MISTRAL_MODELS_URL, MODEL_DATA_URL, CONTROLS_URL})
        self.assertEqual(len(client.requests), 4)

    def test_sale_prices_and_original_amounts_are_kept_without_guessed_terms(self) -> None:
        record = MistralAdapter(DocumentClient()).query("mistral-large-4")[0]
        prices = offer(record)["prices"]
        self.assertEqual([p["type"] for p in prices], ["input", "cache_hit", "output"])
        self.assertEqual([p["amount"] for p in prices], ["0.68", "0.07", "2.09"])
        self.assertEqual([p["list_amount"] for p in prices], ["1.36", "0.14", "4.18"])
        self.assertTrue(all("discount" not in p and "effective_until" not in p for p in prices))
        self.assertTrue(all(p["unit"] == "USD_per_million_tokens" for p in prices))
        self.assertNotIn("price_scope", offer(record)["conditions"])

    def test_each_pricing_control_retains_its_own_offer_and_exact_arithmetic(self) -> None:
        record = MistralAdapter(DocumentClient()).query("mistral-large-4")[0]
        self.assertEqual(len(record["offers"]), 6)
        for mode, scope, amount, listed in (
            ("standard", "default", "0.68", "1.36"),
            ("batch", "default", "0.34", "0.68"),
            ("priority", "default", "1.19", "2.38"),
            ("standard", "regional", "0.748", "1.496"),
            ("batch", "regional", "0.374", "0.748"),
            ("priority", "regional", "1.309", "2.618"),
        ):
            with self.subTest(mode=mode, scope=scope):
                price = offer(record, mode, scope)["prices"][0]
                self.assertEqual((price["amount"], price["list_amount"]), (amount, listed))

    def test_new_control_factors_and_modes_are_read_without_hard_coded_rates(self) -> None:
        controls = CONTROLS.replace("factor:.5", "factor:.6")
        controls = controls.replace('let b=[', 'let b=[{value:"flex",label:e=>e.text("Flex"),factor:.8},')
        record = MistralAdapter(DocumentClient({CONTROLS_URL: controls})).query("mistral-large-4")[0]
        self.assertEqual(len(record["offers"]), 8)
        self.assertEqual(offer(record, "batch")["prices"][0]["amount"], "0.408")
        self.assertEqual(offer(record, "flex")["prices"][0]["amount"], "0.544")

    def test_non_token_prices_keep_pages_minutes_and_characters(self) -> None:
        adapter = MistralAdapter(DocumentClient())
        for model, amounts, unit in (
            ("mistral-ocr-4-1", ["4", "0.4"], "USD_per_thousand_pages"),
            ("voxtral-mini-2602", ["0.003", "0.0003"], "USD_per_minute"),
            ("voxtral-mini-tts-2603", ["0", "0", "16"], "USD_per_million_characters"),
        ):
            with self.subTest(model=model):
                prices = offer(adapter.query(model)[0])["prices"]
                self.assertEqual([p["amount"] for p in prices], amounts)
                self.assertTrue(all(p["unit"] == unit for p in prices))
        self.assertEqual(unit_code("$4 /1000 Pages", "USD"), "USD_per_thousand_pages")
        self.assertEqual(unit_label("USD_per_thousand_pages"), "美元/千页")

    def test_published_free_rates_and_missing_rates_remain_different(self) -> None:
        adapter = MistralAdapter(DocumentClient())
        free = offer(adapter.query("mistral-moderation-2603")[0])["prices"]
        self.assertTrue(all(p["amount"] == "0" and p["display"] == "Free" for p in free))
        self.assertTrue(all(p["unit"] == "provider_defined" for p in free))
        self.assertTrue(all(
            price["display"] == "Free"
            for item in adapter.query("mistral-moderation-2603")[0]["offers"]
            for price in item["prices"]
        ))
        self.assertEqual(adapter.query("mistral-embed")[0]["offers"], [])
        self.assertEqual(adapter.query("voxtral-small-2507")[0]["offers"], [])
        self.assertEqual(adapter.query("not-a-published-model"), [])

    def test_family_queries_also_match_official_display_names_without_guessing_ids(self) -> None:
        adapter = MistralAdapter(DocumentClient())
        records = adapter.search("Ministral 3 14B")
        self.assertEqual([record["model_id"] for record in records], ["ministral-14b-2512", "ministral-14b-latest"])
        self.assertEqual(adapter.search("Ministral 3 14B", exact=True), [])
        self.assertEqual(len(adapter.search("Mistral Medium 3.5")), 3)

    def test_only_the_published_currency_is_read(self) -> None:
        prices = PRICES.replace("$", "€")
        record = MistralAdapter(DocumentClient({MISTRAL_PRICING_URL: prices})).query("mistral-large-4")[0]
        self.assertEqual(record["offers"], [])

    def test_third_party_models_remain_hosted_by_mistral(self) -> None:
        record = MistralAdapter(DocumentClient()).query("zai-glm-5-3")[0]
        self.assertEqual(record["provider"]["id"], "mistral")
        self.assertEqual(record["delivery_mode"], "third_party_hosted")
        self.assertEqual(record["source"]["url"], MISTRAL_PRICING_URL)
        self.assertEqual(offer(record)["prices"][0]["amount"], "1.4")

    def test_an_unfamiliar_model_family_needs_no_parser_name_list(self) -> None:
        model = '{name:"Nebula 9",describe:e=>({description:e.text("A new model.")}),slug:"nebula-nine",status:"GA",identifiers:{apiNames:["nebula-api-9"]}}'
        data = MODEL_DATA.replace("const models=[", "const models=[" + model + ",")
        models = MODELS.replace("</main>", '<a href="/models/nebula-nine">Nebula 9</a></main>')
        row = '<tr><td><a href="/models/nebula-nine">Nebula 9 ↗</a></td><td>$0.25</td><td>$0.025</td><td>$1</td></tr>'
        prices = PRICES.replace("</tbody>", row + "</tbody>", 1)
        adapter = MistralAdapter(DocumentClient({MODEL_DATA_URL: data, MISTRAL_MODELS_URL: models, MISTRAL_PRICING_URL: prices}))
        self.assertEqual(offer(adapter.query("nebula-api-9")[0])["prices"][0]["amount"], "0.25")

    def test_unreadable_sources_never_masquerade_as_empty_catalogues(self) -> None:
        for url, text in (
            (MODEL_DATA_URL, "const models=[];"),
            (MISTRAL_MODELS_URL, '<html><script src="https://other.example/2894-fake.js"></script></html>'),
            (MISTRAL_PRICING_URL, "<h1>Pricing unavailable</h1>"),
            (CONTROLS_URL, "const modes=[];"),
        ):
            with self.subTest(url=url):
                with self.assertRaises(SourceError):
                    MistralAdapter(DocumentClient({url: text})).catalog_records()


class MistralEvidenceTests(unittest.TestCase):
    def test_preview_description_uses_this_hosts_literal_id_and_own_prose(self) -> None:
        source = MistralDescriptionSource(DocumentClient())
        item = source.describe("mistral-large-4")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item["lifecycle"], "preview")
        self.assertIn("52B active parameters", item["summary"])
        self.assertEqual(item["specifications"]["context_window"], "1M")
        self.assertEqual(item["source"]["url"], f"{MISTRAL_MODELS_URL}/mistral-large-4-0")
        self.assertIn("function-calling", item["capabilities"])
        self.assertIsNone(source.describe("mistral-large-4-fake", "Mistral Large 4"))

    def test_missing_descriptions_do_not_suppress_prices_and_long_prose_is_whole(self) -> None:
        data = MODEL_DATA.replace("description:e.text(", "description:e.missing(")
        client = DocumentClient({MODEL_DATA_URL: data})
        self.assertTrue(MistralAdapter(client).query("mistral-large-4")[0]["offers"])
        self.assertIsNone(MistralDescriptionSource(client).describe("mistral-large-4"))
        long_summary = "Mistral Large 4 is a multimodal model. " * 12
        entry = next(item for item in model_entries(MODEL_DATA) if item["name"] == "Mistral Large 4")
        data = MODEL_DATA.replace(json.dumps(entry["description"]), json.dumps(long_summary))
        description = MistralDescriptionSource(DocumentClient({MODEL_DATA_URL: data})).describe("mistral-large-4")
        assert description is not None
        self.assertEqual(description["summary"], long_summary.strip())
        self.assertTrue(description["summary_needs_condensing"])

    def test_bundle_properties_cannot_be_inferred_from_prose_or_executed(self) -> None:
        body = '{name:"Test",describe:e=>({description:e.text("status:GA, apiNames:[fake], contextLength:1M")}),slug:"test-page",status:"PublicPreview",identifiers:{apiNames:["test-api"]},contextLength:"32k"}'
        entry = model_entries(body)[0]
        self.assertEqual(entry["status"], "PublicPreview")
        self.assertEqual(entry["contextLength"], "32k")
        self.assertEqual(entry["identifiers"]["apiNames"], ["test-api"])
        with self.assertRaises(SourceError):
            literal_at("fetch('https://example.test')")

    def test_unreadable_introduction_properties_do_not_suppress_price_results(self) -> None:
        data = re.sub(r'capabilities:\{[^}]+\}', 'capabilities:unreadable()', MODEL_DATA)
        client = DocumentClient({MODEL_DATA_URL: data})
        resolver = DescriptionResolver({"mistral": MistralDescriptionSource(client)})
        payload = query_adapters([MistralAdapter(client)], "mistral-large-4", exact=True, descriptions=resolver)
        self.assertEqual(payload["source_checks"][0]["status"], "available")
        self.assertEqual(offer(payload["results"][0])["prices"][0]["amount"], "0.68")
        self.assertEqual(payload["model_descriptions"][0]["status"], "source_error")

    def test_notices_keep_announcement_shutdown_and_manual_replacement_separate(self) -> None:
        url, events = read_events("mistral", DocumentClient(), [])
        self.assertEqual(url, MISTRAL_MODELS_URL)
        item = next(e for e in events if e["model_id"] == "zai-glm-5-2")
        self.assertEqual((item["announced_at"], item["eos_at"]), ("2026-09-29", "2026-10-31"))
        self.assertEqual(item["replacement"], "Z.ai GLM 5.3")
        self.assertEqual(item["end_behavior"], "unavailable")
        self.assertIsNone(item["eom_at"])
        self.assertIsNone(item["redirect_at"])
        self.assertNotIn("zai-glm-5-2", retired_model_ids(events, datetime.fromisoformat(NOW)))
        self.assertIn("zai-glm-5-2", retired_model_ids(events, datetime.fromisoformat("2026-11-01T12:00:00+08:00")))

    def test_retired_status_with_no_date_is_evidence_and_ga_is_not_a_notice(self) -> None:
        events = read_events("mistral", DocumentClient(), [])[1]
        self.assertNotIn("mistral-large-4", {item["model_id"] for item in events})
        retired = next(entry for entry in model_entries(MODEL_DATA) if entry["status"] == "Retired")
        metadata = retired["metadata"]
        data = MODEL_DATA.replace('retirementDate:' + json.dumps(metadata["retirementDate"]), 'retirementDate:null')
        events = read_events("mistral", DocumentClient({MODEL_DATA_URL: data}), [])[1]
        self.assertIn(retired["identifiers"]["apiNames"][0], retired_model_ids(events, datetime.fromisoformat(NOW)))

    def test_prices_notices_and_introductions_share_one_public_inventory_read(self) -> None:
        client = DocumentClient()
        adapter = MistralAdapter(client)
        records = adapter.catalog_records()
        source = MistralDescriptionSource(client)
        for record in records:
            self.assertIsNotNone(source.describe(record["model_id"]))
        read_events("mistral", client, records)
        self.assertEqual(client.requests.count(MISTRAL_MODELS_URL), 1)
        self.assertEqual(client.requests.count(MODEL_DATA_URL), 1)

    def test_a_reassigned_live_alias_does_not_inherit_an_old_revisions_eos(self) -> None:
        data = MODEL_DATA.replace('apiNames:["zai-glm-5-2"]', 'apiNames:["zai-glm-5-2","zai-glm-latest"]')
        events = read_events("mistral", DocumentClient({MODEL_DATA_URL: data}), [])[1]
        self.assertIn("zai-glm-5-2", {item["model_id"] for item in events})
        self.assertNotIn("zai-glm-latest", {item["model_id"] for item in events})

    def test_aliases_absent_from_the_explicit_schedule_do_not_acquire_dates(self) -> None:
        data = MODEL_DATA.replace('apiNames:["labs-leanstral-2603"]', 'apiNames:["labs-leanstral-2603","leanstral-latest"]')
        events = read_events("mistral", DocumentClient({MODEL_DATA_URL: data}), [])[1]
        self.assertIn("labs-leanstral-2603", {item["model_id"] for item in events})
        self.assertNotIn("leanstral-latest", {item["model_id"] for item in events})

    def test_news_discovers_the_published_subject_and_public_preview_api(self) -> None:
        client = DocumentClient()
        result = read_publications("mistral", client, datetime.fromisoformat(NOW))
        self.assertEqual(result["status"], "available")
        self.assertEqual([item["model_id"] for item in result["models"]], ["Mistral Large 4"])
        model = result["models"][0]
        self.assertEqual(model["published_at"], "2026-10-06T12:00:27.000Z")
        self.assertIn("launching a public preview", model["summary"])
        self.assertNotIn("powerful AI platform for enterprises", model["summary"])
        self.assertEqual(model["access"]["status"], "public")
        self.assertEqual(model["access"]["developers"], "public")
        self.assertEqual(model["source_url"], LARGE_4_NEWS_URL)
        self.assertEqual(model["identity_kind"], "published_name")
        self.assertNotIn("https://mistral.ai/news/mistral-makes-sovereign-open-weight-ai-to-frontier/", client.requests)

    def test_sentence_punctuation_does_not_hide_a_model_or_admit_a_sibling(self) -> None:
        self.assertTrue(mentions_model("Introducing Mistral Large 4.", "Mistral Large 4"))
        self.assertFalse(mentions_model("Introducing Mistral Large 4.1.", "Mistral Large 4"))
        self.assertFalse(mentions_model("Introducing Mistral Large 4-preview.", "Mistral Large 4"))

    def test_unversioned_news_subjects_are_read_from_official_inventory(self) -> None:
        news = '<a href="/news/voxtral-tts/"><h3>Introducing Voxtral TTS</h3><p>October 6, 2026</p></a>'
        body = '<h1>Introducing Voxtral TTS</h1><p>Today we launch Voxtral TTS, our text-to-speech model, available via the API today.</p>'
        client = DocumentClient({MISTRAL_NEWS_URL: news, MISTRAL_NEWS_URL + "voxtral-tts/": body})
        result = read_publications("mistral", client, datetime.fromisoformat(NOW))
        self.assertEqual([item["model_id"] for item in result["models"]], ["Voxtral TTS"])

    def test_news_remains_readable_when_the_independent_model_data_fails(self) -> None:
        client = DocumentClient({MODEL_DATA_URL: SourceError("model docs unavailable")})
        result = read_publications("mistral", client, datetime.fromisoformat(NOW))
        self.assertEqual(result["status"], "source_error")
        self.assertEqual(result["models"][0]["model_id"], "Mistral Large 4")
        self.assertEqual(result["models"][0]["access"]["developers"], "public")


class MistralIntegrationTests(unittest.TestCase):
    def scan(self, store: SnapshotStore, client: DocumentClient, at: str = NOW) -> dict[str, Any]:
        return scan_provider(
            MistralAdapter(client), store, at,
            descriptions=DescriptionResolver({"mistral": MistralDescriptionSource(client)}),
            lifecycle_client=client, announcement_client=client,
        )

    def test_provider_selection_follows_the_existing_overseas_policy(self) -> None:
        self.assertIn(MistralAdapter, ALL_PROVIDERS)
        self.assertIn(MistralAdapter, OVERSEAS_PROVIDERS)
        for model in ("mistral-large-4", "ministral-14b-2512", "codestral-latest", "voxtral-mini-2602"):
            self.assertEqual(inferred_overseas_providers(model), ("mistral",))
        with tempfile.TemporaryDirectory() as directory:
            adapters = build_adapters(DocumentClient(), cache_dir=Path(directory))
            ids = lambda selected: {adapter.provider_id for adapter in selected}
            self.assertNotIn("mistral", ids(select_catalog_providers(adapters)))
            self.assertIn("mistral", ids(select_catalog_providers(adapters, include_overseas=True)))
            self.assertIn("mistral", ids(select_compare_providers(adapters, "mistral-large-4")))
            self.assertEqual(ids(select_compare_providers(adapters, "mistral-large-4", requested=["mistral"])), {"mistral"})

    def test_scan_tracks_sale_rates_modes_unpriced_listings_and_notice_crossings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            first = self.scan(store, DocumentClient())
            self.assertEqual(first["status"], "baseline_created")
            self.assertEqual(first["announcements"]["models"][0]["catalog_model_ids"], ["mistral-large-4", "mistral-large-4-0"])
            prices = PRICES.replace("$0.68", "$0.70")
            second = self.scan(store, DocumentClient({MISTRAL_PRICING_URL: prices}), "2026-10-08T12:00:00+08:00")
            self.assertEqual(second["status"], "changed")
            self.assertEqual({item["model_id"] for item in second["changes"]["price_changes"]}, {"mistral-large-4", "mistral-large-4-0"})
            self.assertIn("mistral-embed", store.read("mistral")["models"])
            crossing = self.scan(store, DocumentClient({MISTRAL_PRICING_URL: prices}), "2026-11-01T12:00:00+08:00")
            self.assertTrue(any(item["kind"] == "milestone_reached" and item["event"]["model_id"] == "zai-glm-5-2" for item in crossing["lifecycle"]["changes"]))

    def test_failed_reads_preserve_each_independent_successful_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            self.scan(store, DocumentClient())
            saved = {key: store.read(key) for key in ("mistral", "lifecycle-mistral", "announcements-mistral")}
            failed = self.scan(store, DocumentClient({MODEL_DATA_URL: SourceError("source offline")}), "2026-10-08T12:00:00+08:00")
            self.assertEqual(failed["status"], "source_error")
            self.assertEqual(failed["lifecycle"]["status"], "source_error")
            self.assertEqual(failed["announcements"]["status"], "source_error")
            self.assertEqual({key: store.read(key) for key in saved}, saved)

    def test_catalogue_scan_bypasses_a_previous_query_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheStore(Path(directory))
            CachedPriceSource(MistralAdapter(DocumentClient()), cache).query("mistral-large-4")
            client = DocumentClient({MISTRAL_PRICING_URL: PRICES.replace("$0.68", "$0.72")})
            current = CachedPriceSource(MistralAdapter(client), cache).catalog_records()
            record = next(item for item in current if item["model_id"] == "mistral-large-4")
            self.assertEqual(offer(record)["prices"][0]["amount"], "0.72")
            self.assertIn(MISTRAL_PRICING_URL, client.requests)

    def test_messages_keep_model_introduction_first_and_render_original_units(self) -> None:
        client = DocumentClient()
        resolver = DescriptionResolver({"mistral": MistralDescriptionSource(client)})
        payload = query_adapters([MistralAdapter(client)], "mistral-large-4", exact=True, descriptions=resolver)
        message = comparison_message(payload)
        self.assertLess(message.index("【模型介绍】"), message.index("【渠道对比】"))
        self.assertIn("0.68 美元/百万 tokens", message)
        self.assertIn("原价 1.36", message)
        self.assertIn("推理范围=区域推理", message)
        self.assertNotIn("**", message)
        with tempfile.TemporaryDirectory() as directory:
            report = self.scan(SnapshotStore(Path(directory)), DocumentClient())
            message = scan_message({"providers": [report], "retrieved_at": NOW, "summary": {"providers": 1}})
            self.assertIn("Mistral AI", message)


if __name__ == "__main__":
    unittest.main()
