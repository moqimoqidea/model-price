import json
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.caching import CacheStore, CachedPriceSource
from model_price.core import PriceSource
from model_price.delta import scan_providers
from model_price.diffing import (
    BASELINE_CREATED,
    CHANGED,
    UNCHANGED,
    compare_snapshots,
)
from model_price.errors import SourceError
from model_price.models import (
    model_matches,
    normalize_model,
    strip_footnote_markers,
    trailing_parenthetical,
    without_trailing_parenthetical,
)
from model_price.parsing import (
    document_update_stamp,
    headed_document_tables,
    markdown_tables,
    split_markdown_row,
    time_bands_for,
    token_price_kind,
)
from model_price.pricing import (
    make_record,
    per_million_tokens,
    price_item,
    tokens_per_price_unit,
)
from model_price.providers.aliyun import (
    ALIYUN_BAND_DOC_URL,
    AliyunAdapter,
    pause_before_next_page,
    time_band_label,
)
from model_price.providers.baidu import (
    BAIDU_PAGE_URL,
    BaiduAdapter,
    price_data_url,
)
from model_price.providers.anthropic import ANTHROPIC_MARKDOWN_URL, AnthropicAdapter
from model_price.providers.deepseek import (
    DEEPSEEK_URL,
    DeepSeekAdapter,
    deepseek_news_url,
    recent_news_update,
)
from model_price.providers.google import (
    GEMINI_MARKDOWN_URL,
    GEMINI_URL,
    GeminiAdapter,
)
from model_price.providers.kimi import KIMI_INDEX_URL, KimiAdapter
from model_price.providers.openai import OPENAI_MARKDOWN_URL, OpenAIAdapter
from model_price.providers.tencent import (
    TENCENT_PRICE_URL,
    TencentAdapter,
    article_slate,
    expand_slate_table,
    extract_tencent_article,
    tencent_delivery_mode,
)
from model_price.providers.volcengine import (
    VOLCENGINE_DOC_API,
    VOLCENGINE_PAGE_URL,
    VolcengineAdapter,
)
from model_price.providers.xai import XAI_MARKDOWN_URL, XAI_URL, XAIAdapter
from model_price.providers.xiaomi import XIAOMI_URL, XiaomiAdapter
from model_price.providers.zhipu import ZHIPU_MARKDOWN_URL, ZHIPU_URL, ZhipuAdapter
from model_price.registry import (
    DOMESTIC_PROVIDER_IDS,
    OVERSEAS_PROVIDER_IDS,
    inferred_overseas_providers,
    query_adapters,
    select_compare_providers,
)
from model_price.budget import DEFAULT_MAX_CHARS, overage
from model_price.descriptions.core import (
    SUMMARY_MAX_CHARS,
    description_record,
)
from model_price.messages import band_lines, comparison_message, scan_message
from model_price.reporting import (
    format_moment,
    offer_condition_text,
    offering_text,
    price_lookup,
    shared_conditions,
)
from model_price.snapshots import (
    SnapshotStore,
    build_snapshot,
    offer_identity,
)
from model_price.updating import GitSkillUpdater

import query_model_prices


VOLC_MARKDOWN = """# 大语言模型

## 在线推理（常规）

|模型名称 |条件<br><br>输入长度：千 token |输入(非音频)<br><br>元/百万token |缓存存储<br><br>元/百万token/小时 |缓存命中(非音频)<br><br>元/百万token |输出<br><br>元/百万token |
|---|---|---|---|---|---|
|doubao\\-seed\\-2.0\\-pro |输入长度 [0, 32] |3.2 |0.017 |0.64 |16.0 |
||输入长度 (32, 128] |4.8 |0.017 |0.96 |24.0 |
|deepseek\\-v4\\-1\\-flash |空闲时段 |1.00 |0.017 |0.02 |4.00 |
||高峰时段 |2.00 |0.017 |0.04 |8.00 |
|deepseek\\-v4\\-flash正式版 |\\- |3.00 |0.017 |0.10 |9.00 |
|deepseek\\-v4\\-flash正式版<br><br>> 调整前价格，2026\\-08\\-21 起不适用 |\\- |1.00 |0.017 |0.20 |2.00 |
|deepseek\\-v4\\-pro预览版 |\\- |9.00 |0.017 |0.30 |27.00 |

## 批量推理

|模型名称 |输入(非音频)<br><br>元/百万token |输出<br><br>元/百万token |
|---|---|---|
|deepseek\\-v4\\-flash正式版 |1.50 |4.50 |

## 价格示例

|分辨率 |宽高比 |输入视频时长（秒） |输出视频时长（秒） |doubao\\-seedance 视频价格（元/个） |
|---|---|---|---|---|
|480p |16:9 |2~30 |5 |3.63 |
"""


def volc_payload(markdown=VOLC_MARKDOWN):
    return {
        "Result": {
            "ContentType": "json",
            "Content": json.dumps({"version": "test", "data": {}}),
            "MDContent": markdown,
            "UpdatedTime": "2026-09-14T03:03:07Z",
        }
    }


class MappingClient:
    def __init__(self, values):
        self.values = values

    def get_text(self, url):
        if url not in self.values:
            raise SourceError(f"unmapped test URL: {url}")
        value = self.values[url]
        if isinstance(value, Exception):
            raise value
        return value


class CountingSource(PriceSource):
    provider_id = "counting"
    provider_name = "Counting"
    source_url = "https://example.test/pricing"
    source_kind = "test"

    def __init__(self):
        self.calls = 0

    def list_models(self, prefix=""):
        self.calls += 1
        return ["model-a"]

    def query(self, model):
        self.calls += 1
        return [{"model_id": model}]


class FailingSource(CountingSource):
    def query(self, model):
        raise SourceError("offline")


class FakeGitRunner:
    def __init__(self, responses):
        self.responses = responses
        self.commands = []

    def __call__(self, command, **kwargs):
        arguments = tuple(command[1:])
        self.commands.append(arguments)
        returncode, stdout, stderr = self.responses[arguments]
        return SimpleNamespace(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )


# This is a single-skill repository, so it is cloned straight into a skills
# directory and the skill directory *is* the repository root. Its pathspec is
# therefore the whole tree, not a subdirectory.
SKILL_ROOT = Path("/repo")


def git_update_responses(status_output=""):
    before = "a" * 40
    after = "b" * 40
    return {
        ("rev-parse", "--show-toplevel"): (0, f"{SKILL_ROOT}\n", ""),
        (
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        ): (0, "origin/main\n", ""),
        ("fetch", "--quiet"): (0, "", ""),
        ("rev-parse", "HEAD"): (0, f"{before}\n", ""),
        ("rev-parse", "@{upstream}"): (0, f"{after}\n", ""),
        (
            "diff",
            "--quiet",
            f"{before}..{after}",
            "--",
            ".",
        ): (1, "", ""),
        ("merge-base", "--is-ancestor", before, after): (0, "", ""),
        ("status", "--porcelain"): (0, status_output, ""),
        ("merge", "--ff-only", "origin/main"): (0, "", ""),
    }


class MarkdownTableReaderTests(unittest.TestCase):
    def test_an_empty_leading_cell_survives_so_rows_stay_aligned(self):
        self.assertEqual(split_markdown_row("||输入长度 (32, 128] |4.8 |"), [
            "",
            "输入长度 (32, 128]",
            "4.8",
        ])
        self.assertEqual(split_markdown_row("| a | b |"), ["a", "b"])

    def test_vendor_escapes_are_removed_from_cells_and_headings(self):
        markdown = """## deepseek\\-v4系列价格调整

|模型名称 |输入<br><br>元/百万token |
|---|---|
|x |9.0 元 |
"""
        path, rows = markdown_tables(markdown)[0]
        self.assertEqual(path, ["deepseek-v4系列价格调整"])
        self.assertEqual(rows[0][0], "模型名称")

    def test_a_table_keeps_the_whole_heading_path(self):
        markdown = """# 大语言模型

## 在线推理（常规）

| a | b |
|---|---|
| 1 | 2 |
"""
        self.assertEqual(markdown_tables(markdown)[0][0], ["大语言模型", "在线推理（常规）"])


class HtmlTableReaderTests(unittest.TestCase):
    """The grid a vendor's cells have to be laid onto before any row is read."""

    def grid(self, table):
        return headed_document_tables(table)[0][1]

    def test_a_cell_spanning_rows_is_repeated_down_every_row_it_covers(self):
        self.assertEqual(
            self.grid(
                "<table>"
                "<tr><th>模型</th><th>峰谷</th><th>价格</th></tr>"
                '<tr><td rowspan="2">model-a</td><td>空闲</td><td>1</td></tr>'
                "<tr><td>高峰</td><td>2</td></tr>"
                "</table>"
            ),
            [
                ["模型", "峰谷", "价格"],
                ["model-a", "空闲", "1"],
                ["model-a", "高峰", "2"],
            ],
        )

    def test_a_heading_spanning_columns_is_not_repeated_into_them(self):
        # A colspan is how a vendor lays a row heading across the columns beside
        # it. Repeating it would fill the header row — the row that says which
        # columns are prices — with copies of the heading.
        self.assertEqual(
            self.grid(
                "<table>"
                '<tr><td colspan="2">模型</td><td>价格</td></tr>'
                "<tr><td>model-a</td><td>1</td></tr>"
                "</table>"
            ),
            [["模型", "价格"], ["model-a", "1"]],
        )

    def test_a_row_whose_tr_is_missing_is_still_read(self):
        # Baidu's own table drops the opening tag of one row; the cells below it
        # belong to that row, not to the one above.
        self.assertEqual(
            self.grid(
                "<table>"
                "<tr><th>模型</th><th>价格</th></tr>"
                "<tr><td>model-a</td><td>1</td></tr>"
                "<td>model-a</td><td>2</td></tr>"
                "</table>"
            ),
            [["模型", "价格"], ["model-a", "1"], ["model-a", "2"]],
        )

    def test_a_span_attribute_carrying_a_stray_quote_is_still_read(self):
        self.assertEqual(
            self.grid(
                "<table>"
                "<tr><th>模型</th><th>价格</th></tr>"
                '<tr><td rowspan=2">model-a</td><td>1</td></tr>'
                "<tr><td>2</td></tr>"
                "</table>"
            ),
            [["模型", "价格"], ["model-a", "1"], ["model-a", "2"]],
        )

    def test_a_cell_break_is_kept_so_stacked_values_stay_separable(self):
        self.assertEqual(
            self.grid(
                "<table>"
                "<tr><th>模型</th><th>价格</th></tr>"
                "<tr><td>model-a<br>model-a-air</td><td>1</td></tr>"
                "</table>"
            )[1][0],
            "model-a<br>model-a-air",
        )


class ModelMatchingTests(unittest.TestCase):
    def test_family_match_includes_versions_and_labels(self):
        self.assertTrue(model_matches("deepseek-v4-pro", "deepseek-v4-pro-0813"))
        self.assertTrue(model_matches("deepseek-v4-pro", "deepseek-v4-pro正式版"))
        self.assertTrue(model_matches("deepseek-v4-pro", "vanchin/deepseek-v4-pro"))
        self.assertFalse(model_matches("deepseek-v4-pro", "deepseek-v4-flash"))

    def test_exact_match_does_not_expand_family(self):
        self.assertFalse(
            model_matches("deepseek-v4-pro", "deepseek-v4-pro-0813", exact=True)
        )

    def test_a_live_model_is_found_under_every_vendor_label(self):
        # DeepSeek serves the model as `deepseek-flash`; Aliyun and Ark file it
        # under their own spelling of the same V4.1-Flash generation.
        self.assertTrue(model_matches("deepseek-flash", "deepseek-v4.1-flash"))
        self.assertTrue(model_matches("deepseek-flash", "deepseek-v4-1-flash"))
        self.assertTrue(model_matches("deepseek-v4.1-flash", "deepseek-flash"))
        self.assertFalse(
            model_matches("deepseek-flash", "deepseek-v4.1-flash", exact=True)
        )

    def test_a_superseded_generation_is_not_pulled_into_a_live_comparison(self):
        # `deepseek-v4-flash` is the retired name of the live model, so asking for
        # it must still reach `deepseek-flash`; the reverse must not claim the
        # older generation's price rows belong to the live model.
        self.assertTrue(model_matches("deepseek-v4-flash", "deepseek-flash"))
        self.assertFalse(model_matches("deepseek-flash", "deepseek-v4-flash"))
        self.assertFalse(model_matches("deepseek-flash", "deepseek-v4-flash-0731"))

    def test_tencent_delivery_modes_are_distinct(self):
        self.assertEqual(
            tencent_delivery_mode("DeepSeek-V4-Pro 原厂直供"),
            "upstream_direct",
        )
        self.assertEqual(tencent_delivery_mode("DeepSeek-V4-Pro"), "self_deployed")

    def test_a_trailing_annotation_never_reaches_the_model_id(self):
        # OpenAI and Anthropic file a status note in the name's parenthetical, so
        # the API id is the name without it. Reading the two halves apart is one
        # implementation shared by every adapter that meets such a label.
        self.assertEqual(without_trailing_parenthetical("gpt-5 (deprecated)"), "gpt-5")
        self.assertEqual(without_trailing_parenthetical("gpt-5"), "gpt-5")
        self.assertEqual(trailing_parenthetical("gpt-5"), "")

    def test_a_trailing_annotation_reads_back_as_the_note_it_carries(self):
        # Baidu states the settlement date there, and it is the only thing telling
        # two otherwise identical price sets apart.
        self.assertEqual(
            trailing_parenthetical("命中缓存（高峰时段：8:00-22:00，9月9日起生效）"),
            "高峰时段：8:00-22:00，9月9日起生效",
        )


class OfficialUpdateStampTests(unittest.TestCase):
    def test_rendered_html_dates_share_one_parser(self):
        self.assertEqual(
            document_update_stamp(
                "<div>最近更新时间：2026-09-18 22:04:00</div>",
                utc_offset="+08:00",
            ),
            "2026-09-18T22:04:00+08:00",
        )
        self.assertEqual(
            document_update_stamp(
                "<div>更新时间<!-- -->：<!-- -->2026-09-16</div>",
                utc_offset="+08:00",
            ),
            "2026-09-16",
        )
        self.assertEqual(
            document_update_stamp(
                "<div>更新时间 2026 年 08 月 06 日</div>",
                utc_offset="+08:00",
            ),
            "2026-08-06",
        )

    def test_tencent_article_keeps_its_recent_release_time(self):
        slate = [{"type": "paragraph", "children": [{"text": "价格"}]}]
        state = {
            "loaderData": {
                "product-article": {
                    "data": {
                        "article": {
                            "content": {
                                "slate": json.dumps(slate),
                                "recentReleaseTime": "2026-09-18 22:04:00",
                            }
                        }
                    }
                }
            }
        }
        quoted = json.dumps(json.dumps(state))
        article = extract_tencent_article(
            f"window.__staticRouterHydrationData = JSON.parse({quoted})"
        )
        self.assertEqual(article["recentReleaseTime"], "2026-09-18 22:04:00")
        self.assertEqual(article_slate(article), slate)
        adapter = TencentAdapter(
            MappingClient(
                {
                    TENCENT_PRICE_URL: (
                        f"window.__staticRouterHydrationData = JSON.parse({quoted})"
                    )
                }
            )
        )
        self.assertEqual(
            adapter.source_updated_at(),
            "2026-09-18T22:04:00+08:00",
        )


class StructuredDocumentTests(unittest.TestCase):
    def adapter(self):
        return VolcengineAdapter(
            MappingClient({VOLCENGINE_DOC_API: json.dumps(volc_payload())})
        )

    def test_volcengine_reads_its_official_markdown(self):
        record = self.adapter().query("deepseek-v4-flash正式版")[0]
        self.assertEqual(record["display_name"], "deepseek-v4-flash正式版")
        self.assertEqual(record["source"]["url"], VOLCENGINE_PAGE_URL)
        self.assertEqual(record["source_api"], VOLCENGINE_DOC_API)
        self.assertEqual(record["source_updated_at"], "2026-09-14T03:03:07Z")
        self.assertEqual(record["delivery_mode"], "platform_hosted")
        self.assertEqual(record["currency"], "CNY")
        self.assertEqual(price_lookup(record["offers"][0], "input")["amount"], "3.00")

    def test_cache_storage_is_billed_per_hour(self):
        offer = self.adapter().query("deepseek-v4-flash正式版")[0]["offers"][0]
        self.assertEqual(
            price_lookup(offer, "cache_storage")["unit"],
            "CNY_per_million_tokens_per_hour",
        )

    def test_a_model_is_carried_across_its_context_tier_rows(self):
        record = self.adapter().query("doubao-seed-2.0-pro")[0]
        self.assertEqual(
            [offer["conditions"]["context_tier"] for offer in record["offers"]],
            ["输入长度 [0, 32]", "输入长度 (32, 128]"],
        )
        self.assertEqual(price_lookup(record["offers"][1], "input")["amount"], "4.8")

    def test_a_time_band_cell_keeps_the_two_bands_apart(self):
        offers = self.adapter().query("deepseek-v4-1-flash")[0]["offers"]
        self.assertEqual(
            [
                (offer["conditions"]["time_band"], price_lookup(offer, "input")["amount"])
                for offer in offers
            ],
            [("空闲时段", "1.00"), ("高峰时段", "2.00")],
        )
        self.assertEqual(price_lookup(offers[1], "output")["amount"], "8.00")

    def test_a_superseded_price_keeps_its_note(self):
        offers = self.adapter().query("deepseek-v4-flash正式版")[0]["offers"]
        self.assertEqual(offers[0]["conditions"].get("model_note"), None)
        self.assertEqual(
            offers[1]["conditions"]["model_note"], "调整前价格，2026-08-21 起不适用"
        )

    def test_a_batch_table_becomes_its_own_offer(self):
        record = self.adapter().query("deepseek-v4-flash正式版")[0]
        self.assertEqual([offer["name"] for offer in record["offers"]],
                         ["online_standard", "online_standard", "batch"])

    def test_a_preview_model_is_marked_as_such(self):
        offer = self.adapter().query("deepseek-v4-pro预览版")[0]["offers"][0]
        self.assertEqual(offer["conditions"]["release_stage"], "preview")

    def test_a_table_without_a_model_column_is_not_a_catalogue(self):
        self.assertNotIn("480p", self.adapter().list_models())
        self.assertEqual(self.adapter().query("480p"), [])

    def test_tencent_rowspan_placeholders_keep_columns_aligned(self):
        def cell(value, row_span=None, col_span=None):
            node = {
                "type": "cell",
                "children": [{"type": "p", "children": [{"text": value}]}],
            }
            if row_span is not None:
                node["rowSpan"] = row_span
            if col_span is not None:
                node["colSpan"] = col_span
            return node

        table = {
            "children": [
                {"type": "row", "children": [cell("模型"), cell("峰谷"), cell("输入")]},
                {
                    "type": "row",
                    "children": [cell("model-a", 2), cell("空闲"), cell("1")],
                },
                {"type": "row", "children": [cell("", 0, 0), cell("高峰"), cell("2")]},
            ]
        }
        self.assertEqual(
            expand_slate_table(table),
            [
                ["模型", "峰谷", "输入"],
                ["model-a", "空闲", "1"],
                ["model-a", "高峰", "2"],
            ],
        )


class CacheTests(unittest.TestCase):
    def test_cache_is_provider_scoped_and_expires_after_three_hours(self):
        current = [datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc)]
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheStore(Path(directory), clock=lambda: current[0])
            source = CountingSource()
            adapter = CachedPriceSource(source, cache)

            self.assertEqual(adapter.list_models(), ["model-a"])
            self.assertEqual(adapter.cache_status, "miss")
            self.assertEqual(adapter.list_models(), ["model-a"])
            self.assertEqual(adapter.cache_status, "hit")
            self.assertEqual(source.calls, 1)

            other_source = CountingSource()
            other_source.provider_id = "other"
            other = CachedPriceSource(other_source, cache)
            self.assertEqual(other.list_models(), ["model-a"])
            self.assertEqual(other.cache_status, "miss")
            self.assertEqual(other_source.calls, 1)

            current[0] += timedelta(hours=3, seconds=1)
            self.assertEqual(adapter.list_models(), ["model-a"])
            self.assertEqual(adapter.cache_status, "miss")
            self.assertEqual(source.calls, 2)

    def test_refresh_bypasses_a_fresh_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheStore(Path(directory))
            source = CountingSource()
            CachedPriceSource(source, cache).query("model-a")
            refreshed = CachedPriceSource(source, cache, refresh=True)
            refreshed.query("model-a")
            self.assertEqual(refreshed.cache_status, "refreshed")
            self.assertEqual(source.calls, 2)

    def test_expired_cache_does_not_hide_a_refresh_failure(self):
        current = [datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc)]
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheStore(Path(directory), clock=lambda: current[0])
            CachedPriceSource(CountingSource(), cache).query("model-a")
            current[0] += timedelta(hours=3, seconds=1)
            adapter = CachedPriceSource(FailingSource(), cache)
            with self.assertRaises(SourceError):
                adapter.query("model-a")
            self.assertEqual(adapter.cache_status, "refresh_failed")


class SkillUpdateTests(unittest.TestCase):
    def test_remote_skill_change_is_fetched_then_fast_forwarded(self):
        runner = FakeGitRunner(git_update_responses())

        result = GitSkillUpdater(SKILL_ROOT, runner=runner).update()

        self.assertEqual(result["status"], "updated")
        self.assertLess(
            runner.commands.index(("fetch", "--quiet")),
            runner.commands.index(("merge", "--ff-only", "origin/main")),
        )

    def test_local_changes_prevent_automatic_update(self):
        runner = FakeGitRunner(git_update_responses(" M local.txt\n"))

        result = GitSkillUpdater(SKILL_ROOT, runner=runner).update()

        self.assertEqual(result["status"], "update_skipped")
        self.assertNotIn(("merge", "--ff-only", "origin/main"), runner.commands)

    def test_fetch_failure_is_reported_without_attempting_a_merge(self):
        responses = git_update_responses()
        responses[("fetch", "--quiet")] = (1, "", "network unavailable")
        runner = FakeGitRunner(responses)

        result = GitSkillUpdater(SKILL_ROOT, runner=runner).update()

        self.assertEqual(result["status"], "check_failed")
        self.assertIn("network unavailable", result["reason"])
        self.assertNotIn(("merge", "--ff-only", "origin/main"), runner.commands)


class OverseasRoutingTests(unittest.TestCase):
    def test_domestic_query_does_not_select_overseas_sources(self):
        adapters = {
            provider: object()
            for provider in (*DOMESTIC_PROVIDER_IDS, *OVERSEAS_PROVIDER_IDS)
        }
        selected = select_compare_providers(adapters, "deepseek-v4-pro")
        self.assertEqual(len(selected), len(DOMESTIC_PROVIDER_IDS))
        self.assertNotIn(adapters["openai"], selected)

    def test_model_name_selects_only_its_relevant_overseas_provider(self):
        self.assertEqual(inferred_overseas_providers("gpt-5"), ("openai",))
        self.assertEqual(inferred_overseas_providers("claude-sonnet-5"), ("anthropic",))
        self.assertEqual(inferred_overseas_providers("gemini-2.5-pro"), ("google",))
        self.assertEqual(inferred_overseas_providers("grok-4"), ("xai",))

    def test_unavailable_overseas_source_is_reported_as_source_error(self):
        adapter = OpenAIAdapter(
            MappingClient({OPENAI_MARKDOWN_URL: SourceError("blocked")})
        )
        payload = query_adapters([adapter], "gpt-5")
        self.assertEqual(payload["source_checks"][0]["status"], "source_error")


class OverseasParserTests(unittest.TestCase):
    def test_openai_markdown_parser(self):
        markdown = """### Standard pricing data
| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-test | $1.00 | $0.10 | - | $4.00 | $2.00 | $0.20 | - | $6.00 |
"""
        adapter = OpenAIAdapter(MappingClient({OPENAI_MARKDOWN_URL: markdown}))
        record = adapter.query("gpt-test")[0]
        self.assertEqual(record["currency"], "USD")
        self.assertEqual(len(record["offers"]), 2)
        self.assertEqual(record["offers"][0]["prices"][0]["amount"], "1.00")

    def test_anthropic_markdown_parser(self):
        markdown = """## Model pricing
| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| --- | --- | --- | --- | --- | --- |
| Claude Test 1 | $2 / MTok | $2.50 / MTok | $4 / MTok | $0.20 / MTok | $10 / MTok |
"""
        adapter = AnthropicAdapter(MappingClient({ANTHROPIC_MARKDOWN_URL: markdown}))
        record = adapter.query("claude-test-1")[0]
        self.assertEqual(record["offers"][0]["prices"][-1]["amount"], "10")

    def test_gemini_markdown_parser_uses_paid_tier(self):
        adapter = GeminiAdapter(MappingClient({GEMINI_MARKDOWN_URL: GEMINI_MARKDOWN}))
        record = adapter.query("gemini-test")[0]
        self.assertEqual(record["offers"][0]["conditions"]["billing_tier"], "paid")
        self.assertEqual(record["offers"][0]["prices"][0]["amount"], "0.50")


XAI_MARKDOWN = """# Pricing

### Text API Pricing

| Model | Context | Input / 1M tokens | Cached input / 1M tokens | Output / 1M tokens |
| --- | --- | --- | --- | --- |
| grok-test (< 200k prompt tokens) | 500k | $2.00 | $0.50 | $6.00 |
| grok-test (≥ 200k prompt tokens) | 500k | $4.00 | $1.00 | $12.00 |
| future-family-1 | 256k | $1.25 | $0.20 | $2.50 |

### Imagine Pricing

| Model | Cost |
| --- | --- |
| grok-imagine-image | $0.02 / image |
| grok-imagine-video | $0.050 / sec |
"""


class XAIAdapterTests(unittest.TestCase):
    def adapter(self):
        return XAIAdapter(MappingClient({XAI_MARKDOWN_URL: XAI_MARKDOWN}))

    def test_official_markdown_source_is_used(self):
        self.assertEqual(XAIAdapter.source_url, XAI_MARKDOWN_URL)
        self.assertEqual(XAIAdapter.source_kind, "official_markdown")
        self.assertEqual(XAI_MARKDOWN_URL, f"{XAI_URL}.md")

    def test_token_table_is_read_without_a_model_allowlist(self):
        models = self.adapter().list_models()
        self.assertEqual(models, ["future-family-1", "grok-test"])

    def test_long_context_tier_is_kept_as_a_condition(self):
        record = self.adapter().query("grok-test")[0]
        tiers = [offer["conditions"]["context_tier"] for offer in record["offers"]]
        self.assertEqual(tiers, ["< 200k prompt tokens", "≥ 200k prompt tokens"])
        short, long = record["offers"]
        self.assertEqual(price_lookup(short, "input")["amount"], "2.00")
        self.assertEqual(price_lookup(long, "input")["amount"], "4.00")
        self.assertEqual(record["currency"], "USD")

    def test_non_token_tables_are_not_read_as_model_prices(self):
        models = self.adapter().list_models()
        self.assertNotIn("grok-imagine-image", models)
        self.assertEqual(self.adapter().query("grok-imagine-image"), [])


GEMINI_MARKDOWN = """### Free

For developers and small projects getting started.

## Gemini Test

*[`gemini-test`](https://ai.google.dev/gemini-api/docs/models/gemini-test)*

### Standard

|   | Free Tier | Paid Tier, per 1M tokens in USD |
|---|---|---|
| Input price | Free of charge | $0.50 |
| Output price (including thinking tokens) | Free of charge | $2.00 |
| Context caching price | Free of charge | $0.25 |

### Batch

|   | Free Tier | Paid Tier, per 1M tokens in USD |
|---|---|---|
| Input price | Not available | $0.25 |

## [Gemma Test](https://ai.google.dev/gemma/docs/core/model_card)

|   | Free Tier | Paid Tier, per 1M tokens in USD |
|---|---|---|
| Input price | Free of charge | Not available |

## Pricing for tools

|   | Free Tier | Paid Tier, per 1M tokens in USD |
|---|---|---|
| Google Search | 500 RPD free | $9.99 |

## Notes

- Nothing here is priced per token.
"""


class GeminiAdapterTests(unittest.TestCase):
    def adapter(self, markdown=GEMINI_MARKDOWN):
        return GeminiAdapter(MappingClient({GEMINI_MARKDOWN_URL: markdown}))

    def test_official_markdown_source_is_used(self):
        self.assertEqual(GeminiAdapter.source_url, GEMINI_URL)
        self.assertEqual(GeminiAdapter.source_kind, "official_markdown")
        self.assertEqual(GEMINI_MARKDOWN_URL, f"{GEMINI_URL}.md.txt")

    def test_paid_tier_is_read_and_the_free_tier_is_not(self):
        record = self.adapter().query("gemini-test")[0]
        self.assertEqual(record["currency"], "USD")
        self.assertEqual(record["model_id"], "gemini-test")
        self.assertEqual(record["region"], "全球")
        standard = next(o for o in record["offers"] if o["name"] == "standard")
        self.assertEqual(standard["conditions"]["billing_tier"], "paid")
        self.assertEqual(price_lookup(standard, "input")["amount"], "0.50")
        self.assertEqual(price_lookup(standard, "output")["amount"], "2.00")
        self.assertEqual(price_lookup(standard, "cache_hit")["amount"], "0.25")

    def test_each_tier_becomes_its_own_offer(self):
        record = self.adapter().query("gemini-test")[0]
        self.assertEqual([offer["name"] for offer in record["offers"]], ["standard", "batch"])
        batch = next(o for o in record["offers"] if o["name"] == "batch")
        self.assertEqual(price_lookup(batch, "input")["amount"], "0.25")
        self.assertEqual(price_lookup(batch, "output"), None)

    def test_cache_storage_stays_a_separate_price(self):
        markdown = """## Gemini Cache Test

*[`gemini-cache-test`](https://ai.google.dev/gemini-api/docs/models/gemini-cache-test)*

### Standard

|   | Free Tier | Paid Tier, per 1M tokens in USD |
|---|---|---|
| Context caching price | Free of charge | $0.075 $0.50 / 1,000,000 tokens per hour (storage price) |
"""
        offer = self.adapter(markdown).query("gemini-cache-test")[0]["offers"][0]
        storage = price_lookup(offer, "cache_storage")
        self.assertEqual(storage["amount"], "0.50")
        self.assertEqual(storage["unit"], "USD_per_million_tokens_per_hour")

    def test_model_ids_come_from_the_link_line_the_page_publishes(self):
        markdown = """## Veo Test

*[`veo-test-generate`](https://example.test/a), [`veo-test-fast-generate`](https://example.test/b)*

|   | Free Tier | Paid Tier, per second in USD |
|---|---|---|
| Veo video price | Not available | $0.40 |
"""
        adapter = self.adapter(markdown)
        self.assertEqual(
            adapter.list_models(), ["veo-test-fast-generate", "veo-test-generate"]
        )
        records = adapter.search("veo-test")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["model_id"], "veo-test-generate")
        self.assertEqual(
            records[0]["model_aliases"],
            ["veo-test-generate", "veo-test-fast-generate"],
        )
        self.assertEqual(records[0]["offers"], [])

    def test_overview_sections_are_not_models(self):
        adapter = self.adapter()
        self.assertEqual(adapter.list_models(), ["gemini-test", "gemma-test"])
        self.assertEqual(adapter.query("pricing-for-tools"), [])
        self.assertEqual(adapter.query("notes"), [])


XIAOMI_HTML = """<div>更新时间<!-- --> <!-- -->2026 年 08 月 06 日</div>
<h2>模型国内定价</h2>
<table>
<tr><th>MiMo-V2.5 系列</th><th>输入（命中缓存）</th><th>输入（未命中缓存）</th><th>输出</th></tr>
<tr><td>mimo-v2.5-pro</td><td>¥0.025</td><td>¥3.00</td><td>¥6.00</td></tr>
<tr><td>mimo-v2.5</td><td>¥0.02</td><td>¥1.00</td><td>¥2.00</td></tr>
</table>
<table>
<tr><th>ASR 系列</th><th>输入音频时长</th></tr>
<tr><td>mimo-v2.5-asr</td><td>¥0.5 /小时</td></tr>
</table>
<h2>模型海外定价</h2>
<table>
<tr><th>MiMo-V2.5 系列</th><th>输入（命中缓存）</th><th>输入（未命中缓存）</th><th>输出</th></tr>
<tr><td>mimo-v2.5-pro</td><td>$0.0036</td><td>$0.435</td><td>$0.87</td></tr>
<tr><td>mimo-v2.5</td><td>$0.0028</td><td>$0.14</td><td>$0.28</td></tr>
</table>
<h2>联网服务插件定价</h2>
<table>
<tr><th>服务项</th><th>价格</th><th>说明</th></tr>
<tr><td>国内联网服务</td><td>¥16 /1000 次</td><td>包含网页搜索和网页解析</td></tr>
</table>
"""


class XiaomiAdapterTests(unittest.TestCase):
    def adapter(self):
        return XiaomiAdapter(MappingClient({XIAOMI_URL: XIAOMI_HTML}))

    def test_product_line_header_still_identifies_the_model_column(self):
        self.assertEqual(self.adapter().list_models(), ["mimo-v2.5", "mimo-v2.5-pro"])

    def test_cache_miss_maps_to_input_and_cache_hit_stays_separate(self):
        offer = self.adapter().query("mimo-v2.5")[0]["offers"][0]
        self.assertEqual(price_lookup(offer, "input")["amount"], "1.00")
        self.assertEqual(price_lookup(offer, "cache_hit")["amount"], "0.02")
        self.assertEqual(price_lookup(offer, "output")["amount"], "2.00")

    def test_overseas_table_is_not_labelled_as_cny(self):
        adapter = self.adapter()
        record = adapter.query("mimo-v2.5-pro")[0]
        self.assertEqual(record["currency"], "CNY")
        self.assertEqual(price_lookup(record["offers"][0], "input")["amount"], "3.00")
        amounts = {
            item["amount"] for offer in record["offers"] for item in offer["prices"]
        }
        self.assertNotIn("0.435", amounts)

    def test_audio_duration_table_is_not_read_as_token_pricing(self):
        self.assertEqual(self.adapter().query("mimo-v2.5-asr"), [])

    def test_plugin_pricing_section_is_ignored(self):
        self.assertEqual(self.adapter().query("国内联网服务"), [])

    def test_the_page_update_date_is_kept_on_every_record(self):
        self.assertEqual(
            self.adapter().query("mimo-v2.5")[0]["source_updated_at"],
            "2026-08-06",
        )


class PriceUnitTests(unittest.TestCase):
    def test_a_rate_per_thousand_tokens_is_restated_per_million(self):
        self.assertEqual(per_million_tokens("0.003", 1_000), "3")
        self.assertEqual(per_million_tokens("0.00005", 1_000), "0.05")

    def test_a_unit_that_does_not_price_tokens_is_rejected(self):
        self.assertIsNone(tokens_per_price_unit("元/页"))
        self.assertIsNone(tokens_per_price_unit("元/次"))

    def test_each_token_scale_is_read_as_its_own_multiple(self):
        self.assertEqual(tokens_per_price_unit("元/千tokens"), 1_000)
        self.assertEqual(tokens_per_price_unit("元/百万 tokens"), 1_000_000)


# Baidu prices one billing item per serving channel, keeps the peak/off-peak
# window inside the item's own text, and writes the fourth row below without its
# opening <tr> — the fixture reproduces all three.
BAIDU_HTML = """<h2>模型价格</h2>

<h3>文本生成</h3>

<table>
<tr><th>模型名称</th><th>版本名称</th><th>服务内容</th><th>子项</th><th>在线推理</th><th>批量推理 （原价）</th><th>批量推理 （2月活动价）</th><th>单位</th></tr>
<tr>
<td rowspan="4">Flash-Test（空闲时段为限时活动，8月25日0点起生效）</td>
<td rowspan="4">Flash-Test-0731<br>Flash-Test-0731-Air</td>
<td rowspan="4">推理服务</td>
<td>输入（高峰时段：8:00-22:00）</td>
<td>0.003</td><td>0.0015</td><td>0.001</td><td>元/千tokens</td>
</tr>
<tr>
<td>命中缓存（高峰时段：8:00-22:00，9月9日起生效）</td>
<td>0.0001</td><td>-</td><td>-</td><td>元/千tokens</td>
</tr>
<td>输出（空闲时段：22:00-次日8:00）</td>
<td>0.0045</td><td>-</td><td>-</td><td>元/千tokens</td>
</tr>
<tr>
<td>输入（空闲时段：22:00-次日8:00）</td>
<td>0.0015</td><td>-</td><td>-</td><td>元/千tokens</td>
</tr>
</table>

<h3>按TPM付费</h3>

<table>
<tr><th>模型名称</th><th>版本名称</th><th>子项</th><th>单位规格</th><th>预付费价格（单位：元/个/月）</th></tr>
<tr><td>Flash-Test</td><td>Flash-Test-0731</td><td>输入</td><td>10</td><td>100</td></tr>
</table>

<h3>OCR</h3>

<table>
<tr><th>模型名称</th><th>版本名称</th><th>服务内容</th><th>子项</th><th>在线推理</th><th>单位</th></tr>
<tr><td>OCR-Test</td><td>OCR-Test-0.9B</td><td>推理服务</td><td>输入</td><td>0.09</td><td>元/页</td></tr>
</table>
"""


BAIDU_PAGE = (
    '<div class="post__date">更新时间<!-- -->：<!-- -->2026-09-16</div>'
    '<link rel="preload" as="fetch" '
    'href="/doc/qianfan/s/page-data/wsv6ya/page-data.json"/>'
)


class BaiduAdapterTests(unittest.TestCase):
    def adapter(self, body=BAIDU_HTML, page=BAIDU_PAGE):
        payload = json.dumps({"result": {"data": {"markdownRemark": {"html": body}}}})
        return BaiduAdapter(
            MappingClient({BAIDU_PAGE_URL: page, price_data_url(BAIDU_PAGE): payload})
        )

    def record(self):
        return self.adapter().query("flash-test-0731")[0]

    def test_the_article_body_is_read_from_the_pages_own_data_file(self):
        self.assertEqual(BaiduAdapter.source_kind, "official_json")
        self.assertEqual(self.adapter().list_models(), ["flash-test-0731"])

    def test_the_page_update_date_is_kept_on_every_record(self):
        self.assertEqual(self.record()["source_updated_at"], "2026-09-16")

    def test_a_price_per_thousand_tokens_is_restated_per_million(self):
        peak = self.record()["offers"][0]
        item = price_lookup(peak, "input")
        self.assertEqual(item["amount"], "3")
        self.assertEqual(item["unit"], "CNY_per_million_tokens")
        # The figure Baidu published stays beside it, so the report can be
        # checked against the page without converting anything by hand.
        self.assertEqual(item["display"], "0.003 元/千tokens")

    def test_the_item_names_the_charge_and_the_column_names_the_channel(self):
        batch = next(
            offer for offer in self.record()["offers"] if offer["name"] == "批量推理"
        )
        self.assertEqual(price_lookup(batch, "input")["amount"], "1.5")

    def test_a_promotional_batch_column_is_not_quoted_as_the_price(self):
        batch = next(
            offer for offer in self.record()["offers"] if offer["name"] == "批量推理"
        )
        self.assertEqual([item["amount"] for item in batch["prices"]], ["1.5"])

    def test_the_two_bands_stay_apart_and_carry_the_vendors_window(self):
        record = self.record()
        self.assertEqual(
            {offer["conditions"]["time_band"] for offer in record["offers"]},
            {"高峰时段", "空闲时段"},
        )
        self.assertEqual(
            record["time_bands"]["window"],
            "高峰时段 8:00-22:00；空闲时段 22:00-次日8:00",
        )
        self.assertEqual(
            record["time_bands"]["statements"],
            ["输入（高峰时段：8:00-22:00）", "输出（空闲时段：22:00-次日8:00）"],
        )

    def test_a_price_settling_on_a_later_day_keeps_its_own_offer(self):
        deferred = [
            offer
            for offer in self.record()["offers"]
            if offer["conditions"].get("effective_from") == "9月9日起生效"
        ]
        self.assertEqual(len(deferred), 1)
        self.assertEqual(price_lookup(deferred[0], "cache_hit")["amount"], "0.1")

    def test_the_settlement_named_in_the_model_cell_stays_its_own_offer(self):
        self.assertEqual(
            {offer["conditions"]["model_note"] for offer in self.record()["offers"]},
            {"空闲时段为限时活动，8月25日0点起生效"},
        )

    def test_a_prepaid_rate_is_not_read_as_a_token_price(self):
        amounts = {
            item["amount"]
            for offer in self.record()["offers"]
            for item in offer["prices"]
        }
        self.assertNotIn("100", amounts)

    def test_a_table_billed_per_page_is_not_token_pricing(self):
        self.assertEqual(self.adapter().query("ocr-test-0.9b"), [])

    def test_a_page_without_a_data_link_is_reported_as_a_broken_source(self):
        with self.assertRaises(SourceError):
            self.adapter(page="<html><body>no link here</body></html>").list_models()

    def test_an_article_without_a_pricing_table_is_reported_as_a_broken_source(self):
        with self.assertRaises(SourceError):
            self.adapter(body="<p>本页无价格表</p>").list_models()


ZHIPU_MARKDOWN = """# API 定价

## 旗舰模型

| 模型名称 | 上下文 | 输入单价（元/百万 Tokens） | 输出单价（元/百万 Tokens） | 缓存存储（元/百万 Tokens/小时） | 缓存命中（元/百万 Tokens） | 输入模态 |
| --- | --- | --- | --- | --- | --- | --- |
| GLM-Test | 1M | 8 | 28 | 限时免费 | 2 | 文本 |
| GLM-Test-Flash | 1M | 0.8 | 2.8 | 限时免费 | 0.23 | 图片、视频、文本 |

## 模型推理

### 文本模型

| 模型名称 | 上下文 | 输入单价（元/百万 Tokens） | 输出单价（元/百万 Tokens） | 缓存存储（元/百万 Tokens/小时） | 缓存命中（元/百万 Tokens） |
| --- | --- | --- | --- | --- | --- |
| GLM-Tiered | 输入长度 \\[0, 32K) | 6 | 24 | 限时免费 | 1.3 |
| GLM-Tiered | 输入长度 ≥32K | 8 | 28 | 限时免费 | 2 |
| GLM-Free | 128K | 免费 | 免费 | 限时免费 | 不支持 |

### 多模态生成

| 模型名称 | 简介 | 规格 | 单价 | Batch API 定价 |
| --- | --- | --- | --- | --- |
| GLM-Image-Test | 图像生成 | 多分辨率 | 0.1 元/次 | 不支持 |
"""


class ZhipuAdapterTests(unittest.TestCase):
    def adapter(self, markdown=ZHIPU_MARKDOWN):
        return ZhipuAdapter(MappingClient({ZHIPU_MARKDOWN_URL: markdown}))

    def test_official_markdown_source_is_used(self):
        self.assertEqual(ZhipuAdapter.source_url, ZHIPU_URL)
        self.assertEqual(ZhipuAdapter.source_kind, "official_markdown")
        self.assertEqual(ZHIPU_MARKDOWN_URL, f"{ZHIPU_URL}.md")

    def test_only_per_token_tables_are_read(self):
        self.assertEqual(
            self.adapter().list_models(),
            ["glm-test", "glm-test-flash", "glm-tiered"],
        )
        self.assertEqual(self.adapter().query("glm-image-test"), [])

    def test_each_column_maps_onto_its_schema_type(self):
        offer = self.adapter().query("glm-test")[0]["offers"][0]
        self.assertEqual(offer["name"], "pay_as_you_go")
        self.assertEqual(price_lookup(offer, "input")["amount"], "8")
        self.assertEqual(price_lookup(offer, "output")["amount"], "28")
        self.assertEqual(price_lookup(offer, "cache_hit")["amount"], "2")
        self.assertEqual(offer["conditions"]["context_tier"], "1M")
        self.assertEqual(offer["conditions"]["输入模态"], "文本")
        self.assertEqual(offer["conditions"]["source_section"], "旗舰模型")

    def test_a_promotional_free_storage_cell_reports_no_price(self):
        offer = self.adapter().query("glm-test")[0]["offers"][0]
        self.assertIsNone(price_lookup(offer, "cache_storage"))

    def test_a_free_model_produces_no_price_rows(self):
        self.assertEqual(self.adapter().query("glm-free"), [])

    def test_context_tiers_stay_separate_offers(self):
        record = self.adapter().query("glm-tiered")[0]
        self.assertEqual(
            [offer["conditions"]["context_tier"] for offer in record["offers"]],
            ["输入长度 [0, 32K)", "输入长度 ≥32K"],
        )
        self.assertEqual(price_lookup(record["offers"][1], "input")["amount"], "8")


class TokenPriceHeaderTests(unittest.TestCase):
    def test_chinese_cache_hit_and_miss_are_distinct(self):
        self.assertEqual(token_price_kind("输入（命中缓存）"), "cache_hit")
        self.assertEqual(token_price_kind("输入（未命中缓存）"), "input")
        self.assertEqual(token_price_kind("输出"), "output")

    def test_non_token_units_are_rejected(self):
        self.assertIsNone(token_price_kind("输入音频时长"))
        self.assertIsNone(token_price_kind("价格"))
        self.assertIsNone(token_price_kind("说明"))

    def test_a_condition_column_naming_a_length_is_not_a_price(self):
        # Volcengine labels its condition column "条件 输入长度：千 token". Reading
        # it as an input price column used to drop the time band and the length
        # tier of every row in that table.
        self.assertIsNone(token_price_kind("条件<br><br>输入长度：千 token"))
        self.assertIsNone(token_price_kind("条件<br><br>Context length"))
        self.assertEqual(token_price_kind("输入(非音频)<br><br>元/百万token"), "input")

    def test_cache_storage_is_a_token_price_even_though_it_bills_an_hour(self):
        self.assertEqual(token_price_kind("缓存存储"), "cache_storage")
        self.assertEqual(
            token_price_kind("缓存存储（元/百万 Tokens/小时）"), "cache_storage"
        )
        self.assertEqual(
            token_price_kind("Cache storage, per 1M tokens / hour"), "cache_storage"
        )

    def test_english_headers_still_map(self):
        self.assertEqual(token_price_kind("Input / 1M tokens"), "input")
        self.assertEqual(token_price_kind("Cached input / 1M tokens"), "cache_hit")
        self.assertEqual(token_price_kind("Output / 1M tokens"), "output")
        self.assertIsNone(token_price_kind("Context"))


KIMI_INDEX = """# Kimi API 文档
- [对话模型价格](https://platform.kimi.com/docs/pricing/chat.md)
- [批量推理价格](https://platform.kimi.com/docs/pricing/batch.md)
- [速率限制](https://platform.kimi.com/docs/pricing/limits.md)
"""

KIMI_CHAT = """# 对话模型价格

["kimi-k3", "1M tokens", "¥2.00", "¥20.00", "¥100.00", "1,048,576 tokens"],
["kimi-k3-mini", "1M tokens", "¥0.50", "¥5.00", "¥25.00", "262,144 tokens"],
"""


class KimiAdapterTests(unittest.TestCase):
    def adapter(self):
        # Only the chat document is mapped: any other URL would raise, so this
        # also proves the sibling pricing documents are never fetched.
        return KimiAdapter(
            MappingClient(
                {
                    KIMI_INDEX_URL: KIMI_INDEX,
                    "https://platform.kimi.com/docs/pricing/chat.md": KIMI_CHAT,
                }
            )
        )

    def test_chat_document_is_found_without_a_version_suffix(self):
        self.assertEqual(self.adapter().list_models(), ["kimi-k3", "kimi-k3-mini"])

    def test_row_layout_maps_cache_hit_input_and_output(self):
        offer = self.adapter().query("kimi-k3")[0]["offers"][0]
        self.assertEqual(price_lookup(offer, "cache_hit")["amount"], "2.00")
        self.assertEqual(price_lookup(offer, "input")["amount"], "20.00")
        self.assertEqual(price_lookup(offer, "output")["amount"], "100.00")


DEEPSEEK_HTML = """
<table>
<tr><th>模型</th><th>deepseek-flash(1)</th><th>deepseek-v4-pro(2)</th></tr>
<tr><td>模型版本</td><td>DeepSeek-V4.1-Flash</td><td>DeepSeek-V4-Pro-0813</td></tr>
<tr><td>价格(3)</td><td>百万tokens输入 （缓存命中）</td><td>空闲时段</td><td>0.02元</td><td>0.15元</td></tr>
<tr><td>高峰时段</td><td>0.04元</td><td>0.30元</td></tr>
<tr><td>百万tokens输入 （缓存未命中）</td><td>空闲时段</td><td>1元</td><td>4.5元</td></tr>
<tr><td>高峰时段</td><td>2元</td><td>9.0元</td></tr>
<tr><td>百万tokens输出</td><td>空闲时段</td><td>4元</td><td>13.5元</td></tr>
<tr><td>高峰时段</td><td>8元</td><td>27.0元</td></tr>
</table>
"""


class DeepSeekAdapterTests(unittest.TestCase):
    def adapter(self):
        return DeepSeekAdapter(MappingClient({DEEPSEEK_URL: DEEPSEEK_HTML}))

    def test_footnote_markers_do_not_break_the_catalog(self):
        self.assertEqual(
            self.adapter().list_models(), ["deepseek-flash", "deepseek-v4-pro"]
        )

    def test_prices_are_read_for_each_time_band(self):
        record = self.adapter().query("deepseek-flash")[0]
        self.assertEqual(record["model_id"], "deepseek-flash")
        self.assertEqual(len(record["offers"]), 2)
        off_peak = price_lookup(record["offers"][0], "input")
        self.assertEqual(off_peak["amount"], "1")
        self.assertEqual(
            price_lookup(record["offers"][0], "cache_hit")["amount"], "0.02"
        )
        self.assertEqual(
            price_lookup(record["offers"][1], "output")["amount"], "8"
        )

    def test_retired_name_still_resolves_through_search(self):
        records = self.adapter().search("deepseek-v4-flash")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["model_id"], "deepseek-flash")

    def test_recent_news_uses_page_identity_instead_of_http_success(self):
        today = datetime(2026, 9, 19).date()
        news_day = datetime(2026, 9, 18).date()
        client = MappingClient(
            {
                deepseek_news_url(today): "<h1>Your First API Call</h1>",
                deepseek_news_url(news_day): (
                    '<html class="docs-doc-id-news/news260918">'
                    '<link rel="canonical" '
                    f'href="{deepseek_news_url(news_day)}"></html>'
                ),
            }
        )
        self.assertEqual(
            recent_news_update(client, today=today),
            "2026-09-18",
        )

    def test_prices_survive_when_no_recent_news_page_exists(self):
        record = self.adapter().query("deepseek-flash")[0]
        self.assertIsNone(record["source_updated_at"])


class ModelNameCouplingTests(unittest.TestCase):
    def test_footnote_markers_are_not_part_of_model_identity(self):
        self.assertEqual(strip_footnote_markers("deepseek-flash(1)"), "deepseek-flash")
        self.assertEqual(
            strip_footnote_markers("deepseek-v4-pro（2）"), "deepseek-v4-pro"
        )
        self.assertEqual(normalize_model("deepseek-flash(1)"), "deepseek-flash")

    def test_retired_alias_adds_matches_without_losing_family_expansion(self):
        self.assertTrue(model_matches("deepseek-v4-flash", "deepseek-flash"))
        self.assertTrue(
            model_matches("deepseek-v4-flash", "deepseek-v4-flash-0731")
        )
        self.assertFalse(model_matches("deepseek-v4-flash", "deepseek-v4-pro"))

    def test_anthropic_does_not_filter_rows_by_name_prefix(self):
        markdown = """## Model pricing
| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| --- | --- | --- | --- | --- | --- |
| Anthropic Nova 1 | $3 / MTok | $3.75 / MTok | $6 / MTok | $0.30 / MTok | $15 / MTok |
"""
        adapter = AnthropicAdapter(MappingClient({ANTHROPIC_MARKDOWN_URL: markdown}))
        self.assertEqual(adapter.list_models(), ["anthropic-nova-1"])
        record = adapter.query("anthropic-nova-1")[0]
        self.assertEqual(record["offers"][0]["prices"][0]["amount"], "3")

    def test_gemini_reads_any_google_family_without_a_name_allowlist(self):
        markdown = """## Gemma Test

*[`gemma-test`](https://example.test/gemma)*

### Standard

|   | Free Tier | Paid Tier, per 1M tokens in USD |
|---|---|---|
| Input price | Free of charge | $0.10 |
"""
        adapter = GeminiAdapter(MappingClient({GEMINI_MARKDOWN_URL: markdown}))
        self.assertEqual(adapter.list_models(), ["gemma-test"])
        record = adapter.query("gemma-test")[0]
        self.assertEqual(price_lookup(record["offers"][0], "input")["amount"], "0.10")

    def test_google_family_names_route_to_the_google_source(self):
        self.assertEqual(inferred_overseas_providers("gemma-4"), ("google",))
        self.assertEqual(inferred_overseas_providers("veo-3.1"), ("google",))

    def test_openai_accepts_any_pricing_tier_heading(self):
        markdown = """### Priority pricing data
| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-test | $2.00 | $0.20 | - | $8.00 | $4.00 | $0.40 | - | $12.00 |
"""
        adapter = OpenAIAdapter(MappingClient({OPENAI_MARKDOWN_URL: markdown}))
        record = adapter.query("gpt-test")[0]
        self.assertEqual(record["offers"][0]["name"], "priority")


class MessageRenderingTests(unittest.TestCase):
    """A comparison message is read on a phone before it is ever parsed."""

    def test_matched_model_without_parseable_prices_is_explained(self):
        payload = {
            "query": "veo-3.1",
            "retrieved_at": "2026-09-11T00:00:00+08:00",
            "results": [
                {
                    "provider": {"id": "google", "name": "Google Gemini"},
                    "model_id": "veo-3.1",
                    "display_name": "Veo 3.1",
                    "region": "全球",
                    "offers": [],
                    "source": {
                        "url": "https://example.test/pricing",
                        "kind": "official_html",
                        "retrieved_at": "2026-09-11T00:00:00+08:00",
                    },
                }
            ],
            "source_checks": [],
        }
        self.assertIn("未给出本工具可解析的价格", comparison_message(payload))

    def test_the_message_opens_with_what_it_is_and_what_it_covers(self):
        message = comparison_message(comparison_payload())
        self.assertTrue(message.startswith("模型价格对比\n"))
        self.assertIn("时间：2026-09-15 00:00（UTC+8）。", message)
        self.assertIn("主题：deepseek-flash 在各渠道的价格与服务方式。", message)

    def test_the_message_states_the_conclusion_before_the_channels(self):
        message = comparison_message(comparison_payload())
        self.assertIn("【结论】", message)
        self.assertIn("1 个渠道、1 个渠道版本、1 种计费方案", message)
        self.assertLess(message.index("【结论】"), message.index("【渠道对比】"))
        self.assertLess(message.index("【渠道对比】"), message.index("【差异总结】"))

    def test_a_comparison_that_found_nothing_says_so_once(self):
        message = comparison_message(
            {
                "query": "no-such-model",
                "retrieved_at": "2026-09-15T00:00:00+08:00",
                "results": [],
            }
        )
        self.assertIn("未发现由官方来源确认提供的匹配模型或版本。", message)
        self.assertNotIn("【渠道对比】", message)

    def test_the_message_carries_no_markup_a_parser_could_reinterpret(self):
        """DingTalk reads its own Markdown, and reads this report wrong."""
        message = comparison_message(comparison_payload())
        self.assertNotIn("**", message)
        self.assertNotIn("](", message)
        self.assertNotIn("|---", message)
        self.assertFalse(
            any(line.startswith("#") for line in message.splitlines()), message
        )

    def test_a_source_line_points_back_at_a_page_the_entry_already_showed(self):
        """The channel's entry carries this page, and one message holds so much."""
        payload = comparison_payload()
        payload["source_checks"] = [
            {
                "provider": {"id": "tencent", "name": "腾讯云 TokenHub"},
                "status": "available",
                "source": {
                    "kind": "official_markdown",
                    "url": "https://example.test/pricing",
                    "retrieved_at": "2026-09-15T00:00:00+08:00",
                },
                "cache": {"status": "hit", "fetched_at": "2026-09-15T00:00:00+08:00"},
            }
        ]
        message = comparison_message(payload)
        self.assertIn("- 腾讯云 TokenHub：已找到（来源见上）", message)
        self.assertEqual(message.count("https://example.test/pricing"), 1)
        self.assertNotIn("缓存", message.split("【来源检查】")[1])

    def test_a_source_line_keeps_the_page_of_a_channel_shown_nowhere_else(self):
        """A channel that matched nothing never got an entry to carry its page."""
        payload = comparison_payload()
        payload["source_checks"] = [
            {
                "provider": {"id": "kimi", "name": "月之暗面 Kimi"},
                "status": "not_found",
                "source": {
                    "kind": "official_markdown",
                    "url": "https://example.test/kimi",
                    "retrieved_at": "2026-09-15T00:00:00+08:00",
                },
            }
        ]
        message = comparison_message(payload)
        self.assertIn(
            "- 月之暗面 Kimi：未找到匹配模型｜https://example.test/kimi", message
        )

    def test_the_comparison_leads_with_what_the_model_is_for(self):
        """A reader who does not know what the model does cannot price it."""
        message = comparison_message(comparison_payload())
        self.assertIn("【模型介绍】", message)
        self.assertIn("用途：面向代码与智能体的高吞吐模型。", message)
        self.assertIn("主打能力：文本生成、函数调用。", message)
        self.assertNotIn("生命周期：在用", message)
        self.assertLess(message.index("【模型介绍】"), message.index("【结论】"))
        self.assertLess(message.index("【模型介绍】"), message.index("【渠道对比】"))
        # The header states what the message is; the introduction opens the blocks.
        headings = [
            line for line in message.splitlines() if line.startswith("【")
        ]
        self.assertEqual(headings[0], "【模型介绍】")

    def test_an_exceptional_lifecycle_is_still_visible(self):
        payload = comparison_payload()
        payload["model_descriptions"][0]["lifecycle"] = "preview"
        self.assertIn("生命周期：预览/实验。", comparison_message(payload))

    def test_the_introduction_names_the_page_it_came_from(self):
        """A claim about what a model does is worth the page it was read from."""
        message = comparison_message(comparison_payload())
        introduction = message.split("【结论】")[0]
        self.assertIn(
            "来源：腾讯云模型广场："
            "https://example.test/models/deepseek/deepseek-flash",
            introduction,
        )
        self.assertNotIn("https://example.test/pricing", introduction)

    def test_an_over_long_summary_is_kept_whole_and_labelled(self):
        """The tool has no model to ask, so it states the overrun rather than cutting.

        Cutting would read as the vendor's own wording: a sentence that stops
        mid-clause carries no sign that it was the first part of one.
        """
        payload = comparison_payload()
        payload["model_descriptions"] = [
            description_of(
                "deepseek/deepseek-flash", "DeepSeek-V4.1-Flash", LONG_SUMMARY
            )
        ]
        message = comparison_message(payload)
        self.assertIn(f"用途（原文 {len(LONG_SUMMARY)} 字", message)
        self.assertIn(f"超过 {SUMMARY_MAX_CHARS} 字上限", message)
        self.assertIn("需先总结再发送", message)
        self.assertIn(LONG_SUMMARY, message)


class MessageBudgetTests(unittest.TestCase):
    """The message has to fit the channel it is sent through before it is sent."""

    def test_a_report_that_fits_is_sent_as_it_stands(self):
        message = comparison_message(comparison_payload())
        self.assertLessEqual(len(message), DEFAULT_MAX_CHARS)
        self.assertIn("【渠道对比】", message)
        self.assertNotIn("需总结压缩", message)

    def test_the_default_budget_stays_inside_the_channel_it_writes_for(self):
        """DingTalk carries 5120 characters, and a report is kept well inside it."""
        self.assertLess(DEFAULT_MAX_CHARS, 5120)

    def test_an_over_long_report_loses_nothing_and_says_it_must_be_summarized(self):
        """Nothing is dropped to fit, and that is the point.

        A report that comes in under the limit because a block was removed reports
        on less than the scan covered, and the reader cannot tell an omitted block
        from one that was never there.
        """
        message = comparison_message(crowded_comparison_payload())
        self.assertGreater(len(message), DEFAULT_MAX_CHARS)
        for position in range(9):
            self.assertIn(f"渠道{position + 1}", message)
        for amount in CROWDED_AMOUNTS:
            self.assertIn(amount, message)
        for heading in (
            "【模型介绍】",
            "【结论】",
            "【渠道对比】",
            "【差异总结】",
            "【峰谷时段】",
            "【来源检查】",
        ):
            self.assertIn(heading, message)
        self.assertIn(f"需总结压缩到 {DEFAULT_MAX_CHARS} 字内", message)

    def test_the_note_says_exactly_how_much_has_to_be_summarized_away(self):
        payload = crowded_comparison_payload()
        body = comparison_message(payload, max_chars=10_000)
        message = comparison_message(payload)
        reported = re.search(
            rf"本消息 (\d+) 字，超过 {DEFAULT_MAX_CHARS} 字上限 (\d+) 字", message
        )
        self.assertIsNotNone(reported, message.splitlines()[-1])
        chars, over = (int(value) for value in reported.groups())
        # The count is the report itself, and the overrun is what it has to shed:
        # the sender has to take away exactly that much and no more.
        self.assertEqual(chars, len(body))
        self.assertEqual(over, len(body) - DEFAULT_MAX_CHARS)

    def test_an_over_long_report_is_the_same_report_with_a_note_appended(self):
        """The note is the only difference; no line of the report changes."""
        payload = crowded_comparison_payload()
        at_default = comparison_message(payload)
        wider = comparison_message(payload, max_chars=10_000)
        self.assertTrue(at_default.startswith(wider), at_default[:200])
        self.assertEqual(
            at_default[len(wider) :].strip().splitlines()[0],
            next(
                line
                for line in at_default.splitlines()
                if line.startswith("本消息 ")
            ),
        )

    def test_the_budget_is_asked_for_per_invocation_not_built_in(self):
        payload = crowded_comparison_payload()
        self.assertEqual(
            comparison_message(payload, max_chars=10_000).count("本消息 "), 0
        )
        self.assertGreaterEqual(
            comparison_message(payload, max_chars=1).count("本消息 "), 1
        )

    def test_overage_is_what_a_message_has_to_shed(self):
        self.assertEqual(overage("abcd", 4), 0)
        self.assertEqual(overage("abcde", 4), 1)
        self.assertEqual(overage("", 4), 0)

    def test_the_budget_switch_reaches_every_subcommand_that_renders_a_message(self):
        parser = query_model_prices.build_parser()
        for argv in (
            ["compare", "deepseek-flash"],
            ["provider", "tencent", "deepseek-flash"],
            ["delta"],
        ):
            with self.subTest(argv=argv):
                args = parser.parse_args(argv)
                self.assertEqual(args.max_chars, DEFAULT_MAX_CHARS)
        self.assertEqual(
            parser.parse_args(
                ["compare", "deepseek-flash", "--max-chars", "1200"]
            ).max_chars,
            1200,
        )


def comparison_payload():
    """One priced channel, as the comparison renderer receives it."""
    return {
        "query": "deepseek-flash",
        "retrieved_at": "2026-09-15T00:00:00+08:00",
        "results": [
            {
                "provider": {"id": "tencent", "name": "腾讯云 TokenHub"},
                "model_id": "deepseek/deepseek-flash",
                "display_name": "DeepSeek-V4.1-Flash 原厂直供",
                "region": "中国区（广州）",
                "delivery_mode": "upstream_direct",
                "offers": [
                    {
                        "name": "online_standard",
                        "conditions": {},
                        "prices": [
                            price_item("input", "输入", "2", "CNY_per_million_tokens"),
                            price_item("output", "输出", "8", "CNY_per_million_tokens"),
                        ],
                    }
                ],
                "source": {
                    "url": "https://example.test/pricing",
                    "kind": "official_markdown",
                    "retrieved_at": "2026-09-15T00:00:00+08:00",
                },
            }
        ],
        "source_checks": [],
        "model_descriptions": [
            description_of(
                "deepseek/deepseek-flash",
                "DeepSeek-V4.1-Flash",
                "面向代码与智能体的高吞吐模型。",
            )
        ],
    }


def description_of(model_id, display_name, summary, **overrides):
    """One vendor introduction, as the description resolver returns it.

    Built through the record every source funnels through, so a fixture cannot
    drift from the shape — including whether the summary is over its limit.
    """
    description = description_record(
        model_id,
        display_name,
        summary,
        f"https://example.test/models/{model_id}",
        "tencent_mirror",
        source_name="腾讯云模型广场",
        capabilities=("文本生成", "函数调用"),
        lifecycle="active",
        specifications={"context_window": "128,000"},
    )
    description.update(overrides)
    return description


# A vendor's announcement, which runs past what one message may carry: the page
# DeepSeek publishes for a model is a press release rather than a summary.
LONG_SUMMARY = (
    "今天，我们正式发布新一代模型。这个版本在推理速度、吞吐与多模态理解上均有提升，"
    "并针对代码与智能体任务做了专门优化。"
) * 6


def crowded_comparison_payload(channels=9):
    """The same model priced by many channels, as a wide comparison arrives.

    Each channel publishes what a real catalogue does — two time bands, an input
    and an output rate, a cache-hit rate and a cache-storage rate — because it is
    how much each channel prices rather than how many channels there are that
    makes the first rendering too long for one message.

    Every channel is also checked, so the report carries the source blocks a real
    one does. The pages are distinct per channel because a shared one would be
    printed once and the report would come in shorter than a real one.
    """
    results = []
    checks = []
    for position in range(channels):
        price_pages = {
            "kind": "official_markdown",
            "url": f"https://example.test/p{position}/pricing",
            "retrieved_at": "2026-09-15T00:00:00+08:00",
        }
        results.append(
            {
                "provider": {"id": f"p{position}", "name": f"渠道{position + 1}"},
                "model_id": "deepseek/deepseek-flash",
                "display_name": "DeepSeek-V4.1-Flash",
                "region": "中国区（广州）",
                "delivery_mode": "upstream_direct",
                "offers": [
                    {
                        "name": "闲时",
                        "conditions": {"time_band": "闲时"},
                        "prices": [
                            price_item("input", "输入（未命中缓存）", "1", "CNY_per_million_tokens"),
                            price_item("cache_hit", "输入（命中缓存）", "0.2", "CNY_per_million_tokens"),
                            price_item("cache_storage", "缓存存储", "0.017", "CNY_per_million_tokens_per_hour"),
                            price_item("output", "输出", "4", "CNY_per_million_tokens"),
                        ],
                    },
                    {
                        "name": "忙时",
                        "conditions": {"time_band": "忙时"},
                        "prices": [
                            price_item("input", "输入（未命中缓存）", "2", "CNY_per_million_tokens"),
                            price_item("cache_hit", "输入（命中缓存）", "0.4", "CNY_per_million_tokens"),
                            price_item("cache_storage", "缓存存储", "0.017", "CNY_per_million_tokens_per_hour"),
                            price_item("output", "输出", "8", "CNY_per_million_tokens"),
                        ],
                    },
                ],
                "time_bands": {
                    "window": "00:30-08:30",
                    "statements": ["每日 00:30 至 08:30 为闲时时段"],
                    "source_url": f"https://example.test/p{position}/bands",
                },
                "source": price_pages,
            }
        )
        checks.append(
            {
                "provider": {"id": f"p{position}", "name": f"渠道{position + 1}"},
                "status": "available",
                "source": dict(price_pages),
            }
        )
    payload = comparison_payload()
    payload["results"] = results
    payload["source_checks"] = checks
    return payload


# One amount each density has to keep saying, and the terms it is billed under.
CROWDED_AMOUNTS = ("输入（未命中缓存）：1 元/百万 tokens", "输出：4 元/百万 tokens")


class AliyunAdapterTests(unittest.TestCase):
    """Bailian keys its bands in English but publishes the window only in prose."""

    def test_the_api_key_is_reported_in_the_pages_own_words(self):
        self.assertEqual(time_band_label("offpeak"), "闲时")
        self.assertEqual(time_band_label("peak"), "忙时")
        self.assertEqual(time_band_label("standard"), "standard")

    def test_the_window_comes_from_the_pricing_page(self):
        page = "错峰时段为东八区 22:00 至次日 8:00，其余时段为忙时，以账单时间为准。"
        self.assertEqual(
            time_bands_for(
                page, model_id="deepseek-v4.1-flash", source_url=ALIYUN_BAND_DOC_URL
            )["window"],
            "22:00至次日8:00、其余时段为忙时",
        )

    def test_an_unreachable_page_leaves_the_window_empty(self):
        class UnreachableClient:
            def get_text(self, url):
                raise SourceError("request failed")

        self.assertEqual(AliyunAdapter(UnreachableClient())._band_text(), "")


class BailianPagingClient:
    """A catalogue API that answers one page per request, recording each ask."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.requests = []

    def get_text(self, url):
        return ""

    def post_form(self, url, fields):
        self.requests.append(json.loads(fields["params"])["Data"]["input"])
        page = self.pages[min(len(self.requests) - 1, len(self.pages) - 1)]
        return {"data": {"DataV2": {"data": {"code": "200", "data": page}}}}


def bailian_page(models, total):
    return {
        "list": [{"items": [{"model": model}]} for model in models],
        "total": total,
    }


class AliyunPagingPauseTests(unittest.TestCase):
    """Bailian throttles a burst of paged requests, so a scan is spaced out."""

    def test_a_pause_is_a_random_wait_between_one_and_three_seconds(self):
        with mock.patch("model_price.providers.aliyun.time.sleep") as sleep:
            waited = pause_before_next_page()
        self.assertGreaterEqual(waited, 1.0)
        self.assertLessEqual(waited, 3.0)
        sleep.assert_called_once_with(waited)

    def test_the_first_request_is_not_delayed_and_the_rest_are(self):
        client = BailianPagingClient(
            [
                bailian_page(["m1"], 150),
                bailian_page(["m2"], 150),
                bailian_page(["m3"], 150),
            ]
        )
        with mock.patch("model_price.providers.aliyun.time.sleep") as sleep, mock.patch(
            "model_price.providers.aliyun.random.uniform", return_value=2.0
        ) as uniform:
            models = AliyunAdapter(client).list_models()
        self.assertEqual(models, ["m1", "m2", "m3"])
        self.assertEqual([ask["pageNo"] for ask in client.requests], [1, 2, 3])
        self.assertEqual(sleep.call_count, 2)
        sleep.assert_called_with(2.0)
        uniform.assert_called_with(1.0, 3.0)

    def test_a_catalogue_that_fits_in_one_page_never_waits(self):
        client = BailianPagingClient([bailian_page(["m1"], 1)])
        with mock.patch("model_price.providers.aliyun.time.sleep") as sleep:
            models = AliyunAdapter(client).list_models()
        self.assertEqual(models, ["m1"])
        sleep.assert_not_called()


class TimeBandWindowTests(unittest.TestCase):
    """Every platform draws the peak window differently, and each is quoted as written."""

    VOLCENGINE_NOTE = """* deepseek\\-v4.1\\-flash 模型高峰、空闲时段如下：

   * 高峰时段：**北京时间周一至周五 09:00–12:00、14:00–18:00**。
   * 空闲时段：除上述高峰时段外，其余均为空闲时段。
"""

    DEEPSEEK_NOTE = (
        "(3) 空闲时段价格为高峰时段价格的一半。高峰时段为北京时间"
        "周一至周五 9:00 - 12:00、14:00 - 18:00（其余为空闲时段）。"
    )

    TENCENT_NOTE = (
        "注意：DeepSeek V4【原厂直供】所有模型将跟随原厂调整峰谷计费规则："
        "工作日（周一至周五）继续执行原有峰谷计费（高峰时段为北京时间 "
        "9:00–12:00、14:00–18:00，其余为空闲时段）；"
        "周末（周六、周日）全天不再区分峰谷时段，统一按空闲时段价格计费。"
        "DeepSeek-V4-Flash 0731 正式版模型峰谷计费规则维持不变："
        "高峰时段仍为北京时间周一至周日 9:00–12:00、14:00–18:00（其余为空闲时段）。"
    )

    ALIYUN_NOTE = (
        "部分模型享有限时分时折扣（详见价格旁标签），错峰时段为东八区 "
        "22:00 至次日 8:00，其余时段为忙时，以账单时间为准。"
    )

    def window(self, document, **kwargs):
        return time_bands_for(document, **kwargs).get("window", "")

    def test_the_volcengine_window_is_read_and_its_escaped_name_unescaped(self):
        self.assertEqual(
            self.window(self.VOLCENGINE_NOTE, display_name="deepseek-v4.1-flash"),
            "周一至周五 09:00–12:00、14:00–18:00；其余均为空闲时段",
        )

    def test_a_footnote_window_without_a_model_name_still_applies(self):
        """DeepSeek states one window in a footnote, for every model it lists."""
        self.assertEqual(
            self.window(self.DEEPSEEK_NOTE, model_id="deepseek-flash"),
            "周一至周五 9:00-12:00、14:00-18:00、其余为空闲时段",
        )

    def test_a_platform_can_bill_two_generations_on_different_calendars(self):
        direct = self.window(
            self.TENCENT_NOTE,
            model_id="deepseek/deepseek-flash",
            display_name="DeepSeek-V4.1-Flash 原厂直供",
            labels=("原厂直供",),
        )
        self.assertIn("周一至周五 9:00–12:00、14:00–18:00", direct)
        self.assertIn("周末", direct)
        kept = self.window(
            self.TENCENT_NOTE,
            model_id="deepseek-v4-flash-0731",
            display_name="DeepSeek-V4-Flash 0731 正式版",
        )
        self.assertIn("周一至周日", kept)
        self.assertNotIn("周末", kept)

    def test_an_overnight_off_peak_window_is_read(self):
        self.assertEqual(
            self.window(self.ALIYUN_NOTE, model_id="deepseek-v4.1-flash"),
            "22:00至次日8:00、其余时段为忙时",
        )

    def test_a_platform_publishing_no_window_gets_none_invented(self):
        self.assertEqual(time_bands_for("本页只列出每百万 tokens 单价。"), {})
        self.assertEqual(time_bands_for("|模型|输入|输出|", model_id="anything"), {})

    def test_the_vendor_sentence_is_kept_verbatim(self):
        statements = time_bands_for(self.DEEPSEEK_NOTE, model_id="deepseek-flash")[
            "statements"
        ]
        self.assertTrue(
            any("空闲时段价格为高峰时段价格的一半" in text for text in statements)
        )

    def test_a_price_row_glued_to_the_rule_is_not_a_rule(self):
        """A rendered page can merge a price table into the note that follows it."""
        page = (
            "原价$0.276 （限时错峰4折）忙时8折 错峰时段为东八区"
            " 22:00 至次日 8:00，其余时段为忙时。\n"
            "错峰时段为东八区 22:00 至次日 8:00，其余时段为忙时。"
        )
        self.assertEqual(
            time_bands_for(page, model_id="deepseek-v4.1-flash")["statements"],
            ["错峰时段为东八区 22:00 至次日 8:00，其余时段为忙时。"],
        )


class TimeBandRenderingTests(unittest.TestCase):
    def record(self, window=""):
        return {
            "provider": {"id": "tencent", "name": "腾讯云 TokenHub"},
            "model_id": "deepseek/deepseek-flash",
            "display_name": "DeepSeek-V4.1-Flash 原厂直供",
            "region": "中国区（广州）",
            "time_bands": {
                "window": window,
                "statements": ["工作日（周一至周五）……其余为空闲时段。"],
                "source_url": "https://example.test/pricing",
            },
        }

    def test_a_banded_row_names_its_band_without_repeating_the_window(self):
        """Every offer of a model publishes the same hours, and 峰谷时段 prints
        them once, so a row that carried them again said the same thing twice."""
        offer = {
            "name": "online_conditional",
            "conditions": {"time_band": "空闲时段"},
            "prices": [],
        }
        text = offer_condition_text(offer, {})
        self.assertEqual(text, "空闲时段")

    def test_a_row_without_a_time_band_names_its_offer(self):
        """A model billed the same all day keeps whatever names its offer."""
        offer = {"name": "online_standard", "conditions": {}, "prices": []}
        text = offer_condition_text(offer, {})
        self.assertEqual(text, "online_standard")

    def test_a_band_offer_named_like_its_band_is_not_a_stutter(self):
        """Aliyun files 闲时 as both the offer's name and its ``time_band``."""
        offer = {"name": "闲时", "conditions": {"time_band": "闲时"}, "prices": []}
        self.assertEqual(offer_condition_text(offer, {}), "闲时")

    def test_terms_every_offer_shares_are_read_as_the_models_own(self):
        """Ark bills both bands the same way from the same section."""
        record = {
            "offers": [
                {
                    "name": "online_standard",
                    "conditions": {
                        "time_band": "空闲时段",
                        "billing_mode": "pay_as_you_go",
                        "release_stage": "stable",
                    },
                },
                {
                    "name": "online_standard",
                    "conditions": {
                        "time_band": "高峰时段",
                        "billing_mode": "pay_as_you_go",
                        "release_stage": "stable",
                    },
                },
            ]
        }
        shared = shared_conditions(record)
        self.assertEqual(
            shared, {"billing_mode": "pay_as_you_go", "release_stage": "stable"}
        )
        self.assertEqual(offer_condition_text(record["offers"][0], shared), "空闲时段")

    def test_a_term_only_one_offer_carries_stays_on_that_row(self):
        """A term that tells two offers apart is the one a row has to keep."""
        offer = {
            "name": "按量后付费",
            "conditions": {"time_band": "高峰时段", "release_stage": "retiring"},
        }
        self.assertEqual(
            offer_condition_text(offer, {"billing_mode": "pay_as_you_go"}),
            "高峰时段；release_stage=retiring",
        )

    def test_a_model_with_one_offer_shares_no_terms(self):
        """Nothing is common to a set of one, and nothing is hoisted for it."""
        record = {
            "offers": [
                {"name": "标准", "conditions": {"billing_mode": "pay_as_you_go"}}
            ]
        }
        self.assertEqual(shared_conditions(record), {})

    def test_the_message_quotes_each_platforms_own_words(self):
        payload = {
            "query": "deepseek-flash",
            "retrieved_at": "2026-09-15T00:00:00+08:00",
            "results": [
                {
                    **self.record("周一至周五 9:00–12:00"),
                    "offers": [
                        {
                            "name": "online_conditional",
                            "conditions": {"time_band": "空闲时段"},
                            "prices": [],
                        }
                    ],
                    "source": {
                        "url": "https://example.test/pricing",
                        "kind": "official_document",
                        "retrieved_at": "2026-09-15T00:00:00+08:00",
                    },
                }
            ],
            "source_checks": [],
        }
        message = comparison_message(payload)
        self.assertIn("【峰谷时段】", message)
        self.assertIn("腾讯云 TokenHub", message)
        self.assertIn("工作日（周一至周五）……其余为空闲时段。", message)

    def test_a_comparison_without_time_bands_has_no_window_section(self):
        self.assertEqual(band_lines([{"offers": [{"conditions": {}}]}]), [])


def scanned_record(
    model_id,
    display_name,
    input_amount,
    output_amount,
    *,
    offer="online_standard",
    band=None,
    updated_at=None,
):
    """A record as an adapter would return it, for catalogue-scan tests."""
    extra = {"source_updated_at": updated_at} if updated_at else {}
    return make_record(
        "fake",
        "假渠道",
        model_id,
        display_name,
        "中国区",
        [
            {
                "name": offer,
                "conditions": {"time_band": band} if band else {},
                "prices": [
                    price_item("input", "输入", input_amount, "CNY_per_million_tokens"),
                    price_item("output", "输出", output_amount, "CNY_per_million_tokens"),
                ],
            }
        ],
        "https://example.test/fake",
        "test",
        "2026-09-16T00:00:00+08:00",
        delivery_mode="platform_hosted",
        **extra,
    )


def scanned_offers(model_id, display_name, *offers):
    """A record carrying several offers at once, as one adapter returns them.

    Each offer is ``(name, band, input_amount, output_amount)``.
    """
    return make_record(
        "fake",
        "假渠道",
        model_id,
        display_name,
        "中国区",
        [
            {
                "name": name,
                "conditions": {"time_band": band} if band else {},
                "prices": [
                    price_item("input", "输入", input_amount, "CNY_per_million_tokens"),
                    price_item("output", "输出", output_amount, "CNY_per_million_tokens"),
                ],
            }
            for name, band, input_amount, output_amount in offers
        ],
        "https://example.test/fake",
        "test",
        "2026-09-16T00:00:00+08:00",
        delivery_mode="platform_hosted",
    )


class ScannedProvider:
    """A provider that serves a fixed catalogue, or fails on demand."""

    source_kind = "test"

    def __init__(
        self, records=(), error=None, provider_id="fake", provider_name="假渠道"
    ):
        self.records = list(records)
        self.error = error
        self.provider_id = provider_id
        self.provider_name = provider_name
        self.source_url = f"https://example.test/{provider_id}"

    def catalog_records(self):
        if self.error:
            raise self.error
        return list(self.records)


def snapshot_of(records, captured_at="2026-09-16T10:00:00+08:00"):
    return build_snapshot(ScannedProvider(records), records, captured_at)


def render_scan(store, providers, captured_at):
    """Scan these providers against the store, then render the message they make."""
    return scan_message(
        scan_providers(list(providers), store, captured_at=captured_at)
    )


class SnapshotTests(unittest.TestCase):
    def test_a_model_without_a_price_is_left_out_of_the_catalogue(self):
        unpriced = make_record(
            "fake",
            "假渠道",
            "m2",
            "M2",
            "中国区",
            [],
            "https://example.test/fake",
            "test",
            "2026-09-16T00:00:00+08:00",
        )
        snapshot = snapshot_of([scanned_record("m1", "M1", "2", "8"), unpriced])
        self.assertEqual(list(snapshot["models"]), ["m1"])

    def test_the_same_catalogue_builds_the_same_snapshot(self):
        first = snapshot_of([scanned_record("m1", "M1", "2", "8")], "2026-09-16T10:00:00+08:00")
        second = snapshot_of([scanned_record("m1", "M1", "2", "8")], "2026-09-17T10:00:00+08:00")
        first.pop("captured_at")
        second.pop("captured_at")
        self.assertEqual(first, second)

    def test_the_documents_own_section_is_not_part_of_an_offers_identity(self):
        one = {"name": "标准", "conditions": {"source_section": "在线推理"}}
        other = {"name": "标准", "conditions": {"source_section": "批量推理"}}
        self.assertEqual(offer_identity(one), offer_identity(other))

    def test_a_billed_condition_stays_part_of_an_offers_identity(self):
        one = {"name": "闲时", "conditions": {"time_band": "闲时"}}
        other = {"name": "忙时", "conditions": {"time_band": "忙时"}}
        self.assertNotEqual(offer_identity(one), offer_identity(other))

    def test_the_newest_update_stamp_the_vendor_published_is_kept(self):
        snapshot = snapshot_of(
            [
                scanned_record("m1", "M1", "2", "8", updated_at="2026-09-10T00:00:00Z"),
                scanned_record("m2", "M2", "2", "8", updated_at="2026-09-14T03:03:07Z"),
            ]
        )
        self.assertEqual(snapshot["source"]["updated_at"], "2026-09-14T03:03:07Z")

    def test_a_vendor_publishing_no_update_stamp_reports_none(self):
        snapshot = snapshot_of([scanned_record("m1", "M1", "2", "8")])
        self.assertIsNone(snapshot["source"]["updated_at"])

    def test_two_records_for_one_model_contribute_both_sets_of_offers(self):
        snapshot = snapshot_of(
            [
                scanned_record("m1", "M1", "1", "4", offer="闲时", band="闲时"),
                scanned_record("m1", "M1", "2", "8", offer="忙时", band="忙时"),
            ]
        )
        self.assertEqual(len(snapshot["models"]["m1"]["offers"]), 2)


class SnapshotStoreTests(unittest.TestCase):
    def test_a_baseline_is_written_and_read_back(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            snapshot = snapshot_of([scanned_record("m1", "M1", "2", "8")])
            self.assertIsNone(store.read("fake"))
            store.write("fake", snapshot)
            self.assertEqual(store.read("fake"), snapshot)

    def test_a_baseline_written_by_an_older_shape_is_treated_as_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            store.write("fake", {"schema_version": 0, "models": {}})
            self.assertIsNone(store.read("fake"))

    def test_an_unreadable_baseline_reads_as_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            store.path("fake").write_text("{not json", encoding="utf-8")
            self.assertIsNone(store.read("fake"))


class DiffingTests(unittest.TestCase):
    def report(self, before, after):
        return compare_snapshots(
            snapshot_of(before, "2026-09-15T10:00:00+08:00"),
            snapshot_of(after, "2026-09-16T10:00:00+08:00"),
        )

    def test_a_new_model_carries_what_it_costs(self):
        report = self.report(
            [scanned_record("m1", "M1", "2", "8")],
            [scanned_record("m1", "M1", "2", "8"), scanned_record("m2", "M2", "3", "12")],
        )
        self.assertEqual(report["status"], CHANGED)
        added = report["changes"]["models_added"]
        self.assertEqual([model["model_id"] for model in added], ["m2"])
        self.assertEqual(price_lookup(added[0]["offers"][0], "input")["amount"], "3")

    def test_a_withdrawn_model_keeps_the_price_it_had(self):
        report = self.report(
            [scanned_record("m1", "M1", "2", "8"), scanned_record("m2", "M2", "3", "12")],
            [scanned_record("m1", "M1", "2", "8")],
        )
        removed = report["changes"]["models_removed"]
        self.assertEqual([model["model_id"] for model in removed], ["m2"])
        self.assertEqual(price_lookup(removed[0]["offers"][0], "input")["amount"], "3")

    def test_a_moved_amount_is_reported_with_both_ends(self):
        report = self.report(
            [scanned_record("m1", "M1", "2", "8")],
            [scanned_record("m1", "M1", "1.5", "8")],
        )
        change = report["changes"]["price_changes"][0]
        self.assertEqual((change["type"], change["label"]), ("input", "输入"))
        self.assertEqual(
            change["from"], {"amount": "2", "unit": "CNY_per_million_tokens"}
        )
        self.assertEqual(
            change["to"], {"amount": "1.5", "unit": "CNY_per_million_tokens"}
        )

    def test_a_price_that_did_not_move_is_not_a_change(self):
        report = self.report(
            [scanned_record("m1", "M1", "2", "8")],
            [scanned_record("m1", "M1", "2", "8")],
        )
        self.assertEqual(report["status"], UNCHANGED)
        self.assertEqual(report["changes"]["total"], 0)

    def test_a_new_time_band_is_an_offer_change_not_a_moved_price(self):
        report = self.report(
            [scanned_record("m1", "M1", "2", "8")],
            [
                scanned_offers(
                    "m1",
                    "M1",
                    ("闲时", "闲时", "1", "4"),
                    ("忙时", "忙时", "2", "8"),
                )
            ],
        )
        self.assertEqual(report["changes"]["price_changes"], [])
        self.assertEqual(len(report["changes"]["offers_added"]), 2)
        self.assertEqual(len(report["changes"]["offers_removed"]), 1)

    def test_a_provider_without_a_baseline_is_not_reported_as_unchanged(self):
        report = compare_snapshots(
            None, snapshot_of([scanned_record("m1", "M1", "2", "8")])
        )
        self.assertEqual(report["status"], BASELINE_CREATED)
        self.assertIsNone(report["baseline_at"])
        self.assertEqual(report["changes"]["total"], 0)


class DeltaScanTests(unittest.TestCase):
    def test_the_first_scan_records_a_baseline_rather_than_no_change(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            payload = scan_providers(
                [ScannedProvider([scanned_record("m1", "M1", "2", "8")])],
                store,
                captured_at="2026-09-16T10:00:00+08:00",
            )
            report = payload["providers"][0]
            self.assertEqual(report["status"], BASELINE_CREATED)
            self.assertEqual(report["model_count"], 1)
            self.assertIsNotNone(store.read("fake"))

    def test_a_second_identical_scan_reports_no_change(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            records = [scanned_record("m1", "M1", "2", "8")]
            scan_providers([ScannedProvider(records)], store, captured_at="2026-09-16T10:00:00+08:00")
            payload = scan_providers(
                [ScannedProvider(records)], store, captured_at="2026-09-17T10:00:00+08:00"
            )
            report = payload["providers"][0]
            self.assertEqual(report["status"], UNCHANGED)
            self.assertEqual(report["baseline_at"], "2026-09-16T10:00:00+08:00")

    def test_a_failed_scan_keeps_the_baseline_the_next_scan_compares_against(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            scan_providers(
                [ScannedProvider([scanned_record("m1", "M1", "2", "8")])],
                store,
                captured_at="2026-09-16T10:00:00+08:00",
            )
            failed = scan_providers(
                [ScannedProvider(error=SourceError("offline"))],
                store,
                captured_at="2026-09-17T10:00:00+08:00",
            )
            self.assertEqual(failed["providers"][0]["status"], "source_error")
            self.assertEqual(
                failed["providers"][0]["baseline_at"], "2026-09-16T10:00:00+08:00"
            )
            recovered = scan_providers(
                [ScannedProvider([scanned_record("m1", "M1", "1.5", "8")])],
                store,
                captured_at="2026-09-18T10:00:00+08:00",
            )
            self.assertEqual(recovered["providers"][0]["status"], CHANGED)

    def test_a_scan_that_prices_nothing_keeps_the_baseline_too(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            scan_providers(
                [ScannedProvider([scanned_record("m1", "M1", "2", "8")])],
                store,
                captured_at="2026-09-16T10:00:00+08:00",
            )
            empty = scan_providers(
                [ScannedProvider([])], store, captured_at="2026-09-17T10:00:00+08:00"
            )
            self.assertEqual(empty["providers"][0]["status"], "empty_scan")
            self.assertEqual(store.read("fake")["captured_at"], "2026-09-16T10:00:00+08:00")

    def test_the_summary_counts_every_provider_and_every_change(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            scan_providers(
                [ScannedProvider([scanned_record("m1", "M1", "2", "8")])],
                store,
                captured_at="2026-09-16T10:00:00+08:00",
            )
            payload = scan_providers(
                [
                    ScannedProvider([scanned_record("m1", "M1", "1", "8")]),
                    ScannedProvider(
                        error=SourceError("offline"),
                        provider_id="broken",
                        provider_name="坏渠道",
                    ),
                ],
                store,
                captured_at="2026-09-17T10:00:00+08:00",
            )
            self.assertEqual(
                payload["summary"],
                {
                    "providers": 2,
                    "changed": 1,
                    "unchanged": 0,
                    "baseline_created": 0,
                    "empty_scan": 0,
                    "source_error": 1,
                    "models_added": 0,
                    "models_removed": 0,
                    "offers_added": 0,
                    "offers_removed": 0,
                    "price_changes": 1,
                },
            )


class CatalogScanTests(unittest.TestCase):
    def test_the_default_scan_walks_a_catalogue_it_cannot_read_at_once(self):
        records = CountingSource().catalog_records()
        self.assertEqual([record["model_id"] for record in records], ["model-a"])

    def test_a_tabular_adapter_builds_every_model_from_one_document_pass(self):
        adapter = VolcengineAdapter(
            MappingClient({VOLCENGINE_DOC_API: json.dumps(volc_payload())})
        )
        self.assertEqual(
            {record["model_id"] for record in adapter.catalog_records()},
            {
                "doubao-seed-2.0-pro",
                "deepseek-v4-1-flash",
                "deepseek-v4-flash正式版",
                "deepseek-v4-pro预览版",
            },
        )

    def test_a_whole_catalogue_is_cached_as_a_single_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheStore(Path(directory))
            source = CountingSource()
            adapter = CachedPriceSource(source, cache)
            self.assertEqual(adapter.catalog_records(), [{"model_id": "model-a"}])
            self.assertEqual(source.calls, 2)
            adapter.catalog_records()
            self.assertEqual(source.calls, 2)
            self.assertEqual(len(list(Path(directory).rglob("*.json"))), 1)


class DeltaRenderingTests(unittest.TestCase):
    def test_an_internal_band_key_is_shown_in_the_vendors_own_words(self):
        self.assertEqual(offering_text("off_peak", {"time_band": "空闲时段"}), "空闲时段")

    def test_a_band_offer_is_not_named_twice(self):
        self.assertEqual(offering_text("闲时", {"time_band": "闲时"}), "闲时")

    def test_an_offer_without_a_band_keeps_its_name_and_conditions(self):
        self.assertEqual(
            offering_text("online_standard", {"context_tier": "输入长度 [0, 32]"}),
            "online_standard；context_tier=输入长度 [0, 32]",
        )

    def test_a_scan_that_moved_nothing_says_only_that_it_ran(self):
        """A scheduled run still reports itself; the catalogue stays out of it."""
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            provider = ScannedProvider(
                [scanned_record("m1", "M1", "2", "8", updated_at="2026-09-14T03:03:07Z")]
            )
            render_scan(store, [provider], "2026-09-15T10:00:00+08:00")
            message = render_scan(store, [provider], "2026-09-16T10:00:00+08:00")
            self.assertIn("1 个渠道共 1 个模型，全部无变化。", message)
            self.assertIn("时间：2026-09-16 10:00（UTC+8）", message)
            self.assertNotIn("【渠道概览】", message)
            self.assertNotIn("【变化详情】", message)

    def test_a_channel_that_moved_lists_what_moved(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            render_scan(
                store,
                [ScannedProvider([scanned_record("m1", "M1", "2", "8")])],
                "2026-09-15T10:00:00+08:00",
            )
            report = render_scan(
                store,
                [
                    ScannedProvider(
                        [
                            scanned_record("m1", "M1", "1", "8"),
                            scanned_record("m2", "M2", "3", "12"),
                        ]
                    )
                ],
                "2026-09-16T10:00:00+08:00",
            )
            self.assertIn("新增模型（1）", report)
            self.assertIn("输入 3 元/百万 tokens", report)
            self.assertIn("2 元/百万 tokens → 1 元/百万 tokens", report)

    def test_a_failed_channel_is_named_with_its_reason_and_kept_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            store.write(
                "broken",
                snapshot_of([scanned_record("m1", "M1", "2", "8")], "2026-09-15T10:00:00+08:00"),
            )
            report = render_scan(
                store,
                [
                    ScannedProvider(
                        error=SourceError("official document changed shape"),
                        provider_id="broken",
                        provider_name="坏渠道",
                    )
                ],
                "2026-09-16T10:00:00+08:00",
            )
            self.assertIn("坏渠道", report)
            self.assertIn("来源解析失败", report)
            self.assertIn("official document changed shape", report)
            self.assertIn("上次基线 2026-09-15 10:00（UTC+8） 保留", report)

    def test_a_baseline_run_says_how_many_models_it_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            report = render_scan(
                store,
                [ScannedProvider([scanned_record("m1", "M1", "2", "8")])],
                "2026-09-16T10:00:00+08:00",
            )
            self.assertIn("记录 1 个模型，下次扫描起参与对比", report)


class MomentFormattingTests(unittest.TestCase):
    def test_an_offset_is_written_the_way_a_person_says_it(self):
        self.assertEqual(
            format_moment("2026-09-16T13:37:04+08:00"), "2026-09-16 13:37（UTC+8）"
        )

    def test_a_zulu_stamp_keeps_the_moment_it_names(self):
        self.assertEqual(
            format_moment("2026-09-14T18:48:39Z"), "2026-09-14 18:48（UTC+0）"
        )

    def test_a_half_hour_offset_keeps_its_minutes(self):
        self.assertEqual(
            format_moment("2026-09-16T13:37:00+05:30"), "2026-09-16 13:37（UTC+5:30）"
        )

    def test_a_stamp_this_parser_cannot_read_is_passed_through(self):
        self.assertEqual(format_moment("2026年9月16日更新"), "2026年9月16日更新")

    def test_a_date_only_stamp_does_not_invent_midnight(self):
        self.assertEqual(format_moment("2026-09-16"), "2026-09-16")

    def test_an_absent_stamp_reads_as_unknown(self):
        self.assertEqual(format_moment(None), "未知")


class DetectionReportTests(unittest.TestCase):
    """What the scan report says, and in what order it says it."""

    def test_the_report_opens_with_its_title_and_the_time_it_scanned(self):
        with tempfile.TemporaryDirectory() as directory:
            report = render_scan(
                SnapshotStore(Path(directory)),
                [ScannedProvider([scanned_record("m1", "M1", "2", "8")])],
                "2026-09-16T13:37:00+08:00",
            )
            self.assertTrue(report.startswith("模型价格自动检测\n"))
            self.assertIn("时间：2026-09-16 13:37（UTC+8）。", report)
            self.assertIn("主题：全渠道模型与计费变化。", report)

    def test_one_line_states_the_scan_before_the_channels_are_read(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            moved = ScannedProvider(
                [scanned_record("m1", "M1", "2", "8")],
                provider_id="moved",
                provider_name="动了的渠道",
            )
            quiet = ScannedProvider(
                [scanned_record("m1", "M1", "2", "8")],
                provider_id="quiet",
                provider_name="没动的渠道",
            )
            render_scan(store, [moved, quiet], "2026-09-15T10:00:00+08:00")
            report = render_scan(
                store,
                [
                    ScannedProvider(
                        [scanned_record("m1", "M1", "1", "8")],
                        provider_id="moved",
                        provider_name="动了的渠道",
                    ),
                    quiet,
                ],
                "2026-09-16T10:00:00+08:00",
            )
            self.assertIn(
                "【结论】\n\n2 个渠道共 2 个模型：1 个有变化，1 个无变化，0 个未能完成。",
                report,
            )
            self.assertLess(report.index("【结论】"), report.index("【渠道概览】"))

    def test_every_scanned_channel_is_named_even_when_it_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            working = ScannedProvider(
                [scanned_record("m1", "M1", "2", "8", updated_at="2026-09-14T03:03:07Z")],
                provider_id="ok",
                provider_name="好渠道",
            )
            render_scan(store, [working], "2026-09-15T10:00:00+08:00")
            report = render_scan(
                store,
                [
                    working,
                    ScannedProvider(
                        error=SourceError("offline"),
                        provider_id="bad",
                        provider_name="坏渠道",
                    ),
                ],
                "2026-09-16T10:00:00+08:00",
            )
            self.assertIn("好渠道", report)
            self.assertIn("   状态：无变化。", report)
            self.assertIn("   官方更新时间：2026-09-14 03:03（UTC+0）。", report)
            self.assertIn("坏渠道", report)
            self.assertIn("   状态：来源解析失败", report)
            self.assertIn("   本次变化：offline", report)
            self.assertIn("   模型数：—", report)
            self.assertNotIn("本次变化：—", report)

    def test_a_source_without_an_official_stamp_uses_the_previous_snapshot_time(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            provider = ScannedProvider([scanned_record("m1", "M1", "2", "8")])
            render_scan(store, [provider], "2026-09-15T10:00:00+08:00")
            # Force the expanded report while this provider itself stays unchanged.
            report = render_scan(
                store,
                [
                    provider,
                    ScannedProvider(
                        error=SourceError("offline"),
                        provider_id="bad",
                        provider_name="坏渠道",
                    ),
                ],
                "2026-09-16T10:00:00+08:00",
            )
            self.assertIn(
                "上次更新时间：2026-09-15 10:00（UTC+8）。",
                report,
            )

    def test_a_channel_that_moved_is_listed_before_the_quiet_ones(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            moved = ScannedProvider(
                [scanned_record("m1", "M1", "2", "8")],
                provider_id="moved",
                provider_name="动了的渠道",
            )
            quiet = ScannedProvider(
                [scanned_record("m1", "M1", "2", "8")],
                provider_id="quiet",
                provider_name="没动的渠道",
            )
            render_scan(store, [quiet, moved], "2026-09-15T10:00:00+08:00")
            report = render_scan(
                store,
                [
                    quiet,
                    ScannedProvider(
                        [scanned_record("m1", "M1", "1", "8")],
                        provider_id="moved",
                        provider_name="动了的渠道",
                    ),
                ],
                "2026-09-16T10:00:00+08:00",
            )
            self.assertLess(report.index("动了的渠道"), report.index("没动的渠道"))

    def test_a_channel_that_held_still_has_no_block_of_its_own(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            quiet = ScannedProvider(
                [scanned_record("m1", "M1", "2", "8")],
                provider_id="quiet",
                provider_name="没动的渠道",
            )
            moved = ScannedProvider(
                [scanned_record("m1", "M1", "2", "8")],
                provider_id="moved",
                provider_name="动了的渠道",
            )
            render_scan(store, [quiet, moved], "2026-09-15T10:00:00+08:00")
            report = render_scan(
                store,
                [
                    quiet,
                    ScannedProvider(
                        [scanned_record("m1", "M1", "2", "8"), scanned_record("m2", "M2", "3", "12")],
                        provider_id="moved",
                        provider_name="动了的渠道",
                    ),
                ],
                "2026-09-16T10:00:00+08:00",
            )
            self.assertIn("没动的渠道", report)
            detail = report.split("【变化详情】")[1]
            self.assertIn("1. 动了的渠道", detail)
            self.assertNotIn("没动的渠道", detail)

    def test_a_channel_entry_says_what_moved_rather_than_only_that_something_did(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            render_scan(
                store,
                [ScannedProvider([scanned_record("m1", "M1", "2", "8")])],
                "2026-09-15T10:00:00+08:00",
            )
            report = render_scan(
                store,
                [
                    ScannedProvider(
                        [
                            scanned_record("m1", "M1", "1", "8"),
                            scanned_record("m2", "M2", "3", "12"),
                        ]
                    )
                ],
                "2026-09-16T10:00:00+08:00",
            )
            self.assertIn("   本次变化：新增模型 1；价格变化 1", report)

    def test_the_scan_closes_with_what_its_channels_add_up_to(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            render_scan(
                store,
                [ScannedProvider([scanned_record("m1", "M1", "2", "8")])],
                "2026-09-15T10:00:00+08:00",
            )
            report = render_scan(
                store,
                [
                    ScannedProvider(
                        [scanned_record("m1", "M1", "1", "8"), scanned_record("m2", "M2", "3", "12")]
                    ),
                    ScannedProvider(
                        error=SourceError("offline"),
                        provider_id="bad",
                        provider_name="坏渠道",
                    ),
                ],
                "2026-09-16T10:00:00+08:00",
            )
            self.assertIn("【小结】", report)
            self.assertIn("1 个渠道读取成功；1 个未能完成", report)
            self.assertIn("1 个有变化，见上「变化详情」", report)
            self.assertGreater(report.index("【小结】"), report.index("【变化详情】"))

    def test_the_scan_reports_its_own_skill_update_check(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = scan_providers(
                [ScannedProvider([scanned_record("m1", "M1", "2", "8")])],
                SnapshotStore(Path(directory)),
                captured_at="2026-09-16T13:37:00+08:00",
            )
            payload["skill_update"] = {
                "status": "up_to_date",
                "checked_at": "2026-09-16T13:37:00+08:00",
            }
            report = scan_message(payload)
            self.assertIn("【Skill 更新检查】", report)
            self.assertIn("当前 skill 已是远端版本；检查时间 2026-09-16 13:37（UTC+8）", report)


if __name__ == "__main__":
    unittest.main()
