"""Check Tencent response diagnostics and the published shutdown schedule variants.

A temporary unreadable page must remain a source error, and a new notice's clock
and literal ID must survive without guessing a fixed replacement or redirect.
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.core import HttpClient
from model_price.errors import SourceError
from model_price.lifecycle_sources import SOURCES, tencent_events, tencent_notice
from model_price.providers.tencent import (
    TENCENT_LIST_URL,
    TENCENT_PRICE_URL,
    TencentAdapter,
    extract_tencent_article,
)
from model_price.reporting import lifecycle_schedule_text


NOTICE_URL = "https://cloud.tencent.com/announce/detail/2496"
LABELLED_NOTICE = """下线时间：北京时间 2026 年 10 月 31 日 00:00:00 起。
下线模型：DeepSeek-V4-Flash 0731 正式版（deepseek-v4-flash-0731）。
本公告发布时，系列最新版模型为 DeepSeek-V4.1-Flash（deepseek-v4.1-flash）。
自北京时间 2026 年 10 月 30 日 14:20:01 起，系统将为尚未完成迁移且符合条件的用户，自动切换至实际切换时平台已上线的最新版模型。
不满足上述条件的用户，系统不会自动切换。
"""


class TencentSourceTests(unittest.TestCase):
    def test_a_missing_document_payload_names_the_failed_url_and_response_size(self):
        page = "<html><body>Temporarily unavailable</body></html>"
        with self.assertRaises(SourceError) as caught:
            extract_tencent_article(page, source_url=TENCENT_PRICE_URL)
        diagnostic = str(caught.exception)
        self.assertIn(TENCENT_PRICE_URL, diagnostic)
        self.assertIn(f"{len(page)} characters", diagnostic)
        self.assertIn("expected embedded article data", diagnostic)

    def test_catalogue_failure_diagnostics_name_the_catalogue_instead_of_prices(self):
        adapter = TencentAdapter(SimpleNamespace(get_text=lambda url: ""))
        with self.assertRaises(SourceError) as caught:
            adapter.catalog_records()
        self.assertIn(TENCENT_LIST_URL, str(caught.exception))
        self.assertIn("0 characters", str(caught.exception))

    def test_a_readable_empty_index_is_distinct_from_an_unparseable_notice(self):
        client = SimpleNamespace(get_text=lambda url: "<p>Unexpected page</p>")
        with self.assertRaises(SourceError) as caught:
            tencent_events(client, "<p>Unexpected index</p>")
        self.assertIn("index contained no readable", str(caught.exception))
        self.assertIn(SOURCES["tencent"], str(caught.exception))
        index = f'<a href="{NOTICE_URL}">TokenHub 模型下线通知</a>'
        with self.assertRaises(SourceError) as caught:
            tencent_events(client, index)
        self.assertIn("milestones could not be parsed", str(caught.exception))
        self.assertIn(NOTICE_URL, str(caught.exception))

    def test_seconds_and_a_separately_published_redirect_time_are_retained(self):
        page = """model 参数值：MiMo-V2.5-Pro）
北京时间 2026 年 10 月 20 日 23:59:59 起正式下线。
系统将自动为您切换至 MiMo-V2.6-Pro 模型。
自动切换将于北京时间 2026 年 10 月 19 日 10:00:01 执行。
"""
        item = tencent_notice(page, NOTICE_URL)[0]
        self.assertEqual(item["model_id"], "MiMo-V2.5-Pro")
        self.assertEqual(item["eos_at"], "2026-10-20T23:59:59+08:00")
        self.assertEqual(item["redirect_at"], "2026-10-19T10:00:01+08:00")
        self.assertEqual(item["replacement"], "MiMo-V2.6-Pro")

    def test_a_labelled_notice_keeps_the_literal_id_and_a_dynamic_target_unknown(self):
        found = tencent_notice(LABELLED_NOTICE, NOTICE_URL)
        self.assertEqual(len(found), 1)
        item = found[0]
        self.assertEqual(item["model_id"], "deepseek-v4-flash-0731")
        self.assertEqual(item["eos_at"], "2026-10-31T00:00:00+08:00")
        self.assertEqual(item["redirect_at"], "2026-10-30T14:20:01+08:00")
        self.assertIsNone(item["replacement"])
        self.assertEqual(item["end_behavior"], "redirect")
        self.assertIn("指定条件", item["scope"])

    def test_a_date_only_shutdown_does_not_invent_midnight_or_a_redirect(self):
        page = "下线时间：北京时间 2026 年 10 月 31 日。下线模型：示例（model-old）。"
        item = tencent_notice(page, NOTICE_URL)[0]
        self.assertEqual(item["eos_at"], "2026-10-31")
        self.assertIsNone(item["redirect_at"])
        self.assertEqual(item["end_behavior"], "unknown")

    def test_a_prose_redirect_to_a_future_latest_model_keeps_its_target_unknown(self):
        page = """model 参数值：model-old）
北京时间 2026 年 10 月 31 日 00:00 起正式下线。
系统将自动为您切换至实际切换时已上线的最新版模型。
"""
        item = tencent_notice(page, NOTICE_URL)[0]
        self.assertIsNone(item["replacement"])
        self.assertEqual(item["end_behavior"], "redirect")

    def test_an_earliest_schedule_is_not_an_asserted_shutdown(self):
        page = (
            "最早下线时间：北京时间 2026 年 10 月 31 日。下线模型：示例（model-old）。"
        )
        item = tencent_notice(page, NOTICE_URL)[0]
        self.assertTrue(item["eos_earliest"])

    def test_unique_notice_reads_leave_room_for_the_other_three_documents(self):
        requests = []

        def opener(request, *, timeout):
            requests.append(request.full_url)
            return io.BytesIO(LABELLED_NOTICE.encode())

        client = HttpClient(opener=opener, attempts=1, max_requests_per_host=20)
        for url in (TENCENT_LIST_URL, TENCENT_PRICE_URL, SOURCES["tencent"]):
            client.get_text(url)
        links = [
            f'<a href="/announce/detail/{index}">TokenHub 模型下线通知</a>'
            for index in range(20)
        ]
        index = links[0] * 20 + "".join(links)
        found = tencent_events(client, index)
        self.assertEqual(len(found), 17)
        self.assertEqual(len(requests), 20)
        self.assertEqual(len(set(requests)), 20)

    def test_the_report_does_not_drop_a_shutdowns_nonzero_seconds(self):
        item = tencent_notice(
            "model 参数值：model-old）北京时间 2026 年 10 月 20 日 23:59:59 起正式下线。",
            NOTICE_URL,
        )
        self.assertIn("2026-10-20 23:59:59（UTC+8）", lifecycle_schedule_text(item[0]))


if __name__ == "__main__":
    unittest.main()
