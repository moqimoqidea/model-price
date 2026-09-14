import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.caching import CacheStore, CachedPriceSource
from model_price.core import PriceSource
from model_price.errors import SourceError
from model_price.models import model_matches, normalize_model, strip_footnote_markers
from model_price.parsing import markdown_tables, split_markdown_row, token_price_kind
from model_price.providers.anthropic import ANTHROPIC_MARKDOWN_URL, AnthropicAdapter
from model_price.providers.deepseek import DEEPSEEK_URL, DeepSeekAdapter
from model_price.providers.google import (
    GEMINI_MARKDOWN_URL,
    GEMINI_URL,
    GeminiAdapter,
)
from model_price.providers.kimi import KIMI_INDEX_URL, KimiAdapter
from model_price.providers.openai import OPENAI_MARKDOWN_URL, OpenAIAdapter
from model_price.providers.tencent import expand_slate_table, tencent_delivery_mode
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
from model_price.reporting import price_lookup, to_markdown
from model_price.updating import GitSkillUpdater


VOLC_MARKDOWN = """# 大语言模型

## 在线推理（常规）

|模型名称 |条件<br><br>千 token |输入(非音频)<br><br>元/百万token |缓存存储<br><br>元/百万token/小时 |缓存命中(非音频)<br><br>元/百万token |输出<br><br>元/百万token |
|---|---|---|---|---|---|
|doubao\\-seed\\-2.0\\-pro |输入长度 [0, 32] |3.2 |0.017 |0.64 |16.0 |
||输入长度 (32, 128] |4.8 |0.017 |0.96 |24.0 |
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


def git_update_responses(status_output=""):
    before = "a" * 40
    after = "b" * 40
    return {
        ("rev-parse", "--show-toplevel"): (0, "/repo\n", ""),
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
            "skills/model-price",
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

    def test_tencent_delivery_modes_are_distinct(self):
        self.assertEqual(
            tencent_delivery_mode("DeepSeek-V4-Pro 原厂直供"),
            "upstream_direct",
        )
        self.assertEqual(tencent_delivery_mode("DeepSeek-V4-Pro"), "self_deployed")


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

        result = GitSkillUpdater(
            Path("/repo/skills/model-price"), runner=runner
        ).update()

        self.assertEqual(result["status"], "updated")
        self.assertLess(
            runner.commands.index(("fetch", "--quiet")),
            runner.commands.index(("merge", "--ff-only", "origin/main")),
        )

    def test_local_changes_prevent_automatic_update(self):
        runner = FakeGitRunner(git_update_responses(" M local.txt\n"))

        result = GitSkillUpdater(
            Path("/repo/skills/model-price"), runner=runner
        ).update()

        self.assertEqual(result["status"], "update_skipped")
        self.assertNotIn(("merge", "--ff-only", "origin/main"), runner.commands)

    def test_fetch_failure_is_reported_without_attempting_a_merge(self):
        responses = git_update_responses()
        responses[("fetch", "--quiet")] = (1, "", "network unavailable")
        runner = FakeGitRunner(responses)

        result = GitSkillUpdater(
            Path("/repo/skills/model-price"), runner=runner
        ).update()

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


XIAOMI_HTML = """<h2>模型国内定价</h2>
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


class MarkdownRenderingTests(unittest.TestCase):
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
        self.assertIn("未给出本工具可解析的价格", to_markdown(payload))


if __name__ == "__main__":
    unittest.main()
