"""Keep replacement retirement sources independent, literal, and failure-safe."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from audit_model_retirements import audit_provider
from model_price.delta import scan_providers
from model_price.errors import SourceError
from model_price.lifecycle import retired_model_ids, scan_lifecycle
from model_price.lifecycle_sources import (
    ALIYUN_NOTICE_API,
    ALIYUN_RETIRED_MODELS_URL,
    SOURCES,
    aliyun_notice,
    aliyun_retired_events,
    bedrock_events,
    read_events,
)
from model_price.reporting import lifecycle_schedule_text
from model_price.snapshots import SnapshotStore, parse_baseline_selection, require_moment

FIXTURES = Path(__file__).parent / "fixtures"


class FixtureClient:
    """Reject undeclared reads so audits cannot fall back to a live price API."""

    def __init__(self) -> None:
        self.documents = {
            SOURCES["aliyun"]: (FIXTURES / "aliyun-retirement-index.md").read_text(),
            ALIYUN_RETIRED_MODELS_URL: (FIXTURES / "aliyun-retired-models.md").read_text(),
            SOURCES["aws-bedrock"]: (FIXTURES / "bedrock-retirements.md").read_text(),
        }
        self.notices = json.loads((FIXTURES / "aliyun-retirement-notices.json").read_text())
        for identifier, payload in self.notices.items():
            self.documents[f"{ALIYUN_NOTICE_API}?language=zh&website=cn&id={identifier}"] = json.dumps(payload)
        self.calls: list[str] = []

    def get_text(self, url: str) -> str:
        self.calls.append(url)
        return self.documents[url]


class AliyunRetirementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FixtureClient()

    def notice(self, identifier: str) -> list[dict[str, Any]]:
        return aliyun_notice(self.client.notices[identifier], "https://www.aliyun.com/notice/" + identifier)

    def test_current_and_retired_ids_are_read_without_the_price_catalogue(self) -> None:
        source, events = read_events("aliyun", self.client, [])
        self.assertEqual(source, SOURCES["aliyun"])
        self.assertIn("qwen-turbo", {item["model_id"] for item in events})
        self.assertIn("qwen-max-2024-04-03", {item["model_id"] for item in events})
        self.assertEqual(len(self.client.calls), len(set(self.client.calls)))
        self.assertEqual(self.client.calls.count(ALIYUN_RETIRED_MODELS_URL), 1)

    def test_published_time_point_and_date_only_notices_keep_their_precision(self) -> None:
        self.assertEqual(self.notice("118177")[0]["eos_at"], "2026-10-10T00:00:00+08:00")
        self.assertEqual(self.notice("118177")[0]["announced_at"], "2026-04-13T16:49:54+08:00")
        self.assertEqual(self.notice("118434")[0]["eos_at"], "2026-10-10")

    def test_impact_window_does_not_invent_an_exact_shutdown_instant(self) -> None:
        detail = self.client.notices["118177"]["data"]["info"]["detailList"][0]
        impacts = json.loads(detail["impactTime"])
        impacts[0]["endTime"] = "2026-10-10 23:59:59"
        detail["impactTime"] = json.dumps(impacts)
        self.assertEqual(self.notice("118177")[0]["eos_at"], "2026-10-10")

    def test_a_start_time_without_a_time_point_type_leaves_the_prose_date_only(self) -> None:
        detail = self.client.notices["118177"]["data"]["info"]["detailList"][0]
        detail["impactTimeType"] = "not-set"
        self.assertEqual(self.notice("118177")[0]["eos_at"], "2026-10-10")

    def test_mainline_family_labels_are_not_literal_retired_model_ids(self) -> None:
        events = self.notice("118178")
        names = {item["model_id"] for item in events}
        self.assertIn("qwen-max-latest", names)
        self.assertIn("qwen-max-2025-01-25", names)
        self.assertNotIn("qwen-max", names)
        self.assertNotIn("qwen-turbo", names)

    def test_stacked_model_paragraphs_and_single_segment_ids_stay_literal(self) -> None:
        events = self.notice("118345")
        names = {item["model_id"] for item in events}
        self.assertEqual(len(events), 3)
        self.assertIn("qwen3-vl-8b-instruct", names)
        self.assertIn("qwen3-8b", names)
        self.assertNotIn("qwen3-vl-8b-instructqwen3-8b", names)
        self.assertEqual(self.notice("118434")[0]["model_id"], "aitryon")

    def test_recommended_models_are_manual_migrations_without_redirects(self) -> None:
        item = self.notice("118434")[0]
        self.assertEqual(item["replacement"], "qwen-image-3.0")
        self.assertEqual(item["end_behavior"], "unavailable")
        self.assertIsNone(item["redirect_at"])
        self.assertNotIn("qwen-image-3.0", {event["model_id"] for event in self.notice("118434")})

    def test_a_prose_shutdown_names_only_the_old_model(self) -> None:
        events = self.notice("118217")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["model_id"], "gte-rerank")
        self.assertEqual(events[0]["replacement"], "qwen3-rerank")

    def test_image_notices_use_dated_official_tabs_and_ignore_active_zero_limits(self) -> None:
        events = aliyun_retired_events(self.client.documents[ALIYUN_RETIRED_MODELS_URL])
        self.assertEqual({item["model_id"] for item in events}, {
            "qwen-max-2024-04-03", "qwen-max-2024-04-28", "qwen-max-0428",
        })
        self.assertTrue(all(item["notice_status"] == "retired" for item in events))
        self.assertTrue(all(item["eos_at"] == "2026-01-30" for item in events))
        self.assertTrue(all(item["source_url"] == ALIYUN_RETIRED_MODELS_URL for item in events))

    def test_an_image_notice_without_a_matching_tab_fails_the_whole_read(self) -> None:
        self.client.documents[ALIYUN_RETIRED_MODELS_URL] = self.client.documents[ALIYUN_RETIRED_MODELS_URL].replace("2026年1月30日", "2026年1月31日")
        with self.assertRaisesRegex(SourceError, "image-only notices"):
            read_events("aliyun", self.client, [])

    def test_an_unreadable_retired_row_is_not_silently_dropped(self) -> None:
        document = self.client.documents[ALIYUN_RETIRED_MODELS_URL].replace("qwen-max-2024-04-03", "")
        with self.assertRaisesRegex(SourceError, "unreadable model"):
            aliyun_retired_events(document)

    def test_wrong_bulletin_identity_language_or_status_is_not_accepted(self) -> None:
        original = self.client.notices["118177"]
        for mutation in ("identity", "language", "status", "body"):
            with self.subTest(mutation=mutation):
                payload = copy.deepcopy(original)
                info = payload["data"]["info"]
                if mutation == "identity":
                    info["id"] = 999999
                elif mutation == "language":
                    info["detailList"][0]["language"] = "en"
                elif mutation == "status":
                    payload["data"]["success"] = False
                else:
                    info["detailList"][0]["contentHtml"] = ""
                with self.assertRaises(SourceError):
                    aliyun_notice(payload, "https://www.aliyun.com/notice/118177")

    def test_unreadable_dates_and_missing_lists_do_not_become_empty_successes(self) -> None:
        detail = self.client.notices["118177"]["data"]["info"]["detailList"][0]
        for body in (
            "<p>百炼将于未知日期对下表中的模型进行下线处理。</p><ul><li>qwen-old</li></ul>",
            "<p>百炼将于2026年10月10日对下表中的模型进行下线处理。</p>",
            "<p>百炼将于2026年10月10日对下表中的模型进行下线处理。</p><table><tr><th>模型名称</th></tr><tr><td></td></tr></table>",
        ):
            detail["contentHtml"] = body
            with self.subTest(body=body), self.assertRaises(SourceError):
                self.notice("118177")

    def test_invalid_or_empty_indexes_are_source_errors(self) -> None:
        for index in ("<h1>文档首页</h1>", "# 模型下线机制说明\n## 下线模型列表\n暂无记录"):
            self.client.documents[SOURCES["aliyun"]] = index
            with self.subTest(index=index), self.assertRaises(SourceError):
                read_events("aliyun", self.client, [])

    def test_audit_does_not_need_a_price_api_client(self) -> None:
        report = audit_provider("aliyun", self.client, require_moment("2026-10-10T12:00:00+08:00"))
        self.assertEqual(report["status"], "verified")
        self.assertTrue(report["events"])


class BedrockRetirementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FixtureClient()
        self.events = bedrock_events(self.client.documents[SOURCES["aws-bedrock"]])

    def test_public_schedule_replaces_the_hardcoded_absence(self) -> None:
        source, events = read_events("aws-bedrock", self.client, [])
        self.assertEqual(source, SOURCES["aws-bedrock"])
        self.assertEqual(len(events), 14)
        self.assertEqual(self.client.calls, [source])

    def test_legacy_stops_new_adoption_but_is_not_an_announcement_or_shutdown(self) -> None:
        item = next(item for item in self.events if item["model_id"] == "ai21.jamba-1-5-large-v1:0")
        self.assertEqual(item["eom_at"], "2026-05-26")
        self.assertEqual(item["eos_at"], "2026-11-26")
        self.assertEqual(item["notice_status"], "scheduled")
        self.assertIsNone(item["announced_at"])
        self.assertIsNone(item["redirect_at"])
        self.assertEqual(retired_model_ids([item], require_moment("2026-10-10T12:00:00+08:00")), [])
        self.assertIn("us-east-1", item["scope"])
        self.assertIn("不含私有延长访问协议", item["scope"])
        self.assertNotIn("未公布停服日期", lifecycle_schedule_text(item))

    def test_markdown_history_keeps_separate_regions_for_one_namespaced_id(self) -> None:
        items = [item for item in self.events if item["model_id"] == "anthropic.claude-3-haiku-20240307-v1:0"]
        self.assertEqual(len(items), 2)
        self.assertEqual(len({item["scope"] for item in items}), 2)
        self.assertTrue(all(item["notice_status"] == "retired" for item in items))
        self.assertTrue(all(item["eos_at"] == "2026-09-10" for item in items))
        self.assertEqual(items[1]["eom_at"], "2026-03-10")

    def test_a_floor_is_not_a_confirmed_shutdown(self) -> None:
        document = """| Model ID | Regions | Legacy date | EOL date |
| --- | --- | --- | --- |
| vendor.model-v1:0 | us-east-1 | — | January 1, 2027 or later |
"""
        events = bedrock_events(document)
        self.assertTrue(events[0]["eos_earliest"])
        self.assertEqual(retired_model_ids(events, require_moment("2027-10-10T12:00:00+08:00")), [])

    def test_incomplete_rows_unknown_dates_and_lost_regional_scope_fail(self) -> None:
        for row in (
            "| vendor.model-v1:0 | us-east-1 |",
            "| vendor.model-v1:0 | us-east-1 | unreadable |",
            "| vendor.model-v1:0 | | September 10, 2026 |",
        ):
            document = "| Model ID | Regions | EOL date |\n| --- | --- | --- |\n" + row
            with self.subTest(row=row), self.assertRaises(SourceError):
                bedrock_events(document)

    def test_html_rowspans_also_keep_regions_and_ids_together(self) -> None:
        document = """<table><tr><th>Model ID</th><th>Regions</th><th>EOL date</th></tr>
<tr><td rowspan="2">vendor.model-v1:0</td><td>us-east-1</td><td>September 10, 2026</td></tr>
<tr><td>us-gov-west-1</td><td>October 10, 2026</td></tr></table>"""
        events = bedrock_events(document)
        self.assertEqual([item["model_id"] for item in events], ["vendor.model-v1:0"] * 2)
        self.assertEqual([item["eos_at"] for item in events], ["2026-09-10", "2026-10-10"])
        self.assertNotEqual(events[0]["scope"], events[1]["scope"])

    def test_a_network_failure_remains_a_source_error(self) -> None:
        def fail(url: str) -> str:
            raise SourceError("DNS unavailable: " + url)

        report = audit_provider("aws-bedrock", SimpleNamespace(get_text=fail), require_moment("2026-10-10T12:00:00+08:00"))
        self.assertEqual(report["status"], "source_error")
        self.assertIn("DNS unavailable", report["error"])


class RetirementIndependenceTests(unittest.TestCase):
    def test_price_catalogue_failure_does_not_suppress_aliyun_notices(self) -> None:
        def fail() -> list[dict[str, Any]]:
            raise SourceError("price API unavailable")

        adapter = SimpleNamespace(
            provider_id="aliyun", provider_name="阿里云百炼",
            source_url="https://www.qianwenai.com/models", source_kind="anonymous_api",
            catalog_records=fail,
        )
        with tempfile.TemporaryDirectory() as directory:
            payload = scan_providers([adapter], SnapshotStore(Path(directory)), captured_at="2026-10-10T12:00:00+08:00", lifecycle_client=FixtureClient())
        report = payload["providers"][0]
        self.assertEqual(report["status"], "source_error")
        self.assertEqual(report["lifecycle"]["status"], "baseline_created")
        self.assertIn("qwen-turbo", report["lifecycle"]["retired_model_ids"])

    def test_failed_and_empty_sources_preserve_successful_notice_history(self) -> None:
        for provider_id in ("aliyun", "aws-bedrock"):
            with self.subTest(provider_id=provider_id), tempfile.TemporaryDirectory() as directory:
                store = SnapshotStore(Path(directory))
                adapter = SimpleNamespace(provider_id=provider_id, provider_name=provider_id)
                first = "2026-10-09T12:00:00+08:00"
                second = "2026-10-10T12:00:00+08:00"
                selection = parse_baseline_selection(None)
                good = scan_lifecycle(adapter, FixtureClient(), None, store, first, selection, require_moment(first))
                self.assertEqual(good["status"], "baseline_created")
                archived = store.read("lifecycle-" + provider_id)
                for document in ("", "<h1>Documentation home</h1>"):
                    broken = SimpleNamespace(get_text=lambda url: document)
                    report = scan_lifecycle(adapter, broken, None, store, second, selection, require_moment(second))
                    self.assertEqual(report["status"], "source_error")
                    self.assertEqual(store.read("lifecycle-" + provider_id), archived)
                    self.assertEqual(report["notice_model_ids"], good["notice_model_ids"])

    def test_kling_without_a_public_model_timetable_does_not_request_a_console(self) -> None:
        self.assertEqual(read_events("kling", object(), []), (None, []))


if __name__ == "__main__":
    unittest.main()
