"""Verify Ant Ling's published promotions and independent retirement evidence.

Captured document shapes stay offline, including partner tables and hydration
that must never become this hosting channel's prices or quoted prose.
"""

from __future__ import annotations

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
from model_price.delta import scan_provider
from model_price.descriptions import DescriptionResolver, query_targets
from model_price.errors import SourceError
from model_price.lifecycle import retired_model_ids
from model_price.lifecycle_sources import ant_ling_events, read_events
from model_price.providers.ant_ling import (
    ANT_LING_DEPRECATION_URL,
    ANT_LING_PRICE_URL,
    AntLingAdapter,
)
from model_price.registry import build_adapters, select_catalog_providers
from model_price.snapshots import SnapshotStore

FIXTURES = Path(__file__).with_name("fixtures")
PRICES = (FIXTURES / "ant-ling-price.html").read_text(encoding="utf-8")
DEPRECATIONS = (FIXTURES / "ant-ling-deprecation.html").read_text(encoding="utf-8")


class DocumentClient:
    """Record fixture reads and expose source failures without any live requests."""

    def __init__(self, responses: dict[str, str | Exception]) -> None:
        self.responses = responses
        self.requests: list[str] = []

    def get_text(self, url: str) -> str:
        self.requests.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


class AntLingPricingTests(unittest.TestCase):
    def adapter(self, document: str = PRICES) -> AntLingAdapter:
        return AntLingAdapter(DocumentClient({ANT_LING_PRICE_URL: document}))

    def test_catalogue_queries_and_promotion_notes_share_one_document_read(self) -> None:
        client = DocumentClient({ANT_LING_PRICE_URL: PRICES})
        adapter = AntLingAdapter(client)
        self.assertEqual(adapter.list_models(), [
            "ling-2.6-1t", "ling-2.6-flash", "ling-3.0-flash",
            "ling-3.0-flash-vl", "ring-2.6-1t",
        ])
        records = adapter.catalog_records()
        for record in records:
            queried = adapter.query(record["model_id"])[0]
            self.assertEqual(queried["offers"], record["offers"])
            self.assertEqual(record["source_updated_at"], "2026-09-23")
            self.assertEqual(record["source"]["url"], ANT_LING_PRICE_URL)
            self.assertEqual(record["currency"], "CNY")
            self.assertEqual(record["delivery_mode"], "first_party")
            self.assertEqual(len(record["offers"]), 1)
            self.assertTrue(all(
                price["unit"] == "CNY_per_million_tokens"
                for price in record["offers"][0]["prices"]
            ))
        self.assertEqual(client.requests, [ANT_LING_PRICE_URL])

    def test_a_struck_list_price_is_not_a_second_charge(self) -> None:
        adapter = self.adapter()
        for model, amounts in (
            ("ling-3.0-flash", ["0.125", "0.375", "0.025"]),
            ("ling-3.0-flash-vl", ["0.14", "0.42", "0.028"]),
        ):
            with self.subTest(model=model):
                prices = adapter.query(model)[0]["offers"][0]["prices"]
                self.assertEqual([price["type"] for price in prices], ["input", "output", "cache_hit"])
                self.assertEqual([price["amount"] for price in prices], amounts)
                self.assertEqual([price["list_amount"] for price in prices], ["0.50", "1.50", "0.10"])
                self.assertTrue(all(
                    "discount" not in price and "effective_until" not in price
                    for price in prices
                ))

    def test_promotion_prose_stays_scoped_and_hydration_is_not_quoted(self) -> None:
        adapter = self.adapter()
        for model, expected in (
            ("ling-3.0-flash", "Ling-3.0-flash 当前限时 2.5 折 ，优惠结束后将逐步恢复原价，请注意您的账户余额。"),
            ("ling-3.0-flash-vl", "Ling-3.0-flash-VL 限时 2.8 折 ，优惠结束后将逐步恢复原价，请注意您的账户余额。"),
        ):
            with self.subTest(model=model):
                self.assertEqual(adapter.query(model)[0]["pricing_notes"], [expected])
        self.assertNotIn("pricing_notes", adapter.query("ling-2.6-1t")[0])

    def test_partner_tables_do_not_list_models_even_when_they_quote_cny(self) -> None:
        document = PRICES.replace("$", "¥").replace(
            "<td>Ling-3.0-flash-VL</td>", "<td>Partner-Only-9</td>"
        )
        adapter = self.adapter(document)
        self.assertNotIn("partner-only-9", adapter.list_models())
        self.assertTrue(all(len(record["offers"]) == 1 for record in adapter.catalog_records()))

    def test_new_names_free_prices_unknown_prices_and_context_tiers_survive(self) -> None:
        document = """<h2>模型定价</h2><table>
<tr><th>模型</th><th>输入长度</th><th>输入价格（每百万 token）</th></tr>
<tr><td>Future-9</td><td>≤ 32K</td><td>¥0</td></tr>
<tr><td>Future-9</td><td>&gt; 32K</td><td>¥1</td></tr>
<tr><td>Unpriced-10</td><td>≤ 32K</td><td>未公布</td></tr></table>"""
        adapter = self.adapter(document)
        future = adapter.query("future-9")[0]
        self.assertEqual([offer["conditions"]["输入长度"] for offer in future["offers"]], ["≤ 32K", "> 32K"])
        self.assertEqual([offer["prices"][0]["amount"] for offer in future["offers"]], ["0", "1"])
        self.assertEqual(adapter.query("unpriced-10")[0]["offers"], [])

    def test_missing_or_partner_only_tables_are_source_errors(self) -> None:
        for document in (
            "<html><title>Please sign in</title></html>",
            PRICES[PRICES.index("<h2>第三方平台") :],
        ):
            with self.subTest(document=document[:40]), self.assertRaises(SourceError):
                self.adapter(document).catalog_records()

    def test_domestic_selection_includes_ant_without_any_additional_request(self) -> None:
        client = DocumentClient({})
        with tempfile.TemporaryDirectory() as directory:
            adapters = build_adapters(client, cache_dir=Path(directory))
            self.assertIn(adapters["ant-ling"], select_catalog_providers(adapters))
        self.assertEqual(client.requests, [])

    def test_unregistered_introductions_and_news_are_reported_honestly(self) -> None:
        records = self.adapter().catalog_records()
        descriptions = DescriptionResolver({}).resolve_many(query_targets("ling", records))
        self.assertTrue(all(item["status"] == "not_found" for item in descriptions))
        self.assertTrue(all(item["reference_url"] == ANT_LING_PRICE_URL for item in descriptions))
        client = DocumentClient({})
        publications = read_publications(
            "ant-ling", client, datetime.fromisoformat("2026-10-01T00:00:00+08:00")
        )
        self.assertEqual(publications["status"], "catalogue_only")
        self.assertEqual(client.requests, [])


class AntLingRetirementTests(unittest.TestCase):
    def scan(
        self,
        store: SnapshotStore,
        captured_at: str,
        *,
        prices: str | Exception = PRICES,
        notices: str | Exception = DEPRECATIONS,
    ) -> dict[str, Any]:
        client = DocumentClient({
            ANT_LING_PRICE_URL: prices, ANT_LING_DEPRECATION_URL: notices,
        })
        return scan_provider(
            AntLingAdapter(client), store, captured_at, lifecycle_client=client
        )

    def test_published_times_ids_and_recommendations_keep_their_original_scope(self) -> None:
        client = DocumentClient({ANT_LING_DEPRECATION_URL: DEPRECATIONS})
        url, events = read_events("ant-ling", client, [])
        self.assertEqual(url, ANT_LING_DEPRECATION_URL)
        self.assertEqual(client.requests, [url])
        self.assertEqual(
            [item["model_id"] for item in events],
            ["Ling-2.5-1T", "Ring-2.5-1T", "Ling-1T", "Ring-1T"],
        )
        self.assertEqual([item["eos_at"] for item in events], [
            "2026-09-30T00:00:00+08:00", "2026-09-30T00:00:00+08:00",
            "2026-06-15T00:00:00+08:00", "2026-06-15T00:00:00+08:00",
        ])
        self.assertEqual([item["replacement"] for item in events], ["Ling-2.6-1T", "Ring-2.6-1T"] * 2)
        self.assertEqual(
            [item["notice_status"] for item in events],
            ["scheduled", "scheduled", "retired", "retired"],
        )
        for item in events:
            self.assertIsNone(item["announced_at"])
            self.assertIsNone(item["eom_at"])
            self.assertIsNone(item["redirect_at"])
            self.assertEqual(item["end_behavior"], "unavailable")

    def test_scheduled_shutdowns_become_retired_only_when_the_published_clock_is_due(self) -> None:
        events = ant_ling_events(DEPRECATIONS)
        before = datetime.fromisoformat("2026-09-29T23:59:59+08:00")
        due = datetime.fromisoformat("2026-09-30T00:00:00+08:00")
        self.assertEqual(retired_model_ids(events, before), ["Ling-1T", "Ring-1T"])
        self.assertEqual(retired_model_ids(events, due), ["Ling-1T", "Ling-2.5-1T", "Ring-1T", "Ring-2.5-1T"])

    def test_date_only_and_undated_confirmed_retirement_do_not_acquire_midnight(self) -> None:
        document = DEPRECATIONS.replace("2026-09-30 00:00:00 (UTC+8)", "2026-09-30").replace(
            "2026-06-15 00:00:00 (UTC+8)", "—"
        ).replace("<code>Ling-2.6-1T</code>", "—")
        events = ant_ling_events(document)
        self.assertEqual([item["eos_at"] for item in events], ["2026-09-30", "2026-09-30", None, None])
        self.assertIsNone(events[0]["replacement"])
        self.assertEqual(
            retired_model_ids(events, datetime.fromisoformat("2026-09-01T00:00:00+08:00")),
            ["Ling-1T", "Ring-1T"],
        )

    def test_reordered_columns_and_future_names_need_no_model_name_list(self) -> None:
        document = """<h3>即将下架</h3><table>
<tr><th>推荐替代模型</th><th>计划下架日期</th><th>模型 ID</th></tr>
<tr><td>Future-10</td><td>2027-01-02 13:14:15 (UTC+8)</td><td>Future-9</td></tr></table>"""
        item = ant_ling_events(document)[0]
        self.assertEqual(item["model_id"], "Future-9")
        self.assertEqual(item["replacement"], "Future-10")
        self.assertEqual(item["eos_at"], "2027-01-02T13:14:15+08:00")

    def test_failed_and_partial_notice_reads_keep_the_last_successful_notice_archive(self) -> None:
        for failed_notice in (
            SourceError("HTTP 403"), "<html>Verification required</html>",
            "<table><tr><th>模型 ID</th><th>计划下架日期</th></tr></table>",
            DEPRECATIONS.replace("2026-09-30 00:00:00 (UTC+8)", "unreadable date", 2),
        ):
            with self.subTest(failure=str(failed_notice)[:50]), tempfile.TemporaryDirectory() as directory:
                store = SnapshotStore(Path(directory))
                self.scan(store, "2026-09-29T10:00:00+08:00")
                previous = store.read("lifecycle-ant-ling")
                result = self.scan(
                    store, "2026-10-01T10:00:00+08:00", notices=failed_notice
                )
                self.assertEqual(result["status"], "unchanged")
                self.assertEqual(result["lifecycle"]["status"], "source_error")
                self.assertEqual(store.read("lifecycle-ant-ling"), previous)
                self.assertEqual(store.read("ant-ling")["captured_at"], "2026-10-01T10:00:00+08:00")

    def test_failed_price_reads_preserve_price_history_while_notice_scanning_continues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            self.scan(store, "2026-09-29T10:00:00+08:00")
            previous = store.read("ant-ling")
            result = self.scan(
                store, "2026-10-01T10:00:00+08:00", prices="<html>Verification required</html>"
            )
            self.assertEqual(result["status"], "source_error")
            self.assertEqual(store.read("ant-ling"), previous)
            self.assertEqual(result["lifecycle"]["status"], "changed")
            self.assertEqual(len(result["lifecycle"]["changes"]), 2)
            self.assertEqual(store.read("lifecycle-ant-ling")["captured_at"], "2026-10-01T10:00:00+08:00")


if __name__ == "__main__":
    unittest.main()
