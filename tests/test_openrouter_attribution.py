"""Verify aggregated-price evidence and stable references without network reads."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.caching import CacheStore, CachedPriceSource
from model_price.delta import scan_providers
from model_price.diffing import compare_snapshots
from model_price.errors import SourceError
from model_price.messages import scan_message
from model_price.providers.openrouter import (
    OPENROUTER_MODELS_URL, OPENROUTER_VIDEOS_URL, OpenRouterAdapter, openrouter_rates,
)
from model_price.providers.openrouter_attribution import (
    OPENROUTER_ENDPOINTS_URL, read_endpoints,
)
from model_price.snapshots import SnapshotStore, build_snapshot, parse_baseline_selection


def per_token(amount: str) -> str:
    return format(Decimal(amount) / 1000000, "f")


def model_entry(model_id: str, input_price: str, output_price: str = "3") -> dict[str, Any]:
    return {
        "id": model_id, "name": model_id, "description": "An official model description.",
        "architecture": {"output_modalities": ["text"]},
        "pricing": {"prompt": per_token(input_price), "completion": per_token(output_price)},
    }


def endpoint(
    provider: str, input_price: str, output_price: str = "3", *,
    discount: Any = 0, status: int = 0, tag: str | None = None,
) -> dict[str, Any]:
    return {
        "provider_name": provider, "tag": tag or provider.lower(),
        "name": provider, "status": status, "context_length": 128000,
        "pricing": {
            "prompt": per_token(input_price), "completion": per_token(output_price),
            "discount": discount,
        },
    }


class FixtureClient:
    """Unregistered reads fail, so request-scope regressions cannot pass silently."""

    def __init__(self, documents: dict[str, str | Exception]) -> None:
        self.documents = documents
        self.requests: list[str] = []

    def get_text(self, url: str) -> str:
        self.requests.append(url)
        document = self.documents.get(url, SourceError(f"unexpected read: {url}"))
        if isinstance(document, Exception):
            raise document
        return document


def fixture_client(
    models: list[dict[str, Any]],
    endpoints: dict[str, list[dict[str, Any]] | str | Exception] | None = None,
) -> FixtureClient:
    documents: dict[str, str | Exception] = {
        OPENROUTER_MODELS_URL: json.dumps({"data": models}),
        OPENROUTER_VIDEOS_URL: json.dumps({"data": []}),
    }
    for model_id, entries in (endpoints or {}).items():
        document = json.dumps({"data": {"id": model_id, "endpoints": entries}}) if isinstance(entries, list) else entries
        documents[OPENROUTER_ENDPOINTS_URL.format(model_id=model_id)] = document
    return FixtureClient(documents)


class OpenRouterAttributionTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = SnapshotStore(Path(directory.name))
        self.day = 0

    def scan(
        self,
        models: list[dict[str, Any]],
        endpoints: dict[str, list[dict[str, Any]] | str | Exception] | None = None,
        *, since: str | None = None, cached: bool = False,
    ) -> tuple[dict[str, Any], FixtureClient]:
        self.day += 1
        client = fixture_client(models, endpoints)
        adapter = OpenRouterAdapter(client)
        source = CachedPriceSource(adapter, CacheStore(self.store.root / "cache"), refresh=True) if cached else adapter
        payload = scan_providers(
            [source], self.store, captured_at=f"2026-10-{self.day:02d}T09:00:00+08:00",
            baseline=parse_baseline_selection(since),
        )
        return payload, client

    def prime(
        self, model_id: str = "vendor/model", input_price: str = "1", output_price: str = "3",
        *, provider: str = "HostA", discount: Any = 0,
    ) -> None:
        self.scan([model_entry(model_id, "99", output_price)])
        self.scan(
            [model_entry(model_id, input_price, output_price)],
            {model_id: [endpoint(provider, input_price, output_price, discount=discount)]},
        )

    @staticmethod
    def report(payload: dict[str, Any]) -> dict[str, Any]:
        return payload["providers"][0]

    @staticmethod
    def moves(payload: dict[str, Any]) -> list[dict[str, Any]]:
        return payload["providers"][0]["changes"]["price_changes"]

    def offer(self, model_id: str = "vendor/model") -> dict[str, Any]:
        return self.store.read("openrouter")["models"][model_id]["offers"][0]

    def test_initial_and_unchanged_catalogues_never_walk_the_endpoints(self) -> None:
        models = [model_entry(f"vendor/model-{index}", "1") for index in range(600)]
        first, first_client = self.scan(models)
        second, second_client = self.scan(models)
        self.assertEqual(self.report(first)["status"], "baseline_created")
        self.assertEqual(self.report(second)["status"], "unchanged")
        for client in (first_client, second_client):
            self.assertEqual(client.requests, [OPENROUTER_MODELS_URL, OPENROUTER_VIDEOS_URL])

    def test_queries_lists_and_catalogue_reads_keep_their_original_request_scope(self) -> None:
        for operation in ("query", "list", "catalog"):
            with self.subTest(operation=operation):
                client = fixture_client([model_entry("vendor/model", "1")])
                source = OpenRouterAdapter(client)
                if operation == "query":
                    source.query("vendor/model")
                elif operation == "list":
                    source.list_models()
                else:
                    source.catalog_records()
                self.assertEqual(client.requests, [OPENROUTER_MODELS_URL, OPENROUTER_VIDEOS_URL])

    def test_only_changed_models_are_read_once_even_with_multiple_changed_charges(self) -> None:
        self.scan([model_entry("vendor/moved", "2", "6"), model_entry("vendor/still", "1")])
        payload, client = self.scan(
            [model_entry("vendor/moved", "1", "3"), model_entry("vendor/still", "1"), model_entry("vendor/new", "1")],
            {"vendor/moved": [endpoint("HostA", "1", "3")]},
        )
        self.assertEqual(len(self.moves(payload)), 2)
        self.assertEqual(client.requests, [
            OPENROUTER_MODELS_URL, OPENROUTER_VIDEOS_URL,
            OPENROUTER_ENDPOINTS_URL.format(model_id="vendor/moved"),
        ])

    def test_learning_endpoint_terms_does_not_rewrite_the_historical_price(self) -> None:
        self.scan([model_entry("vendor/model", "2")])
        payload, _ = self.scan(
            [model_entry("vendor/model", "0.45", "1.35")],
            {"vendor/model": [endpoint("HostA", "0.45", "1.35", discount=0.55)]},
        )
        move = self.moves(payload)[0]
        self.assertEqual(move["cause"], "catalog_price_fluctuation")
        self.assertNotIn("provider_name", move["from"])
        self.assertEqual(move["discount"], "0.45")
        self.assertEqual(move["endpoint_discount"], 0.55)
        self.assertEqual(move["list_amount"], "1")
        self.assertEqual(move["to"]["amount"], "0.45")
        self.assertEqual(move["to"]["list_amount_basis"], "endpoint_discount")
        message = scan_message(payload)
        self.assertIn("减价 55%", message)
        self.assertIn("4.5 折", message)
        self.assertNotIn("5.5 折", message)

    def test_discount_start_and_end_are_promotions_on_the_same_reference(self) -> None:
        self.prime()
        for amount, output, discount in (("0.45", "1.35", 0.55), ("1", "3", 0)):
            with self.subTest(discount=discount):
                payload, _ = self.scan(
                    [model_entry("vendor/model", amount, output)],
                    {"vendor/model": [endpoint("HostA", amount, output, discount=discount)]},
                )
                self.assertEqual({move["cause"] for move in self.moves(payload)}, {"promotion_change"})
                self.assertTrue(all(move["pricing_attribution"]["reference_unchanged"] for move in self.moves(payload)))
                message = scan_message(payload)
                self.assertIn("促销/折扣变动", message)
                self.assertNotIn("【价格调整】", message)
                self.assertNotIn("标准价格变化", message)

    def test_same_reference_base_rate_adjustment_survives_a_discount(self) -> None:
        self.prime(input_price="0.45", output_price="1.35", discount=0.55)
        payload, _ = self.scan(
            [model_entry("vendor/model", "0.54", "1.8")],
            {"vendor/model": [endpoint("HostA", "0.54", "1.8", discount=0.55)]},
        )
        self.assertEqual({move["cause"] for move in self.moves(payload)}, {"price_adjustment"})
        self.assertEqual(self.moves(payload)[0]["pricing_attribution"]["reference_from"]["list_amount"], "1")
        self.assertEqual(self.moves(payload)[0]["pricing_attribution"]["reference_to"]["list_amount"], "1.2")
        self.assertIn("【价格调整】", scan_message(payload))
        self.assertIn("主供应商 HostA 的未折价变动", scan_message(payload))

    def test_a_base_rate_adjustment_and_promotion_both_remain_visible(self) -> None:
        self.prime()
        payload, _ = self.scan(
            [model_entry("vendor/model", "0.54", "1.8")],
            {"vendor/model": [endpoint("HostA", "0.54", "1.8", discount=0.55)]},
        )
        self.assertEqual(self.moves(payload)[0]["causes"], ["price_adjustment", "promotion_change"])
        message = scan_message(payload)
        self.assertIn("【价格调整】【促销/折扣变动】", message)

    def test_a_supplier_display_name_change_does_not_replace_its_tagged_reference(self) -> None:
        self.prime()
        payload, _ = self.scan(
            [model_entry("vendor/model", "1.2")],
            {"vendor/model": [endpoint("Renamed Host", "1.2", tag="hosta")]},
        )
        self.assertEqual(self.moves(payload)[0]["cause"], "price_adjustment")
        self.assertEqual(self.offer()["pricing_attribution"]["reference_endpoint"]["tag"], "hosta")

    def test_a_b_a_routes_do_not_replace_the_primary_reference_or_report_repricing(self) -> None:
        self.prime()
        hosts = [endpoint("HostA", "1"), endpoint("HostB", "0.2", "0.6")]
        for input_price, output_price, provider in (("0.2", "0.6", "HostB"), ("1", "3", "HostA")):
            payload, _ = self.scan(
                [model_entry("vendor/model", input_price, output_price)],
                {"vendor/model": hosts},
            )
            self.assertEqual({move["cause"] for move in self.moves(payload)}, {"provider_switch"})
            self.assertEqual(self.moves(payload)[0]["provider_name"], provider)
            self.assertEqual(self.offer()["pricing_attribution"]["reference_endpoint"]["provider_name"], "HostA")
            self.assertEqual(self.report(payload)["changes"]["offers_added"], [])
            self.assertEqual(self.report(payload)["changes"]["offers_removed"], [])
            self.assertNotIn("【价格调整】", scan_message(payload))

    def test_a_secondary_hosts_promotion_is_not_a_primary_rate_adjustment(self) -> None:
        self.prime()
        self.scan(
            [model_entry("vendor/model", "0.2", "0.6")],
            {"vendor/model": [endpoint("HostA", "1"), endpoint("HostB", "0.2", "0.6")]},
        )
        payload, _ = self.scan(
            [model_entry("vendor/model", "0.15", "0.45")],
            {"vendor/model": [endpoint("HostA", "1"), endpoint("HostB", "0.15", "0.45", discount=0.25)]},
        )
        self.assertEqual({move["cause"] for move in self.moves(payload)}, {"promotion_change"})
        self.assertIn("主供应商 HostA 的未折价未变", scan_message(payload))

    def test_the_matching_endpoint_is_not_assumed_to_be_the_minimum(self) -> None:
        self.prime()
        payload, _ = self.scan(
            [model_entry("vendor/model", "0.2", "0.6")],
            {"vendor/model": [endpoint("Cheapest", "0.1", "0.3"), endpoint("HostB", "0.2", "0.6"), endpoint("HostA", "1")]},
        )
        self.assertEqual(self.moves(payload)[0]["provider_name"], "HostB")

    def test_input_price_alone_cannot_identify_a_route(self) -> None:
        self.prime()
        payload, _ = self.scan(
            [model_entry("vendor/model", "0.2", "0.6")],
            {"vendor/model": [endpoint("HostB", "0.2", "0.7"), endpoint("HostA", "1")]},
        )
        self.assertIsNone(self.moves(payload)[0]["provider_name"])
        self.assertEqual(self.moves(payload)[0]["cause"], "catalog_price_fluctuation")
        self.assertIn("未找到完整费率匹配", scan_message(payload))

    def test_ambiguous_or_unavailable_routes_are_not_named_as_selected(self) -> None:
        for hosts, expected_status in (
            ([endpoint("HostB", "0.2", "0.6"), endpoint("HostC", "0.2", "0.6")], "ambiguous"),
            ([endpoint("HostB", "0.2", "0.6", status=-5)], "not_found"),
        ):
            with self.subTest(status=expected_status):
                self.prime()
                payload, _ = self.scan(
                    [model_entry("vendor/model", "0.2", "0.6")], {"vendor/model": hosts},
                )
                self.assertIsNone(self.moves(payload)[0]["provider_name"])
                self.assertEqual(self.offer()["pricing_attribution"]["status"], expected_status)
                self.assertEqual(self.offer()["pricing_attribution"]["reference_endpoint"]["provider_name"], "HostA")

    def test_missing_primary_keeps_its_identity_instead_of_pinning_a_replacement(self) -> None:
        self.prime()
        payload, _ = self.scan(
            [model_entry("vendor/model", "0.2", "0.6")],
            {"vendor/model": [endpoint("HostB", "0.2", "0.6")]},
        )
        evidence = self.offer()["pricing_attribution"]
        self.assertEqual(evidence["reference_endpoint"]["tag"], "hosta")
        self.assertEqual(evidence["reference_status"], "not_found")
        self.assertIn("主供应商 HostA 本次未找到，保留参照", scan_message(payload))

    def test_a_returning_primary_is_still_the_reference_after_an_endpoint_read_failure(self) -> None:
        self.prime()
        self.scan(
            [model_entry("vendor/model", "0.2", "0.6")],
            {"vendor/model": SourceError("temporarily unavailable")},
        )
        payload, _ = self.scan(
            [model_entry("vendor/model", "0.3", "0.9")],
            {"vendor/model": [endpoint("HostA", "1"), endpoint("HostB", "0.3", "0.9")]},
        )
        evidence = self.offer()["pricing_attribution"]
        self.assertEqual(evidence["reference_endpoint"]["provider_name"], "HostA")
        self.assertEqual(evidence["reference_status"], "observed")
        self.assertEqual(self.moves(payload)[0]["cause"], "catalog_price_fluctuation")

    def test_an_unexpected_enrichment_failure_cannot_discard_a_valid_catalogue(self) -> None:
        self.prime()

        def fail_after_mutating(source: Any, current: dict[str, Any], *args: Any) -> None:
            current["models"]["vendor/model"]["offers"][0]["prices"][0]["amount"] = "999"
            raise RuntimeError("invalid supplemental evidence")

        with patch.object(OpenRouterAdapter, "enrich_snapshot", fail_after_mutating):
            payload, _ = self.scan([model_entry("vendor/model", "0.2", "0.6")])
        self.assertEqual(self.moves(payload)[0]["to"]["amount"], "0.2")
        self.assertEqual(self.offer()["prices"][0]["amount"], "0.2")
        self.assertEqual(self.report(payload)["pricing_attribution"]["status"], "source_error")
        self.assertIn("目录价格已保留", scan_message(payload))

    def test_invalid_discount_evidence_does_not_break_the_message(self) -> None:
        self.prime()
        payload, _ = self.scan(
            [model_entry("vendor/model", "0.2", "0.6")],
            {"vendor/model": [endpoint("HostA", "0.2", "0.6", discount="bad")]},
        )
        self.assertIsNone(self.moves(payload)[0]["list_amount"])
        self.assertEqual(self.moves(payload)[0]["cause"], "catalog_price_fluctuation")
        self.assertIn("0.2 美元/百万 tokens", scan_message(payload))

    def test_failures_leave_catalogue_prices_and_the_pinned_reference_usable(self) -> None:
        for document in (SourceError("request budget exhausted"), "not json", "{}", json.dumps({"data": {"id": "wrong/model", "endpoints": []}})):
            with self.subTest(document=document):
                self.prime()
                payload, _ = self.scan(
                    [model_entry("vendor/model", "0.2", "0.6")], {"vendor/model": document},
                )
                self.assertEqual(self.report(payload)["status"], "changed")
                self.assertEqual(self.moves(payload)[0]["to"]["amount"], "0.2")
                self.assertEqual(self.moves(payload)[0]["cause"], "catalog_price_fluctuation")
                self.assertEqual(self.offer()["pricing_attribution"]["reference_endpoint"]["tag"], "hosta")
                self.assertEqual(self.offer()["pricing_attribution"]["status"], "source_error")
                self.assertIn("托管方归因读取失败", scan_message(payload))

    def test_unchanged_scans_preserve_the_date_of_the_last_endpoint_observation(self) -> None:
        self.prime()
        before = copy.deepcopy(self.offer())
        payload, client = self.scan([model_entry("vendor/model", "1")])
        self.assertEqual(self.report(payload)["status"], "unchanged")
        self.assertEqual(self.offer(), before)
        self.assertEqual(client.requests, [OPENROUTER_MODELS_URL, OPENROUTER_VIDEOS_URL])

    def test_historical_selection_checks_changes_against_both_selected_and_latest_archives(self) -> None:
        self.prime()
        self.scan(
            [model_entry("vendor/model", "0.2", "0.6")],
            {"vendor/model": [endpoint("HostA", "1"), endpoint("HostB", "0.2", "0.6")]},
        )
        payload, client = self.scan(
            [model_entry("vendor/model", "0.2", "0.6")],
            {"vendor/model": [endpoint("HostA", "1"), endpoint("HostB", "0.2", "0.6")]},
            since="2026-10-02",
        )
        self.assertEqual(self.report(payload)["baseline_at"], "2026-10-02T09:00:00+08:00")
        self.assertEqual(self.moves(payload)[0]["cause"], "provider_switch")
        self.assertEqual(len([url for url in client.requests if url.endswith("/endpoints")]), 1)
        self.assertEqual(self.offer()["pricing_attribution"]["reference_endpoint"]["provider_name"], "HostA")

    def test_cached_adapter_forwards_the_policy_and_does_not_cache_attribution(self) -> None:
        self.scan([model_entry("vendor/model", "2")], cached=True)
        payload, client = self.scan(
            [model_entry("vendor/model", "1")], {"vendor/model": [endpoint("HostA", "1")]}, cached=True,
        )
        self.assertTrue(self.report(payload)["aggregated_pricing"])
        self.assertEqual(self.moves(payload)[0]["provider_name"], "HostA")
        self.assertEqual(len(client.requests), 3)

    def test_attribution_and_term_changes_alone_do_not_change_price_or_offer_identity(self) -> None:
        self.prime()
        before = self.store.read("openrouter")
        after = copy.deepcopy(before)
        offer = after["models"]["vendor/model"]["offers"][0]
        offer["pricing_attribution"]["selected_endpoint"]["provider_name"] = "Renamed"
        offer["prices"][0]["discount"] = "0.1"
        offer["prices"][0]["list_amount"] = "10"
        changes = compare_snapshots(before, after)
        self.assertEqual(changes["status"], "unchanged")
        self.assertEqual(changes["changes"]["total"], 0)

    def test_context_overrides_keep_their_scope_and_are_not_given_default_endpoint_terms(self) -> None:
        before = model_entry("vendor/model", "2")
        before["pricing"]["overrides"] = [{"min_prompt_tokens": 1000, "prompt": per_token("4")}]
        after = copy.deepcopy(before)
        after["pricing"]["prompt"] = per_token("1")
        self.scan([before])
        payload, _ = self.scan([after], {"vendor/model": [endpoint("HostA", "1")]})
        offers = self.store.read("openrouter")["models"]["vendor/model"]["offers"]
        self.assertIn("pricing_attribution", offers[0])
        self.assertNotIn("pricing_attribution", offers[1])
        self.assertIsNone(offers[1]["prices"][0]["provider_name"])
        self.assertEqual(self.report(payload)["changes"]["offers_added"], [])

    def test_a_two_model_promotion_remains_one_complete_neutral_batch_per_section(self) -> None:
        ids = ["vendor/model-a", "vendor/model-b"]
        self.scan([model_entry(name, "99") for name in ids])
        self.scan(
            [model_entry(name, "1") for name in ids],
            {name: [endpoint("HostA", "1")] for name in ids},
        )
        payload, _ = self.scan(
            [model_entry(name, "0.45", "1.35") for name in ids],
            {name: [endpoint("HostA", "0.45", "1.35", discount=0.55)] for name in ids},
        )
        original = copy.deepcopy(payload)
        message = scan_message(payload, max_chars=10)
        self.assertEqual(message.count("【目录价波动】"), 2)
        self.assertEqual(message.count("vendor/model-a、vendor/model-b"), 2)
        self.assertEqual(message.count("https://openrouter.ai/models"), 2)
        self.assertIn("2 个模型，4 项目录价变化", message)
        self.assertIn("托管方 HostA；减价 55%", message)
        self.assertIn("不据此认定原厂标准价调整", message)
        self.assertNotIn("标准价格变化", message)
        self.assertNotIn("【价格调整】", message)
        self.assertIn("需总结压缩到 10 字内", message)
        self.assertEqual(payload, original)
        for line in message.splitlines():
            if "https://" in line:
                self.assertTrue(line.endswith("https://openrouter.ai/models"))

    def test_a_mixed_batch_keeps_a_verified_primary_adjustment_visible_without_expanding_rates(self) -> None:
        ids = ["vendor/model-a", "vendor/model-b"]
        self.scan([model_entry(name, "99") for name in ids])
        self.scan(
            [model_entry(name, "1") for name in ids],
            {name: [endpoint("HostA", "1")] for name in ids},
        )
        payload, _ = self.scan(
            [model_entry(ids[0], "1.2345"), model_entry(ids[1], "0.45", "1.35")],
            {
                ids[0]: [endpoint("HostA", "1.2345")],
                ids[1]: [endpoint("HostA", "0.45", "1.35", discount=0.55)],
            },
        )
        message = scan_message(payload)
        self.assertEqual(message.count("【目录价波动】"), 2)
        self.assertEqual(message.count("其中【价格调整】"), 2)
        self.assertIn("已核实同一主供应商的 1 项未折价变化）：vendor/model-a", message)
        self.assertIn("vendor/model-a、vendor/model-b", message)
        self.assertNotIn("1.2345", message)
        self.assertNotIn("标准价格变化", message)

    def test_a_primary_adjustment_and_route_change_preserve_both_prices_meanings(self) -> None:
        self.prime()
        payload, _ = self.scan(
            [model_entry("vendor/model", "0.2", "0.6")],
            {"vendor/model": [endpoint("HostA", "1.2"), endpoint("HostB", "0.2", "0.6")]},
        )
        self.assertEqual(self.moves(payload)[0]["causes"], ["price_adjustment", "provider_switch"])
        message = scan_message(payload)
        self.assertIn("1 美元/百万 tokens → 0.2 美元/百万 tokens", message)
        self.assertIn("主供应商 HostA 的未折价变动（相对上次端点观察）：1 美元/百万 tokens → 1.2 美元/百万 tokens（按折扣还原）", message)

    def test_aggregated_wording_is_selected_by_policy_rather_than_provider_id(self) -> None:
        self.prime()
        payload, _ = self.scan(
            [model_entry("vendor/model", "0.45", "1.35")],
            {"vendor/model": [endpoint("HostA", "0.45", "1.35", discount=0.55)]},
        )
        self.report(payload)["provider"] = {"id": "another-aggregator", "name": "Another Aggregator"}
        message = scan_message(payload)
        self.assertIn("Another Aggregator", message)
        self.assertIn("促销/折扣变动", message)
        self.assertNotIn("标准价格变化", message)

    def test_an_old_snapshot_still_compares_by_amount_without_a_global_version_reset(self) -> None:
        client = fixture_client([model_entry("vendor/model", "1")])
        source = OpenRouterAdapter(client)
        previous = build_snapshot(source, source.catalog_records(), "2026-10-01T09:00:00+08:00")
        previous.pop("aggregated_pricing")
        current = copy.deepcopy(previous)
        current["aggregated_pricing"] = True
        self.assertEqual(compare_snapshots(previous, current)["status"], "unchanged")


class EndpointDiscountParsingTests(unittest.TestCase):
    def test_the_official_discount_formula_restores_the_endpoints_own_rate(self) -> None:
        document = json.dumps({"data": {"id": "qwen/model", "endpoints": [
            endpoint("StreamLake", "0.04815", "0.19305", discount=0.55),
        ]}})
        prices = read_endpoints(document, "qwen/model", openrouter_rates)[0]["prices"]
        self.assertEqual(prices[0]["list_amount"], "0.107")
        self.assertEqual(prices[1]["list_amount"], "0.429")
        self.assertEqual(prices[0]["discount"], "0.45")

    def test_missing_invalid_and_full_discounts_do_not_invent_a_base_price(self) -> None:
        for discount in (None, -0.1, 1.1, "bad", "NaN", 1):
            with self.subTest(discount=discount):
                item = endpoint("HostA", "0" if discount == 1 else "1", discount=discount)
                document = json.dumps({"data": {"id": "vendor/model", "endpoints": [item]}})
                prices = read_endpoints(document, "vendor/model", openrouter_rates)[0]["prices"]
                self.assertNotIn("list_amount", prices[0])


if __name__ == "__main__":
    unittest.main()
