"""Offline checks for official retirement evidence and milestone transitions."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.delta import scan_providers
from model_price.lifecycle import scan_lifecycle
from model_price.lifecycle_sources import (
    aliyun_events,
    event,
    kimi_events,
    openai_events,
    volcengine_events,
    xai_events,
)
from model_price.messages import scan_message
from model_price.snapshots import (
    SnapshotStore,
    parse_baseline_selection,
    require_moment,
)


class OfficialSourceTests(unittest.TestCase):
    def test_openai_single_segment_reasoning_id_is_kept(self):
        page = """## September 10, 2026
| Shutdown date | Model / system | Recommended replacement |
| --- | --- | --- |
| October 23, 2026 | `o1-2024-12-17` \\| `o1` | `gpt-5.6-sol` |
"""
        found = openai_events(page)
        self.assertEqual({item["model_id"] for item in found}, {"o1", "o1-2024-12-17"})
        self.assertTrue(all(item["source_url"].endswith(".md") for item in found))

    def test_aliyun_utc_offline_time_keeps_the_beijing_instant(self):
        found = aliyun_events(
            [
                {
                    "model_id": "qwen-example",
                    "source": {"url": "https://www.qianwenai.com/models/qwen-example"},
                    "model_metadata": {
                        "specifications": {"sunset_note": "2026-10-09T16:00:00Z 下线"}
                    },
                }
            ]
        )
        self.assertEqual(found[0]["eos_at"], "2026-10-10T00:00:00+08:00")

    def test_kimi_series_dates_follow_the_published_family_not_a_name_list(self):
        page = """## 已下线模型
> `kimi-k2.5` 已于 2026 年 8 月 31 日下线。
> `kimi-k2` 系列模型已于 2026 年 5 月 25 日下线。
| 模型名称 | 描述 |
| --- | --- |
| `kimi-k2.5` | 已下线 |
| `kimi-k2-thinking` | 已下线 |
"""
        found = {item["model_id"]: item for item in kimi_events(page)}
        self.assertEqual(found["kimi-k2.5"]["eos_at"], "2026-08-31")
        self.assertEqual(found["kimi-k2-thinking"]["eos_at"], "2026-05-25")

    def test_volcengine_two_column_system_replacement_is_a_redirect(self):
        page = """<table><tr><th>节点</th><th>时间</th></tr>
<tr><td>EOM</td><td>2026年9月24日 10:00</td></tr>
<tr><td>EOS</td><td>2026年10月22日 14:00</td></tr></table>
<table><tr><th>模型 ID</th><th>到期未迁移，系统替换模型</th></tr>
<tr><td>doubao-old-260101</td><td>doubao-new-260901</td></tr></table>"""
        found = volcengine_events(page)
        self.assertEqual(found[0]["replacement"], "doubao-new-260901")
        self.assertEqual(found[0]["end_behavior"], "redirect")

    def test_xai_retired_slug_is_a_redirect_at_the_published_pt_time(self):
        index = (
            "[Migration](https://docs.x.ai/developers/migration/may-15-retirement.md)"
        )
        page = """# Grok Model Retirement on May 15, 2026
Effective May 15, 2026 at 12:00 PM PT, these models will be retired.
| Model being retired | Redirect target after May 15 |
| --- | --- |
| `grok-3` | `grok-4.3` |
"""
        client = SimpleNamespace(get_text=lambda _: page)
        found = xai_events(client, index)
        self.assertEqual(found[0]["redirect_at"], "2026-05-15T12:00-07:00")
        self.assertIsNone(found[0]["eos_at"])


class LifecycleScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SnapshotStore(Path(self.temp.name))
        self.provider = SimpleNamespace(provider_id="openai", provider_name="OpenAI")
        self.selection = parse_baseline_selection(None)
        self.first = "2026-09-22T10:00:00+08:00"
        self.second = "2026-09-23T10:00:00+08:00"

    def scan(self, at, item):
        with mock.patch(
            "model_price.lifecycle.read_events",
            return_value=("https://official.example/notice", [item]),
        ):
            return scan_lifecycle(
                self.provider,
                object(),
                [],
                self.store,
                at,
                self.selection,
                require_moment(at),
            )

    def test_initial_notice_baseline_is_quiet_then_a_date_crossing_is_reported(self):
        item = event(
            "o1",
            "https://official.example/notice",
            announced_at="2026-09-20",
            eom_at="2026-09-23",
            eos_at="2026-10-01",
        )
        first = self.scan(self.first, item)
        second = self.scan(self.second, item)
        self.assertEqual(first["status"], "baseline_created")
        self.assertEqual(first["changes"], [])
        self.assertEqual(second["status"], "changed")
        self.assertEqual(
            [(x["kind"], x.get("milestone")) for x in second["changes"]],
            [("milestone_reached", "eom_at")],
        )

    def test_a_revised_date_is_reported_and_no_notice_is_erased_on_failure(self):
        old = event("o1", "https://official.example/notice", eos_at="2026-10-01")
        new = event("o1", "https://official.example/notice", eos_at="2026-10-03")
        self.scan(self.first, old)
        with mock.patch(
            "model_price.lifecycle.read_events",
            side_effect=RuntimeError("source unavailable"),
        ):
            failure = scan_lifecycle(
                self.provider,
                object(),
                [],
                self.store,
                self.second,
                self.selection,
                require_moment(self.second),
            )
        self.assertEqual(failure["status"], "source_error")
        self.assertEqual(self.store.read("lifecycle-openai")["captured_at"], self.first)
        fixed = self.scan("2026-09-24T10:00:00+08:00", new)
        self.assertEqual(fixed["changes"][0]["kind"], "date_revised")
        self.assertEqual(fixed["changes"][0]["before"], "2026-10-01")

    def test_same_model_on_two_hosts_has_independent_dates(self):
        self.scan(
            self.first,
            event(
                "deepseek-v4-flash",
                "https://official.example/openai",
                eos_at="2026-09-27",
            ),
        )
        other = SimpleNamespace(provider_id="baidu", provider_name="百度")
        with mock.patch(
            "model_price.lifecycle.read_events",
            return_value=(
                "https://official.example/baidu",
                [
                    event(
                        "deepseek-v4-flash",
                        "https://official.example/baidu",
                        eos_at="2026-09-29",
                    )
                ],
            ),
        ):
            report = scan_lifecycle(
                other,
                object(),
                [],
                self.store,
                self.first,
                self.selection,
                require_moment(self.first),
            )
        self.assertEqual(report["status"], "baseline_created")
        self.assertNotEqual(
            self.store.read("lifecycle-openai")["models"],
            self.store.read("lifecycle-baidu")["models"],
        )

    def test_an_older_notice_cannot_restore_a_superseded_date(self):
        newer = event("o1", "https://official.example/new", eos_at="2026-10-03")
        older = event("o1", "https://official.example/old", eos_at="2026-10-01")
        with mock.patch(
            "model_price.lifecycle.read_events",
            return_value=("https://official.example/index", [newer, older]),
        ):
            scan_lifecycle(
                self.provider,
                object(),
                [],
                self.store,
                self.first,
                self.selection,
                require_moment(self.first),
            )
        saved = self.store.read("lifecycle-openai")["models"]["API|o1"]
        self.assertEqual(saved["eos_at"], "2026-10-03")
        self.assertEqual(saved["source_url"], "https://official.example/new")

    def test_earliest_possible_date_has_no_actual_shutdown_claim(self):
        from model_price.reporting import lifecycle_change_text

        item = event(
            "gemini-example",
            "https://official.example/gemini",
            eos_at="2026-09-23",
            eos_earliest=True,
        )
        text = lifecycle_change_text(
            {"kind": "milestone_reached", "milestone": "eos_at", "event": item}
        )
        self.assertIn("实际下线待官方确认", text)

    def test_notice_change_is_visible_when_prices_hold_still(self):
        class PriceAdapter:
            provider_id = "openai"
            provider_name = "OpenAI"
            source_url = "https://official.example/pricing"
            source_kind = "official_markdown"

            def catalog_records(self):
                return [
                    {
                        "model_id": "o1",
                        "display_name": "o1",
                        "offers": [
                            {
                                "name": "standard",
                                "conditions": {},
                                "prices": [
                                    {
                                        "type": "input",
                                        "label": "input",
                                        "amount": "1",
                                        "unit": "USD_per_million_tokens",
                                    }
                                ],
                            }
                        ],
                    }
                ]

        class Descriptions:
            def resolve_many(self, targets):
                return [
                    {
                        "model_id": target["model_id"],
                        "display_name": target["display_name"],
                        "status": "not_found",
                        "note": "无独立介绍",
                        "attempted_sources": [],
                    }
                    for target in targets
                ]

        first = event("o1", "https://official.example/notice", eos_at="2026-10-01")
        second = event("o1", "https://official.example/notice", eos_at="2026-10-03")
        with mock.patch(
            "model_price.lifecycle.read_events",
            return_value=("https://official.example/notice", [first]),
        ):
            scan_providers(
                [PriceAdapter()],
                self.store,
                captured_at=self.first,
                lifecycle_client=object(),
                descriptions=Descriptions(),
            )
        with mock.patch(
            "model_price.lifecycle.read_events",
            return_value=("https://official.example/notice", [second]),
        ):
            payload = scan_providers(
                [PriceAdapter()],
                self.store,
                captured_at=self.second,
                lifecycle_client=object(),
                descriptions=Descriptions(),
            )
        report = payload["providers"][0]
        self.assertEqual(report["status"], "unchanged")
        self.assertEqual(report["lifecycle"]["status"], "changed")
        message = scan_message(payload)
        self.assertLess(
            message.index("【模型能力】"), message.index("【退役公告与时间节点】")
        )
        self.assertIn("2026-10-01 → 2026-10-03", message)
        self.assertIn("https://official.example/notice", message)

    def test_notice_change_survives_a_price_source_failure(self):
        class BrokenPriceAdapter:
            provider_id = "openai"
            provider_name = "OpenAI"
            source_url = "https://official.example/pricing"
            source_kind = "official_markdown"

            def catalog_records(self):
                raise RuntimeError("pricing unavailable")

        old = event("o1", "https://official.example/notice", eos_at="2026-10-01")
        new = event("o1", "https://official.example/notice", eos_at="2026-10-03")
        self.scan(self.first, old)
        with mock.patch(
            "model_price.lifecycle.read_events",
            return_value=("https://official.example/notice", [new]),
        ):
            result = scan_providers(
                [BrokenPriceAdapter()],
                self.store,
                captured_at=self.second,
                lifecycle_client=object(),
            )
        report = result["providers"][0]
        self.assertEqual(report["status"], "source_error")
        self.assertEqual(report["lifecycle"]["status"], "changed")
        self.assertIn("退役公告与时间节点", scan_message(result))


if __name__ == "__main__":
    unittest.main()
