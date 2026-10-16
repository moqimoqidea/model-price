"""Verify a channel that reports no price movements is scanned on its listing.

OpenRouter rebills whatever endpoint a request routes to, so its catalogue amounts
move every day without saying anything a reader can act on. A scan therefore
compares it on listing, notice, and capability alone. These tests hold that rule at
the adapter policy, the comparison, the archive, and the message, without network
reads.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.caching import CacheStore, CachedPriceSource
from model_price.delta import scan_providers
from model_price.descriptions.registry import DescriptionResolver
from model_price.descriptions.sources import OpenRouterDescriptionSource
from model_price.messages import scan_message
from model_price.pricing import make_record, price_item
from model_price.providers.openrouter import (
    OPENROUTER_MODELS_URL, OPENROUTER_VIDEOS_URL, OpenRouterAdapter,
)
from model_price.snapshots import SnapshotStore


def per_token(amount: str) -> str:
    return format(Decimal(amount) / 1000000, "f")


def model_entry(
    model_id: str = "vendor/model", input_price: str = "1", output_price: str = "3",
) -> dict[str, Any]:
    return {
        "id": model_id,
        "name": model_id,
        "description": "An official model description.",
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        "supported_parameters": ["tools"],
        "pricing": {
            "prompt": per_token(input_price),
            "completion": per_token(output_price),
        },
    }


class FixtureClient:
    """Unregistered reads fail, so request-scope regressions cannot pass silently."""

    def __init__(self, models: list[dict[str, Any]]) -> None:
        self.requests: list[str] = []
        self.documents = {
            OPENROUTER_MODELS_URL: json.dumps({"data": models}),
            OPENROUTER_VIDEOS_URL: json.dumps({"data": []}),
        }

    def get_text(self, url: str) -> str:
        self.requests.append(url)
        if url not in self.documents:
            raise AssertionError(f"unexpected read: {url}")
        return self.documents[url]


class OpenRouterScanTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = SnapshotStore(Path(directory.name))
        self.day = 0

    def scan(
        self,
        models: list[dict[str, Any]],
        *,
        cached: bool = False,
        store: SnapshotStore | None = None,
    ) -> tuple[dict[str, Any], FixtureClient]:
        self.day += 1
        client = FixtureClient(models)
        adapter = OpenRouterAdapter(client)
        source = (
            CachedPriceSource(
                adapter, CacheStore(self.store.root / "cache"), refresh=True
            )
            if cached
            else adapter
        )
        payload = scan_providers(
            [source],
            store or self.store,
            captured_at=f"2026-10-{self.day:02d}T09:00:00+08:00",
            descriptions=DescriptionResolver(
                {"openrouter": OpenRouterDescriptionSource(client)}
            ),
        )
        return payload, client

    @staticmethod
    def report(payload: dict[str, Any]) -> dict[str, Any]:
        return payload["providers"][0]

    def catalogue(self, model_id: str = "vendor/model") -> dict[str, Any]:
        return self.store.read("openrouter")["models"][model_id]

    def test_a_price_move_alone_is_not_a_change_this_scan_reports(self) -> None:
        self.scan([model_entry()])
        payload, _ = self.scan([model_entry(input_price="2", output_price="6")])
        report = self.report(payload)
        self.assertEqual(report["status"], "unchanged")
        self.assertFalse(report["reports_price_movements"])
        self.assertEqual(report["changes"]["total"], 0)
        for field in ("models_added", "models_removed", "offers_added", "offers_removed"):
            self.assertEqual(report["changes"][field], [])
        self.assertEqual(report["changes"]["price_changes"], [])
        message = scan_message(payload)
        self.assertNotIn("【模型价格】", message)
        self.assertNotIn("美元", message)
        self.assertIn("全部无变化", message)

    def test_the_archive_keeps_the_amounts_the_comparison_leaves_out(self) -> None:
        self.scan([model_entry()])
        self.scan([model_entry(input_price="2")])
        offer = self.catalogue()["offers"][0]
        self.assertEqual(
            (offer["prices"][0]["amount"], offer["prices"][0]["unit"]),
            ("2", "USD_per_million_tokens"),
        )
        self.assertNotIn("pricing_attribution", offer)

    def test_a_new_listing_is_reported_by_its_capability_and_never_by_its_rates(self) -> None:
        self.scan([model_entry("vendor/older")])
        payload, _ = self.scan([model_entry("vendor/older"), model_entry("vendor/new", "3")])
        report = self.report(payload)
        self.assertEqual(report["status"], "changed")
        added = report["changes"]["models_added"]
        self.assertEqual([model["model_id"] for model in added], ["vendor/new"])
        # The JSON keeps the whole entry, so a program can still read what a new
        # listing publishes without a second query.
        self.assertTrue(added[0]["offers"])
        message = scan_message(payload)
        self.assertIn("【新增上架】", message)
        self.assertIn("【新增上架】vendor/new", message)
        self.assertNotIn("vendor/new（vendor/new）", message)
        self.assertIn("主打能力", message)
        self.assertIn("输入：文本", message)
        self.assertNotIn("【模型价格】", message)
        self.assertNotIn("美元", message)
        self.assertIn("新增模型 1", message)

    def test_a_withdrawal_is_reported_as_a_catalogue_fact(self) -> None:
        self.scan([model_entry("vendor/gone"), model_entry("vendor/kept")])
        payload, _ = self.scan([model_entry("vendor/kept")])
        report = self.report(payload)
        self.assertEqual(report["status"], "changed")
        self.assertEqual(
            [model["model_id"] for model in report["changes"]["models_removed"]],
            ["vendor/gone"],
        )
        message = scan_message(payload)
        self.assertIn("【目录下架】", message)
        self.assertIn("vendor/gone", message)
        self.assertNotIn("美元", message)

    def test_a_new_billing_mode_alone_is_not_a_change_either(self) -> None:
        self.scan([model_entry()])
        tiered = model_entry()
        tiered["pricing"]["overrides"] = [
            {"min_prompt_tokens": 1000, "prompt": per_token("4")}
        ]
        payload, _ = self.scan([tiered])
        self.assertEqual(self.report(payload)["status"], "unchanged")

    def test_the_policy_survives_a_cached_adapter(self) -> None:
        self.scan([model_entry()], cached=True)
        payload, client = self.scan([model_entry(input_price="2")], cached=True)
        self.assertFalse(self.report(payload)["reports_price_movements"])
        self.assertEqual(self.report(payload)["status"], "unchanged")
        self.assertEqual(
            set(client.requests), {OPENROUTER_MODELS_URL, OPENROUTER_VIDEOS_URL}
        )

    def test_a_baseline_archived_with_the_retired_fields_still_compares(self) -> None:
        self.scan([model_entry("vendor/kept")])
        legacy = copy.deepcopy(self.store.read("openrouter"))
        legacy["captured_at"] = "2026-10-05T09:00:00+08:00"
        legacy["aggregated_pricing"] = True
        legacy["models"]["vendor/kept"]["offers"][0]["pricing_attribution"] = {
            "status": "matched",
            "reference_endpoint": {"provider_name": "HostA", "tag": "hosta"},
        }
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            store.write("openrouter", legacy)
            payload, _ = self.scan(
                [model_entry("vendor/kept", "5"), model_entry("vendor/new")],
                store=store,
            )
        report = self.report(payload)
        self.assertEqual(report["baseline_at"], "2026-10-05T09:00:00+08:00")
        self.assertEqual(report["status"], "changed")
        self.assertEqual(
            [model["model_id"] for model in report["changes"]["models_added"]],
            ["vendor/new"],
        )
        self.assertEqual(report["changes"]["price_changes"], [])

    def test_both_documents_are_read_once_and_nothing_else_is_requested(self) -> None:
        first, first_client = self.scan([model_entry()])
        second, second_client = self.scan([model_entry("vendor/other")])
        self.assertEqual(self.report(first)["status"], "baseline_created")
        self.assertEqual(self.report(second)["status"], "changed")
        for client in (first_client, second_client):
            # The introduction source reads the catalogue document for itself; no
            # scan ever asks for a third document, and no endpoint page at all.
            self.assertEqual(
                set(client.requests), {OPENROUTER_MODELS_URL, OPENROUTER_VIDEOS_URL}
            )


class PolicySource:
    """A channel whose catalogue is fixed, so only its policy can decide the report."""

    source_kind = "test"

    def __init__(
        self, provider_id: str, reports_price_movements: bool, amount: str
    ) -> None:
        self.provider_id = provider_id
        self.provider_name = provider_id
        self.source_url = f"https://{provider_id}.example/api/models"
        self.catalog_url = f"https://{provider_id}.example/models"
        self.reports_price_movements = reports_price_movements
        self.records = [
            make_record(
                provider_id,
                provider_id,
                "vendor/model",
                "vendor/model",
                "Global",
                [
                    {
                        "name": "pay_as_you_go",
                        "conditions": {"billing_mode": "pay_as_you_go"},
                        "prices": [
                            price_item(
                                "input", "输入", amount, "USD_per_million_tokens"
                            )
                        ],
                    }
                ],
                self.source_url,
                self.source_kind,
                "2026-09-30T09:00:00+08:00",
            )
        ]

    def catalog_records(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self.records)

    def search(self, model: str, *, exact: bool = False) -> list[dict[str, Any]]:
        return copy.deepcopy(self.records)


class PriceMovementPolicyTests(unittest.TestCase):
    """The channel reports price movements or it does not: its id decides nothing."""

    def scan_twice(self, source: PolicySource) -> dict[str, Any]:
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            scan_providers(
                [source], store, captured_at="2026-09-30T09:00:00+08:00"
            )
            source.records[0]["offers"][0]["prices"][0]["amount"] = "2"
            return scan_providers(
                [source], store, captured_at="2026-10-01T09:00:00+08:00"
            )["providers"][0]

    def test_a_channel_that_reports_its_movements_still_compares_them(self) -> None:
        report = self.scan_twice(PolicySource("openrouter", True, "1"))
        self.assertEqual(report["status"], "changed")
        self.assertEqual(len(report["changes"]["price_changes"]), 1)
        self.assertNotIn("reports_price_movements", report)

    def test_another_channels_choice_does_not_follow_its_name(self) -> None:
        report = self.scan_twice(PolicySource("another-aggregator", False, "1"))
        self.assertEqual(report["status"], "unchanged")
        self.assertFalse(report["reports_price_movements"])


if __name__ == "__main__":
    unittest.main()
