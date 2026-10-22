"""Exercise channel-scoped change reports without contacting official services."""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.delta import scan_providers
from model_price.diffing import compare_snapshots
from model_price.descriptions.core import DescriptionSource, description_record
from model_price.descriptions.resolver import DescriptionResolver
from model_price.lifecycle_sources import event
from model_price.messages import comparison_message, scan_message, scan_notes
from model_price.pricing import make_record, price_item
from model_price.registry import query_adapters
from model_price.snapshots import SnapshotStore


def model_record(model_id: str, amount: str = "1.125") -> dict[str, Any]:
    """A standing price whose exact decimals and conditions must survive rendering."""
    return make_record(
        "aggregator", "Aggregator", model_id, model_id, "Global",
        [{
            "name": "standard", "conditions": {"context_tier": "short"},
            "prices": [
                price_item("input", "输入", amount, "USD_per_million_tokens"),
                price_item("output", "输出", "8.875", "USD_per_million_tokens"),
            ],
        }],
        "https://aggregator.example/models", "test", "2026-09-30T09:00:00+08:00",
    )


class ChannelAdapter:
    """Expose distinct catalogue and API URLs so reports choose the public page."""

    source_kind = "test"

    def __init__(self, records: list[dict[str, Any]], provider_id: str = "aggregator") -> None:
        self.provider_id = provider_id
        self.provider_name = provider_id
        self.catalog_url = f"https://{provider_id}.example/models"
        self.source_url = f"https://{provider_id}.example/api/models"
        self.records = copy.deepcopy(records)
        for record in self.records:
            record["provider"] = {"id": provider_id, "name": provider_id}

    def catalog_records(self) -> list[dict[str, Any]]:
        return self.records

    def search(self, model: str, *, exact: bool = False) -> list[dict[str, Any]]:
        return self.records


class ChannelIntroduction(DescriptionSource):
    """Return recognizably different prose for the same ID on different hosts."""

    source_kind = "test"

    def __init__(self, provider_id: str) -> None:
        self.source_id = provider_id
        self.source_name = provider_id
        self.source_url = f"https://{provider_id}.example/models"

    def describe(
        self, model_id: str, display_name: str = "", *,
        record: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return description_record(
            model_id, display_name, f"{self.source_id} 对 {model_id} 的官方说明",
            f"{self.source_url}/{model_id}", self.source_kind,
            source_name=self.source_name, lifecycle="active",
        )


def scanned_pair(
    before: list[dict[str, Any]], after: list[dict[str, Any]],
    provider_id: str = "aggregator",
) -> dict[str, Any]:
    resolver = DescriptionResolver({provider_id: ChannelIntroduction(provider_id)})
    with tempfile.TemporaryDirectory() as directory:
        store = SnapshotStore(Path(directory))
        scan_providers(
            [ChannelAdapter(before, provider_id)], store,
            captured_at="2026-09-29T09:00:00+08:00", descriptions=resolver,
        )
        return scan_providers(
            [ChannelAdapter(after, provider_id)], store,
            captured_at="2026-09-30T09:00:00+08:00", descriptions=resolver,
        )


def report_sections(payload: dict[str, Any]) -> tuple[str, str, str]:
    message = scan_message(payload, max_chars=100000)
    capabilities, tail = message.split("【模型能力】", 1)[1].split("【模型价格】", 1)
    prices, conclusion = tail.split("【渠道结论】", 1)
    return capabilities, prices, conclusion


class BatchPriceReportTests(unittest.TestCase):
    def test_multiple_models_are_one_item_in_both_sections_for_any_channel(self) -> None:
        for channel in ("aggregator", "aliyun"):
            with self.subTest(channel=channel):
                payload = scanned_pair(
                    [model_record("m1"), model_record("m2")],
                    [model_record("m1", "0.8125"), model_record("m2", "0.9375")], channel,
                )
                original = copy.deepcopy(payload)
                capabilities, prices, conclusion = report_sections(payload)
                for section in (capabilities, prices):
                    self.assertEqual(section.count("【价格调整】"), 1)
                    self.assertIn("2 个模型，2 项标准价格变化", section)
                    self.assertIn("m1、m2", section)
                    self.assertIn(f"https://{channel}.example/models", section)
                    self.assertNotIn("/api/models", section)
                    self.assertNotIn("0.8125", section)
                    self.assertNotIn("0.9375", section)
                self.assertNotIn("官方说明", capabilities)
                self.assertIn("价格变化 2", conclusion)
                self.assertEqual(payload, original)
                self.assertEqual(len(payload["providers"][0]["model_descriptions"]), 2)
                self.assertEqual(
                    payload["providers"][0]["changes"]["price_changes"][0]["to"]["amount"],
                    "0.8125",
                )

    def test_one_model_with_multiple_charges_keeps_all_rates_and_conditions(self) -> None:
        before, after = model_record("m1"), model_record("m1", "0.8125")
        after["offers"][0]["prices"][1]["amount"] = "6.9375"
        capabilities, prices, _ = report_sections(scanned_pair([before], [after]))
        self.assertIn("【价格调整】m1", capabilities)
        self.assertNotIn("【新增上架】", capabilities)
        self.assertIn("官方说明", capabilities)
        self.assertIn("context_tier=short", prices)
        self.assertIn("1.125 美元/百万 tokens → 0.8125 美元/百万 tokens", prices)
        self.assertIn("8.875 美元/百万 tokens → 6.9375 美元/百万 tokens", prices)

    def test_one_model_with_multiple_context_tiers_is_not_a_batch(self) -> None:
        before, after = model_record("m1"), model_record("m1", "0.8125")
        long_before = copy.deepcopy(before["offers"][0])
        long_before["conditions"] = {"context_tier": "long", "input_tokens": ">272000"}
        long_after = copy.deepcopy(long_before)
        long_after["prices"][0]["amount"] = "2.625"
        before["offers"].append(long_before)
        after["offers"].append(long_after)
        _, prices, _ = report_sections(scanned_pair([before], [after]))
        for text in (
            "context_tier=short", "context_tier=long",
            "input_tokens=>272000", "0.8125", "2.625",
        ):
            self.assertIn(text, prices)
        self.assertNotIn("详情查看", prices)

    def test_nonstandard_prices_do_not_form_a_standard_batch(self) -> None:
        before = [model_record("m1"), model_record("m2")]
        after = [model_record("m1", "0.8125"), model_record("m2", "9.12345")]
        for record in (before[1], after[1]):
            record["offers"][0]["name"] = "fast"
            record["offers"][0]["conditions"] = {"service_tier": "fast"}
        _, prices, conclusion = report_sections(scanned_pair(before, after))
        self.assertIn("0.8125", prices)
        self.assertNotIn("9.12345", prices)
        self.assertNotIn("详情查看", prices)
        self.assertIn("价格变化 2", conclusion)

    def test_batch_does_not_swallow_a_new_model_or_added_standard_mode(self) -> None:
        after = [
            model_record("m1", "0.8125"), model_record("m2", "0.9375"),
            model_record("new", "3.4567"),
        ]
        extra = copy.deepcopy(after[0]["offers"][0])
        extra["conditions"] = {"context_tier": "long"}
        extra["prices"][0]["amount"] = "4.5678"
        after[0]["offers"].append(extra)
        capabilities, prices, _ = report_sections(
            scanned_pair([model_record("m1"), model_record("m2")], after)
        )
        self.assertIn("【新增上架】new", capabilities)
        self.assertIn("【价格调整】【计费模式新增】m1", capabilities)
        self.assertIn("context_tier=long", capabilities)
        self.assertIn("3.4567", prices)
        self.assertIn("4.5678", prices)
        self.assertIn("m1、m2", prices)

    def test_no_https_reference_keeps_detailed_rates(self) -> None:
        payload = scanned_pair(
            [model_record("m1"), model_record("m2")],
            [model_record("m1", "0.8125"), model_record("m2", "0.9375")],
        )
        report = payload["providers"][0]
        report["catalog_url"] = ""
        report["source"]["url"] = ""
        _, prices, _ = report_sections(payload)
        self.assertIn("0.8125", prices)
        self.assertIn("0.9375", prices)

    def test_a_small_budget_never_cuts_the_batch_model_list_or_url(self) -> None:
        ids = sorted(f"vendor/model-{index}" for index in range(20))
        payload = scanned_pair(
            [model_record(name) for name in ids],
            [model_record(name, "0.8125") for name in ids],
        )
        message = scan_message(payload, max_chars=10)
        self.assertIn("需总结压缩到 10 字内", message)
        self.assertEqual(message.count("、".join(ids)), 2)
        self.assertEqual(message.count("https://aggregator.example/models"), 2)


class ChangeClassificationTests(unittest.TestCase):
    def test_mode_removal_has_its_own_label_without_nonstandard_amounts(self) -> None:
        before, after = model_record("m1"), model_record("m1")
        fast = copy.deepcopy(before["offers"][0])
        fast["name"] = "fast-mode"
        fast["conditions"] = {"service_tier": "fast"}
        fast["prices"][0]["amount"] = "99.12345"
        before["offers"].append(fast)
        message = scan_message(scanned_pair([before], [after]), max_chars=100000)
        self.assertIn("【计费模式移除】m1", message)
        self.assertIn("fast-mode", message)
        self.assertNotIn("【新增上架】", message)
        self.assertNotIn("99.12345", message)
        self.assertNotIn("【模型价格】", message)

    def test_a_cleaned_legacy_id_is_not_a_catalogue_removal(self) -> None:
        entry = {
            "model_id": "deepseek-v4-pro",
            "display_name": "DeepSeek-V4-Pro",
            "offers": [],
        }
        before = {
            "provider": {"id": "baidu", "name": "baidu"},
            "captured_at": "2026-09-30T09:00:00+08:00",
            "source": {},
            "models": {"deepseek-v4-pro）": entry},
        }
        after = {**before, "captured_at": "2026-10-01T09:00:00+08:00",
                 "models": {"deepseek-v4-pro": entry}}
        report = compare_snapshots(before, after)
        self.assertEqual(report["changes"]["models_added"], [])
        self.assertEqual(report["changes"]["models_removed"], [])

    def test_a_renamed_caption_is_not_a_price_movement(self) -> None:
        before, after = model_record("m1"), model_record("m1")
        after["offers"][0]["prices"][0]["label"] = "Input tokens"
        payload = scanned_pair([before], [after])
        report = payload["providers"][0]
        changes = report["changes"]
        self.assertEqual(report["status"], "unchanged")
        self.assertEqual(changes["amount_moves"], 0)
        self.assertEqual(changes["caption_changes"], 1)
        self.assertEqual(changes["total"], 0)
        change = changes["price_changes"][0]
        self.assertEqual(change["kind"], "caption_changed")
        self.assertEqual((change["from_label"], change["to_label"]), ("输入", "Input tokens"))
        message = scan_message(payload, max_chars=100000)
        self.assertNotIn("价格调整", message)
        self.assertNotIn("价格变化", message)
        self.assertIn("全部无变化", message)

    def test_a_unit_correction_is_archived_without_making_a_price_change(self) -> None:
        before, after = model_record("m1"), model_record("m1")
        after["offers"][0]["prices"][0]["unit"] = "USD_per_thousand_pages"
        payload = scanned_pair([before], [after])
        changes = payload["providers"][0]["changes"]
        self.assertEqual(changes["amount_moves"], 0)
        self.assertEqual(changes["unit_changes"], 1)
        self.assertEqual(changes["total"], 0)
        self.assertEqual(changes["price_changes"][0]["kind"], "unit_changed")
        message = scan_message(payload, max_chars=100000)
        self.assertIn("全部无变化", message)
        self.assertNotIn("单位修正", message)

    def test_a_price_line_appearing_is_structure_not_a_price_move(self) -> None:
        before, after = model_record("m1"), model_record("m1")
        after["offers"][0]["prices"].append(
            price_item("cache_hit", "缓存输入", "0.05", "USD_per_million_tokens")
        )
        payload = scanned_pair([before], [after])
        changes = payload["providers"][0]["changes"]
        self.assertEqual(changes["amount_moves"], 0)
        self.assertEqual(changes["price_items_added"], 1)
        self.assertEqual(changes["price_changes"][0]["kind"], "line_added")
        capabilities, prices, _ = report_sections(payload)
        self.assertIn("【计费项变化】m1", capabilities)
        self.assertIn("计费项变化（1）", prices)
        self.assertIn("新增计费项", prices)
        self.assertNotIn("价格变化（1）", prices)

    def test_catalogue_removal_is_distinct_from_confirmed_shutdown(self) -> None:
        message = scan_message(scanned_pair(
            [model_record("removed"), model_record("kept")], [model_record("kept")]
        ))
        self.assertIn("【目录下架】removed", message)
        self.assertNotIn("官方确认已下线", message)
        self.assertNotIn("【模型价格】", message)

    def test_replacement_and_price_changes_are_both_visible_on_one_model(self) -> None:
        payload = scanned_pair(
            [model_record("m1"), model_record("m2")],
            [model_record("m1", "0.8125"), model_record("m2", "0.9375")],
        )
        payload["providers"][0]["lifecycle"] = {
            "status": "changed", "changes": [{
                "kind": "detail_revised", "field": "replacement",
                "before": "old-successor", "after": "new-successor",
                "event": event("m1", "https://aggregator.example/notices", replacement="new-successor"),
            }],
        }
        capabilities, prices, conclusion = report_sections(payload)
        self.assertIn("【价格调整】【替代模型更新】m1", capabilities)
        self.assertIn("old-successor → new-successor", capabilities)
        self.assertIn("https://aggregator.example/notices", capabilities)
        self.assertIn("m1、m2", prices)
        self.assertIn("替代模型更新 1", conclusion)

    def test_a_new_model_with_many_offers_is_wrapped_not_one_giant_line(self) -> None:
        kept = model_record("kept")
        big = model_record("big")
        big["offers"] = [
            {
                "name": f"offer-{index}",
                "conditions": {"region": f"r{index}", "tier": f"t{index}"},
                "prices": [price_item(
                    "input", f"输入-{index}", "1.25", "USD_per_million_tokens"
                )],
            }
            for index in range(12)
        ]
        message = scan_message(scanned_pair([kept], [kept, big]), max_chars=100000)
        self.assertTrue(all(f"offer-{index}" in message for index in range(12)))
        self.assertLess(max(len(line) for line in message.splitlines()), 1000)

    def test_a_mass_removal_is_one_notice_item_and_a_singleton_stays_detailed(self) -> None:
        payload = scanned_pair(
            [model_record("m1"), model_record("m2"), model_record("m3"), model_record("kept")],
            [model_record("kept")],
        )
        provider = payload["providers"][0]
        notice = "https://aggregator.example/notices/retire"
        provider["lifecycle"] = {
            "status": "changed",
            "changes": [
                {"kind": "new_notice", "event": event(
                    model_id, notice, replacement="m-next", eos_at="2026-10-10"
                )}
                for model_id in ("m1", "m2")
            ] + [
                {"kind": "new_notice", "event": event(
                    "m3", "https://aggregator.example/notices/other",
                    replacement="m-other", eos_at="2026-10-11",
                )}
            ],
        }
        message = scan_message(payload, max_chars=100000)
        capabilities = message.split("【模型能力】", 1)[1].split("【渠道结论】", 1)[0]
        self.assertEqual(capabilities.count("【目录下架】"), 2)
        self.assertIn("【目录下架】2 个模型（同一公告）：m1、m2", capabilities)
        self.assertIn("推荐/切换至 m-next", capabilities)
        self.assertIn(notice, capabilities)
        self.assertIn("【目录下架】【退役公告新增】m3", capabilities)
        self.assertIn("m-other", capabilities)

    def test_notice_read_failure_is_not_reported_as_an_unchanged_channel(self) -> None:
        payload = scanned_pair([model_record("m1")], [model_record("m1")])
        payload["providers"][0]["lifecycle"] = {"status": "source_error", "error": "notice unavailable"}
        message = scan_message(payload)
        self.assertIn("0 个无变化，1 个本次未纳入", message)
        self.assertNotIn("notice unavailable", message)
        self.assertIn("notice unavailable", scan_notes(payload))
        self.assertNotIn("无变化：aggregator", message)

    def test_same_model_on_two_channels_keeps_two_local_introductions_in_queries(self) -> None:
        providers = [
            ChannelAdapter([model_record("deepseek-flash")], name)
            for name in ("deepseek", "tencent")
        ]
        payload = query_adapters(
            providers, "deepseek-flash",
            descriptions=DescriptionResolver({
                name: ChannelIntroduction(name) for name in ("deepseek", "tencent")
            }),
        )
        message = comparison_message(payload, max_chars=100000)
        for name in ("deepseek", "tencent"):
            self.assertIn(f"{name} 对 deepseek-flash 的官方说明", message)
            self.assertIn(f"https://{name}.example/models/deepseek-flash", message)
        self.assertEqual(len(payload["model_descriptions"]), 2)

    def test_same_added_model_keeps_two_channel_introductions_in_a_scan(self) -> None:
        names = ("deepseek", "tencent")
        resolver = DescriptionResolver({name: ChannelIntroduction(name) for name in names})
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            scan_providers(
                [ChannelAdapter([model_record("base")], name) for name in names],
                store, captured_at="2026-09-29T09:00:00+08:00", descriptions=resolver,
            )
            payload = scan_providers(
                [ChannelAdapter([model_record("base"), model_record("deepseek-flash")], name) for name in names],
                store, captured_at="2026-09-30T09:00:00+08:00", descriptions=resolver,
            )
        capabilities = (
            scan_message(payload).split("【模型能力】", 1)[1].split("【模型价格】", 1)[0]
        )
        self.assertEqual(capabilities.count("【新增上架】deepseek-flash"), 2)
        for name in names:
            self.assertIn(f"{name} 对 deepseek-flash 的官方说明", capabilities)
            self.assertIn(f"https://{name}.example/models/deepseek-flash", capabilities)

    def test_source_urls_remain_the_last_item_on_their_line(self) -> None:
        payload = scanned_pair(
            [model_record("m1"), model_record("m2")],
            [model_record("m1", "0.8125"), model_record("m2", "0.9375"), model_record("new")],
        )
        message = scan_message(payload)
        for line in message.splitlines():
            if "https://" in line:
                self.assertNotRegex(line.split("https://", 1)[1], r"[\s。；]$")
        for marker in ("**", "# ", "](", "【退役公告与时间节点】", "渠道概览", "渠道定价来源", "Skill 更新检查"):
            self.assertNotIn(marker, message)


if __name__ == "__main__":
    unittest.main()
