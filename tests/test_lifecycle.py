"""Offline checks for official retirement evidence and milestone transitions."""

from __future__ import annotations

import json
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
from audit_model_retirements import PROVIDERS, audit_provider
from model_price.lifecycle import retired_model_ids, scan_lifecycle
from model_price.lifecycle_sources import (
    aliyun_events,
    anthropic_events,
    baidu_events,
    deepseek_events,
    event,
    gemini_events,
    kimi_events,
    minimax_events,
    openai_events,
    read_events,
    tencent_events,
    volcengine_events,
    xai_events,
    xiaomi_events,
    zhipu_events,
)
from model_price.errors import SourceError
from model_price.messages import scan_message
from model_price.reporting import changed_descriptions
from model_price.snapshots import (
    SnapshotStore,
    parse_baseline_selection,
    require_moment,
)


class OfficialSourceTests(unittest.TestCase):
    def test_baidu_excludes_illustrative_rows_and_keeps_the_real_retirement(self):
        page = """## 完整模型退役历史记录
| 登记日期 | 退役模型版本 | 退役日期 | 推荐替换模型 |
| --- | --- | --- | --- |
| 2026-08-01 | ernie-old | 2026-09-24 | ernie-new |
| 2026-08-01 | ernie-example | 2026-09-24（示意） | ernie-new |
"""
        found = baidu_events(page)
        self.assertEqual([item["model_id"] for item in found], ["ernie-old"])
        self.assertEqual(found[0]["eos_at"], "2026-09-24")

    def test_tencent_requires_a_model_shutdown_notice_not_a_price_notice(self):
        index = """<a href="/announce/detail/2469">TokenHub 模型下线通知</a>
<a href="/announce/detail/2470">TokenHub 模型价格通知</a>"""
        page = """model 参数值：deepseek-old）北京时间 2026 年 9 月 24 日 10:00 起正式下线。系统将自动为您切换至 deepseek-new 模型。"""
        client = SimpleNamespace(get_text=lambda url: page)
        found = tencent_events(client, index)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["model_id"], "deepseek-old")
        self.assertEqual(found[0]["eos_at"], "2026-09-24T10:00+08:00")
        self.assertEqual(found[0]["replacement"], "deepseek-new")

    def test_deepseek_requires_explicit_old_version_withdrawal(self):
        page = "时间：2026/09/24。旧版本模型 deepseek-old 现已下线，模型名称更改为 deepseek-new。"
        found = deepseek_events(page)
        self.assertEqual([item["model_id"] for item in found], ["deepseek-old"])
        self.assertEqual(found[0]["eos_at"], "2026-09-24")

    def test_deepseek_reader_uses_the_working_trailing_slash_route(self):
        page = "时间：2026/09/24。旧版本模型 deepseek-old 现已下线，模型名称更改为 deepseek-new。"

        def get_text(url):
            self.assertTrue(url.endswith("/updates/"))
            return page

        _, found = read_events("deepseek", SimpleNamespace(get_text=get_text), [])
        self.assertEqual(found[0]["model_id"], "deepseek-old")

    def test_xiaomi_keeps_redirect_and_final_expiry_separate(self):
        page = """| Model ID | System replacement time | Deprecated Time | System Replacement Model |
| --- | --- | --- | --- |
| mimo-old | 2026-09-20 10:00 | 2026-10-01 10:00 | mimo-new |
"""
        found = xiaomi_events(page)
        self.assertEqual(found[0]["redirect_at"], "2026-09-20T10:00+08:00")
        self.assertEqual(found[0]["eos_at"], "2026-10-01T10:00+08:00")

    def test_anthropic_reads_retired_and_deprecated_but_not_active_rows(self):
        page = """| API model name | Current state | Deprecation date | Retirement date |
| --- | --- | --- | --- |
| claude-old | Retired | January 1, 2026 | September 24, 2026 |
| claude-soon | Deprecated | August 1, 2026 | October 1, 2026 |
| claude-live | Active | - | September 24, 2026 |
"""
        found = anthropic_events(page)
        self.assertEqual({x["model_id"] for x in found}, {"claude-old", "claude-soon"})
        self.assertEqual({x["model_id"]: x["notice_status"] for x in found}, {
            "claude-old": "retired", "claude-soon": "scheduled",
        })

    def test_google_earliest_possible_date_is_not_confirmed_retirement(self):
        page = """| Model | Release date | Shutdown date | Replacement |
| --- | --- | --- | --- |
| gemini-old | 2026-01-01 | 2026-09-24 | gemini-new |
"""
        found = gemini_events(page)
        self.assertTrue(found[0]["eos_earliest"])
        self.assertEqual(retired_model_ids(found, require_moment("2026-09-25T00:00:00+08:00")), [])

    def test_google_gray_rows_confirm_shutdown_even_without_a_date(self):
        page = """<table><tr><td>Model</td><td>Release date</td><td>Shutdown date</td></tr>
<tr class="row-gray"><td><code>gemini-retired-undated</code></td><td>2026-01-01</td><td>No shutdown date announced</td></tr>
<tr class="row-gray"><td><code>gemini-retired-dated</code></td><td>2026-01-01</td><td>September 30, 2026</td></tr>
<tr><td><code>gemini-scheduled</code></td><td>2026-01-01</td><td>September 1, 2026</td></tr></table>"""
        found = {item["model_id"]: item for item in gemini_events(page)}
        self.assertEqual(found["gemini-retired-undated"]["notice_status"], "retired")
        self.assertIsNone(found["gemini-retired-undated"]["eos_at"])
        self.assertEqual(
            retired_model_ids(found.values(), require_moment("2026-09-24T00:00:00+08:00")),
            ["gemini-retired-dated", "gemini-retired-undated"],
        )

    def test_zhipu_distinguishes_dated_redirect_retired_and_undated_plans(self):
        pages = {
            "https://docs.bigmodel.cn/flash": "GLM-4.5-Flash 将于2026 年 1 月 30 日下线。正式下线后，相关请求将会自动路由至 GLM-4.7-Flash。",
            "https://docs.bigmodel.cn/z1": "GLM-Z1 系列模型已下线，建议选择 GLM-4.6。",
            "https://docs.bigmodel.cn/4.5": "GLM-4.5、GLM-4.5-X 模型即将下线，建议选择 GLM-4.7。",
        }
        found = {item["model_id"]: item for item in zhipu_events(pages)}
        self.assertEqual(found["GLM-4.5-Flash"]["eos_at"], "2026-01-30")
        self.assertEqual(found["GLM-4.5-Flash"]["end_behavior"], "redirect")
        self.assertEqual(found["GLM-Z1"]["notice_status"], "retired")
        self.assertIsNone(found["GLM-4.5-X"]["eos_at"])

    def test_minimax_legacy_is_not_shutdown_but_free_music_ids_have_a_date(self):
        page = """<Accordion title="Legacy Models">
| Models | Description |
| --- | --- |
| [MiniMax-M2.5](/docs/model) | Legacy |
</Accordion>
<Note title="Music API Service Adjustment Notice">
Starting August 20, 2026, paid APIs remain for existing users. The free music generation APIs (Music-3.0-free, Music-2.6-free) will be discontinued.
</Note>
"""
        found = {item["model_id"]: item for item in minimax_events(page)}
        self.assertEqual(found["MiniMax-M2.5"]["notice_status"], "legacy")
        self.assertIsNone(found["MiniMax-M2.5"]["eos_at"])
        self.assertEqual(found["Music-3.0-free"]["eos_at"], "2026-08-20")
        self.assertEqual(
            retired_model_ids(found.values(), require_moment("2026-09-24T00:00:00+08:00")),
            ["Music-2.6-free", "Music-3.0-free"],
        )
        with self.assertRaises(SourceError):
            minimax_events(
                '<Note>Free music generation APIs (Music-3.0-free) '
                'will be discontinued.</Note>'
            )

    def test_volcengine_reader_uses_the_official_markdown_api(self):
        page = """| 节点 | 时间 |
| --- | --- |
| EOS | 2026年10月22日 14:00 |

| 模型 ID | 建议迁移模型 |
| --- | --- |
| doubao-old-260101 | doubao-new-260901 |
        """
        seen = []

        def get_text(url):
            seen.append(url)
            return json.dumps({"Result": {"MDContent": page}})

        client = SimpleNamespace(get_text=get_text)
        source, found = read_events("volcengine", client, [])
        self.assertIn("model-deprecation-notice", source)
        self.assertIn("getDocDetail", seen[0])
        self.assertEqual(found[0]["model_id"], "doubao-old-260101")

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

    def test_due_shutdown_marks_even_a_still_priced_model_delisted(self):
        class PriceAdapter:
            provider_id = "openai"
            provider_name = "OpenAI"
            source_url = "https://official.example/pricing"
            source_kind = "official_markdown"

            def __init__(self):
                self.amount = "1"

            def catalog_records(self):
                return [
                    {
                        "model_id": name,
                        "display_name": name,
                        "offers": [{
                            "name": "standard", "conditions": {},
                            "prices": [{
                                "type": "input", "label": "input",
                                "amount": self.amount,
                                "unit": "USD_per_million_tokens",
                            }],
                        }],
                    }
                    for name in ("live", "due", "future", "earliest")
                ]

        class Descriptions:
            def resolve_many(self, targets):
                return [
                    {"model_id": target["model_id"], "display_name": target["model_id"],
                     "status": "not_found", "note": "无独立介绍", "attempted_sources": []}
                    for target in targets
                ]

        notices = [
            event("due", "https://official.example/notice", eos_at="2026-09-23"),
            event("future", "https://official.example/notice", eos_at="2026-10-01"),
            event("earliest", "https://official.example/notice", eos_at="2026-09-23", eos_earliest=True),
        ]
        adapter = PriceAdapter()
        with mock.patch("model_price.lifecycle.read_events", return_value=("https://official.example/notice", notices)):
            scan_providers([adapter], self.store, captured_at=self.first, lifecycle_client=object())
            adapter.amount = "2"
            payload = scan_providers(
                [adapter], self.store, captured_at="2026-09-24T10:00:00+08:00",
                lifecycle_client=object(), descriptions=Descriptions(),
            )
        report = payload["providers"][0]
        self.assertEqual(report["model_availability"]["due"], "delisted")
        self.assertEqual(report["model_availability"]["future"], "listed")
        self.assertEqual(report["model_availability"]["earliest"], "listed")
        message = scan_message(payload)
        abilities = message.split("【模型能力】", 1)[1].split("【退役公告与时间节点】", 1)[0]
        prices = message.split("【模型价格】", 1)[1].split("【渠道结论】", 1)[0]
        self.assertEqual(abilities.count("【下架】"), 1)
        self.assertLess(abilities.index("【上架】"), abilities.index("【下架】"))
        self.assertIn("due", abilities)
        self.assertNotIn("due", prices)
        self.assertIn("live", prices)

    def test_canonical_description_uses_observed_retired_alias_state(self):
        payload = {"providers": [{
            "model_availability": {"deepseek-v4-flash": "delisted"},
            "model_descriptions": [{
                "model_id": "deepseek-flash",
                "canonical_model_id": "deepseek-flash",
                "observed_model_ids": ["deepseek-v4-flash"],
            }],
        }]}
        self.assertEqual(changed_descriptions(payload)[0]["availability"], "delisted")

    def test_future_notice_keeps_a_model_listed_without_a_price_row(self):
        class PriceAdapter:
            provider_id = "openai"
            provider_name = "OpenAI"
            source_url = "https://official.example/pricing"
            source_kind = "official_markdown"

            def catalog_records(self):
                return [{
                    "model_id": "priced", "display_name": "priced",
                    "offers": [{"name": "standard", "conditions": {}, "prices": [{
                        "type": "input", "label": "input", "amount": "1",
                        "unit": "USD_per_million_tokens",
                    }]}],
                }]

        first = event("notice-only", "https://official.example/notice", eos_at="2026-10-01")
        revised = event("notice-only", "https://official.example/notice", eos_at="2026-10-03")
        with mock.patch("model_price.lifecycle.read_events", return_value=("https://official.example/notice", [first])):
            scan_providers([PriceAdapter()], self.store, captured_at=self.first, lifecycle_client=object())
        with mock.patch("model_price.lifecycle.read_events", return_value=("https://official.example/notice", [revised])):
            payload = scan_providers([PriceAdapter()], self.store, captured_at=self.second, lifecycle_client=object())
        self.assertEqual(
            payload["providers"][0]["model_availability"]["notice-only"], "listed"
        )

    def test_retired_history_survives_notice_read_failure(self):
        item = event("o1", "https://official.example/notice", eos_at="2026-09-23")
        self.scan(self.first, item)
        with mock.patch("model_price.lifecycle.read_events", side_effect=RuntimeError("offline")):
            report = scan_lifecycle(
                self.provider, object(), [], self.store, self.second,
                self.selection, require_moment(self.second),
            )
        self.assertEqual(report["status"], "source_error")
        self.assertEqual(report["retired_model_ids"], ["o1"])

    def test_older_notice_archive_is_carried_into_the_new_shape_without_diffing(self):
        old = event("old", "https://official.example/old", eos_at="2026-09-20")
        self.store.write("lifecycle-openai", {
            "schema_version": 1, "lifecycle_schema_version": 1,
            "provider": {"id": "openai", "name": "OpenAI"},
            "captured_at": self.first,
            "source": {"url": "https://official.example/index"},
            "models": {"API|old": old},
        })
        new = event("new", "https://official.example/new", eos_at="2026-10-01")
        with mock.patch("model_price.lifecycle.read_events", return_value=("https://official.example/index", [new])):
            report = scan_lifecycle(
                self.provider, object(), [], self.store, self.second,
                self.selection, require_moment(self.second),
            )
        self.assertEqual(report["status"], "baseline_created")
        self.assertEqual(report["event_count"], 2)
        self.assertEqual(report["retired_model_ids"], ["old"])


class AuditScriptTests(unittest.TestCase):
    def test_every_price_provider_has_a_retirement_reader(self):
        self.assertEqual(len(PROVIDERS), 13)
        from model_price.lifecycle_sources import PARSERS, SOURCES
        self.assertEqual(set(PROVIDERS), set(SOURCES) | {"aliyun"})
        self.assertEqual(
            set(PROVIDERS) - {"aliyun", "tencent", "xai", "zhipu"},
            set(PARSERS),
        )

    def test_audit_keeps_exact_evidence_and_isolates_source_failure(self):
        page = """<Accordion title="Legacy Models">
| Models | Description |
| --- | --- |
| [MiniMax-M2.5](/docs/model) | Legacy |
</Accordion>
<Note title="Music API Service Adjustment Notice">
Starting August 20, 2026, the free music generation APIs (Music-3.0-free) will be discontinued.
</Note>
"""
        at = require_moment("2026-09-24T10:00:00+08:00")
        good = audit_provider("minimax", SimpleNamespace(get_text=lambda _: page), at)
        self.assertEqual(good["status"], "verified")
        events = {item["model_id"]: item for item in good["events"]}
        self.assertFalse(events["MiniMax-M2.5"]["confirmed_retired_as_of_scan"])
        self.assertTrue(events["Music-3.0-free"]["confirmed_retired_as_of_scan"])
        self.assertTrue(events["Music-3.0-free"]["source_url"].startswith("https://"))
        def broken_get_text(_):
            raise RuntimeError("offline")

        broken = audit_provider(
            "minimax", SimpleNamespace(get_text=broken_get_text), at
        )
        self.assertEqual(broken["status"], "source_error")


if __name__ == "__main__":
    unittest.main()
