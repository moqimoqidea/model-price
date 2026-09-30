import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.delta import scan_providers
from model_price.descriptions.core import (
    SUMMARY_MAX_CHARS,
    DescriptionSource,
    description_record,
    unavailable_description,
)
from model_price.descriptions.resolver import DescriptionResolver
from model_price.descriptions.sources import (
    AliyunDescriptionSource,
    KLING_IMAGE_CAPABILITY_URL,
    KLING_VIDEO_CAPABILITY_URL,
    KlingDescriptionSource,
    OpenRouterDescriptionSource,
    ANTHROPIC_MODELS_MARKDOWN_URL,
    AnthropicDescriptionSource,
    DeepSeekDescriptionSource,
    KIMI_MODELS_URL,
    KimiDescriptionSource,
    OpenAIDescriptionSource,
    TencentMirrorDescriptionSource,
    VolcengineDescriptionSource,
)
from model_price.descriptions.tencent_mirror import (
    TENCENT_MODELS_URL,
    build_tencent_mirror,
    validate_tencent_mirror,
)
from model_price.deepseek_updates import DEEPSEEK_UPDATES_URL
from model_price.errors import SourceError
from model_price.pricing import make_record, price_item
from model_price.providers.openrouter import (
    OPENROUTER_MODELS_URL,
    OPENROUTER_VIDEOS_URL,
)
from model_price.providers.aliyun import (
    ALIYUN_API_URL,
    qianwen_model_metadata,
    qianwen_model_url,
)
from model_price.registry import query_adapters
from model_price.messages import comparison_message, scan_message
from model_price.snapshots import SnapshotStore


class MappingClient:
    def __init__(self, mapping):
        self.mapping = mapping
        self.requests = []

    def get_text(self, url):
        self.requests.append(url)
        value = self.mapping[url]
        if isinstance(value, Exception):
            raise value
        return value


# The vendor's own update log, trimmed to the two entries these tests turn on. The
# Vision-Exp model was withdrawn eleven days after it was announced, so the entry
# that announces it is also the entry that dates its withdrawal.
DEEPSEEK_UPDATES_PAGE = """<link rel="canonical" href="https://api-docs.deepseek.com/zh-cn/updates">
<h2>时间: 2026-08-21</h2>
<h3>DeepSeek-V4-Flash-Vision-Exp 发布</h3>
<p>我们发布 DeepSeek-V4-Flash-Vision-Exp 视觉实验模型。</p>
<h2>时间: 2026-04-24</h2>
<h3>DeepSeek-V4 发布</h3>
<p>我们发布 DeepSeek-V4 模型。</p>
"""


class QianwenDescriptionClient:
    def __init__(self, items=()):
        self.items = list(items)
        self.requests = []

    def post_form(self, url, fields, *, idempotent=False):
        self.requests.append((url, dict(fields), json.loads(fields["params"])))
        return {
            "code": "200",
            "data": {
                "Data": [{"Items": self.items}],
                "Ext": {"totalCount": 1},
            },
        }


class FakeDescriptionSource(DescriptionSource):
    source_kind = "test"

    def __init__(self, source_id, summary):
        self.source_id = source_id
        self.source_name = source_id
        self.source_url = f"https://example.test/{source_id}"
        self.summary = summary
        self.calls = []

    def describe(self, model_id, display_name="", *, record=None):
        self.calls.append(model_id)
        return description_record(
            model_id,
            display_name or model_id,
            self.summary,
            self.source_url,
            self.source_kind,
            source_name=self.source_name,
            lifecycle="active",
        )


class BrokenDescriptionSource(FakeDescriptionSource):
    def describe(self, model_id, display_name="", *, record=None):
        raise RuntimeError("official introduction unavailable")


class DescriptionSourceTests(unittest.TestCase):
    def test_volcengine_percent_encodes_non_ascii_model_ids(self):
        url = (
            "https://console.volcengine.com/ark/region:cn-beijing/model/detail?"
            "name=deepseek-v4-flash%E9%A2%84%E8%A7%88%E7%89%88"
        )
        client = MappingClient({url: "<title>Model Square</title>"})
        self.assertIsNone(
            VolcengineDescriptionSource(client).describe("deepseek-v4-flash预览版")
        )
        self.assertEqual(client.requests, [url])

    def test_deepseek_preview_does_not_borrow_another_models_release(self):
        # A separately priced preview build is not the dated vision experiment the
        # log announced under its own name, and reading one must not answer for the
        # other.
        client = MappingClient({DEEPSEEK_UPDATES_URL: DEEPSEEK_UPDATES_PAGE})
        source = DeepSeekDescriptionSource(client)
        self.assertIsNone(source.describe("deepseek-v4-flash预览版"))
        self.assertEqual(client.requests, [DEEPSEEK_UPDATES_URL])

    def test_deepseek_announced_model_keeps_its_own_release_entry(self):
        client = MappingClient({DEEPSEEK_UPDATES_URL: DEEPSEEK_UPDATES_PAGE})
        record = DeepSeekDescriptionSource(client).describe("deepseek-v4-flash-vision-exp")
        self.assertEqual(record["display_name"], "DeepSeek-V4-Flash-Vision-Exp")
        self.assertEqual(record["lifecycle"], "retired")
        self.assertEqual(record["source"]["url"], DEEPSEEK_UPDATES_URL)

    def test_deepseek_reads_the_log_once_however_many_models_it_describes(self):
        client = MappingClient({DEEPSEEK_UPDATES_URL: DEEPSEEK_UPDATES_PAGE})
        source = DeepSeekDescriptionSource(client)
        source.describe("deepseek-v4-flash-vision-exp")
        source.describe("deepseek-v4")
        self.assertEqual(client.requests, [DEEPSEEK_UPDATES_URL])

    def test_markdown_detail_keeps_summary_features_and_limits(self):
        url = "https://developers.openai.com/api/docs/models/gpt-test.md"
        source = OpenAIDescriptionSource(
            MappingClient(
                {
                    url: """# GPT Test

> Built for coding and research.

Model ID: `gpt-test`

## Model details

- Input modalities: text, image
- Output modalities: text
- Context window: 200,000

## Supported features

- function_calling
- image_input
"""
                }
            )
        )
        result = source.describe("gpt-test")
        self.assertEqual(result["summary"], "Built for coding and research.")
        self.assertEqual(result["capabilities"], ["function_calling", "image_input"])
        self.assertEqual(result["specifications"]["context_window"], "200,000")
        self.assertEqual(result["lifecycle"], "active")

    def test_anthropic_uses_the_official_index_link_for_dotted_versions(self):
        page = "https://platform.claude.com/docs/en/models/opus-5-5/overview"
        index = f"""## Compare models

| Feature | Claude Opus 5.5 |
|---|---|
| Model page | [Claude Opus 5.5]({page}) |
| Claude API ID | `claude-opus-5-5` |
| Claude API alias | `claude-opus-5-5` |
"""
        detail = """# Claude Opus 5.5

Model ID: `claude-opus-5-5`

## Overview

Built for long-running agentic coding and knowledge work.

## How it compares

| Model | Context | Max output | Price / MTok | Knowledge cutoff |
|---|---|---|---|---|
| Claude Opus 5.5 | 1M | 128K | $4 / $20 | Jun 2026 |

### Capabilities

| Feature | Value |
|---|---|
| [Context window](https://example.test/context) | 1M tokens |
| Max output | 128K tokens |
| Reliable knowledge cutoff | Jun 2026 |
"""
        client = MappingClient(
            {
                ANTHROPIC_MODELS_MARKDOWN_URL: index,
                f"{page}.md": detail,
            }
        )
        source = AnthropicDescriptionSource(client)

        result = source.describe("claude-opus-5.5", "Claude Opus 5.5")

        self.assertEqual(
            result["summary"],
            "Built for long-running agentic coding and knowledge work.",
        )
        self.assertEqual(result["source"]["url"], page)
        self.assertEqual(
            result["specifications"],
            {
                "context_window": "1M tokens",
                "max_output": "128K tokens",
                "knowledge_cutoff": "Jun 2026",
            },
        )
        self.assertEqual(
            client.requests,
            [ANTHROPIC_MODELS_MARKDOWN_URL, f"{page}.md"],
        )

    def test_an_indexed_anthropic_page_failure_is_a_source_error(self):
        page = "https://platform.claude.com/docs/en/models/opus-5-5/overview"
        source = AnthropicDescriptionSource(
            MappingClient(
                {
                    ANTHROPIC_MODELS_MARKDOWN_URL: (
                        f"[Claude Opus 5.5]({page})"
                    ),
                    f"{page}.md": SourceError(
                        "request rejected by platform.claude.com: HTTP 404"
                    ),
                }
            )
        )
        result = DescriptionResolver({"anthropic": source}).resolve(
            [
                {
                    "model_id": "claude-opus-5.5",
                    "display_name": "Claude Opus 5.5",
                    "provider_id": "anthropic",
                }
            ]
        )

        self.assertEqual(result["status"], "source_error")
        self.assertEqual(result["attempted_sources"][0]["status"], "source_error")

    def test_an_unreadable_anthropic_index_stays_a_source_error(self):
        source = AnthropicDescriptionSource(
            MappingClient({ANTHROPIC_MODELS_MARKDOWN_URL: "# Models"})
        )

        for model_id in ("claude-one", "claude-two"):
            with self.assertRaisesRegex(
                SourceError, "index published no model detail links"
            ):
                source.describe(model_id)

    def test_markdown_overview_marks_retired_models_honestly(self):
        source = KimiDescriptionSource(
            MappingClient(
                {
                    KIMI_MODELS_URL: """## 多模态模型

| 模型名称 | 描述 |
|---|---|
| `kimi-k3` | 软件工程、知识工作和深度推理 |

## 已下线模型

| 模型名称 | 描述 |
|---|---|
| `kimi-k2.5` | 已下线 |
"""
                }
            )
        )
        self.assertEqual(source.describe("kimi-k3")["lifecycle"], "active")
        retired = source.describe("kimi-k2.5")
        self.assertEqual(retired["lifecycle"], "retired")
        self.assertEqual(retired["summary"], "已下线")

    def test_aliyun_record_metadata_avoids_a_second_catalogue_request(self):
        item = {
            "Model": "qwen/test",
            "Description": "面向长文档理解的模型。",
            "Capabilities": ["TG"],
            "Features": ["function-calling"],
            "ModelInfo": {
                "ContextWindow": 1000000,
                "MaxInputTokens": 991808,
                "MaxOutputTokens": 8192,
            },
            "InferenceMetadata": {
                "RequestModality": ["Text", "Image"],
                "ResponseModality": ["Text"],
            },
            "VersionTag": "PREVIEW",
        }
        client = QianwenDescriptionClient()
        result = AliyunDescriptionSource(client).describe(
            "qwen/test",
            "Qwen Test",
            record={"model_metadata": qianwen_model_metadata(item)},
        )

        self.assertEqual(client.requests, [])
        self.assertEqual(result["display_name"], "Qwen Test")
        self.assertEqual(result["summary"], "面向长文档理解的模型。")
        self.assertEqual(result["capabilities"], ["TG", "function-calling"])
        self.assertEqual(result["lifecycle"], "preview")
        self.assertEqual(result["specifications"]["context_window"], 1000000)
        self.assertEqual(result["specifications"]["input_modalities"], "Text、Image")
        self.assertEqual(result["source"]["url"], qianwen_model_url("qwen/test"))

    def test_aliyun_description_fallback_searches_the_public_catalogue(self):
        client = QianwenDescriptionClient(
            [
                {
                    "Model": "qwen3.8-flash",
                    "Name": "Qwen3.8-Flash",
                    "Description": "高吞吐多模态模型。",
                    "ModelInfo": {"MaxOutputTokens": 131072},
                }
            ]
        )
        result = AliyunDescriptionSource(client).describe("qwen3.8-flash")

        self.assertEqual(len(client.requests), 1)
        url, fields, params = client.requests[0]
        self.assertEqual(url, ALIYUN_API_URL)
        self.assertEqual(fields["product"], "AliyunDeliveryService")
        self.assertEqual(fields["action"], "ListModelSeries")
        self.assertNotIn("sec_token", fields)
        self.assertEqual(params["Query"], "qwen3.8-flash")
        self.assertEqual(params["Language"], "zh-CN")
        self.assertEqual(result["display_name"], "Qwen3.8-Flash")
        self.assertEqual(result["specifications"]["max_output_tokens"], 131072)
        self.assertEqual(
            result["source"]["url"], qianwen_model_url("qwen3.8-flash")
        )


class TencentMirrorTests(unittest.TestCase):
    def capture(self):
        return {
            "schema_version": 1,
            "captured_at": "2026-09-17T00:00:00+08:00",
            "source_url": TENCENT_MODELS_URL,
            "models": [
                {
                    "model_id": "DeepSeek-V4-Pro 0813 正式版",
                    "display_name": "DeepSeek-V4-Pro 0813 正式版",
                    "summary": "旗舰模型，适合复杂推理与专业代码。",
                    "capabilities": ["文本生成", "深度思考"],
                    "lifecycle": "active",
                    "specifications": {"brand": "DeepSeek"},
                },
                {
                    "model_id": "DeepSeek-V4-Pro 0813 正式版",
                    "display_name": "DeepSeek-V4-Pro 0813 正式版",
                    "summary": "由 DeepSeek 直接提供的模型服务。",
                    "capabilities": ["深度思考", "文本生成"],
                    "lifecycle": "active",
                    "specifications": {
                        "brand": "DeepSeek",
                        "badge": "原厂直供",
                    },
                },
            ],
        }

    def test_builder_deduplicates_delivery_cards_and_adds_api_aliases(self):
        mirror = build_tencent_mirror(
            self.capture(),
            [
                {
                    "model_id": "deepseek/deepseek-v4-pro-0813",
                    "display_name": "DeepSeek-V4-Pro 0813 正式版 原厂直供",
                }
            ],
        )
        self.assertEqual(len(mirror["models"]), 1)
        item = mirror["models"][0]
        self.assertEqual(
            item["summary"], "旗舰模型，适合复杂推理与专业代码。"
        )
        self.assertEqual(item["capabilities"], ["文本生成", "深度思考"])
        self.assertEqual(
            item["aliases"], ["deepseek/deepseek-v4-pro-0813"]
        )
        self.assertTrue(item["specifications"]["official_direct_available"])

    def test_source_resolves_alias_and_rejects_an_invalid_mirror(self):
        mirror = build_tencent_mirror(
            self.capture(),
            [
                {
                    "model_id": "deepseek/deepseek-v4-pro-0813",
                    "display_name": "DeepSeek-V4-Pro 0813 正式版 原厂直供",
                }
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tencent.json"
            path.write_text(json.dumps(mirror), encoding="utf-8")
            source = TencentMirrorDescriptionSource(None, path)
            result = source.describe("deepseek/deepseek-v4-pro-0813")
            self.assertEqual(result["status"], "available")

            mirror["models"][0]["summary"] = ""
            with self.assertRaisesRegex(ValueError, "summary is required"):
                validate_tencent_mirror(mirror)

    def test_a_past_sunset_is_retired_and_a_future_one_is_legacy(self):
        capture = self.capture()
        capture["models"] = [
            {
                "model_id": "Past",
                "display_name": "Past",
                "summary": "Past model",
                "capabilities": ["文本生成"],
                "lifecycle": "legacy",
                "specifications": {"sunset_note": "8月31日下线"},
            },
            {
                "model_id": "Future",
                "display_name": "Future",
                "summary": "Future model",
                "capabilities": ["文本生成"],
                "lifecycle": "legacy",
                "specifications": {"sunset_note": "10月9日下线"},
            },
        ]
        lifecycles = {
            item["model_id"]: item["lifecycle"]
            for item in build_tencent_mirror(capture)["models"]
        }
        self.assertEqual(lifecycles, {"Past": "retired", "Future": "legacy"})

    def test_sunset_uses_the_console_region_calendar_day(self):
        capture = self.capture()
        capture["captured_at"] = "2026-09-23T17:35:00Z"
        capture["models"] = [{
            "model_id": "Today", "display_name": "Today", "summary": "Model",
            "capabilities": [], "lifecycle": "legacy",
            "specifications": {"sunset_note": "9月24日下线"},
        }]
        self.assertEqual(build_tencent_mirror(capture)["models"][0]["lifecycle"], "retired")


class SummaryOverLimitTests(unittest.TestCase):
    """A vendor writes an announcement; one message may carry 300 characters."""

    OVER_LIMIT = "腾讯云模型广场的官方卡片介绍，涵盖模型定位、适用场景与调用方式。" * 20

    def test_a_summary_that_fits_is_not_flagged(self):
        record = description_record(
            "m1", "M1", "面向代码与智能体的高吞吐模型。", "https://example.test", "test"
        )
        self.assertFalse(record["summary_needs_condensing"])

    def test_an_over_long_summary_is_kept_whole_and_flagged(self):
        """Cutting would read as the vendor's own wording, so nothing is cut.

        The tool takes no credentials and has no model to ask, so it cannot
        summarize either: it keeps the vendor's words whole, says they are over
        the limit, and leaves the summarizing to whoever sends the message.
        """
        record = description_record(
            "m1", "M1", self.OVER_LIMIT, "https://example.test", "test"
        )
        self.assertEqual(record["summary"], self.OVER_LIMIT)
        self.assertGreater(len(record["summary"]), SUMMARY_MAX_CHARS)
        self.assertTrue(record["summary_needs_condensing"])

    def test_a_summary_exactly_on_the_limit_is_not_flagged(self):
        record = description_record(
            "m1", "M1", "甲" * SUMMARY_MAX_CHARS, "https://example.test", "test"
        )
        self.assertFalse(record["summary_needs_condensing"])
        record = description_record(
            "m1", "M1", "甲" * (SUMMARY_MAX_CHARS + 1), "https://example.test", "test"
        )
        self.assertTrue(record["summary_needs_condensing"])

    def test_the_flag_reaches_the_message_and_the_vendor_words_are_all_there(self):
        payload = {
            "query": "m1",
            "retrieved_at": "2026-09-15T00:00:00+08:00",
            "results": [],
            "source_checks": [],
            "model_descriptions": [
                description_record(
                    "m1", "M1", self.OVER_LIMIT, "https://example.test/m1", "test"
                )
            ],
        }
        message = comparison_message(payload)
        self.assertIn(f"原文 {len(self.OVER_LIMIT)} 字", message)
        self.assertIn(f"超过 {SUMMARY_MAX_CHARS} 字上限", message)
        self.assertIn(self.OVER_LIMIT, message)

    def test_the_mirror_is_flagged_at_read_time_not_rewritten_at_capture(self):
        """The checked-in capture is evidence; the limit is a message concern."""
        capture = {
            "schema_version": 1,
            "captured_at": "2026-09-17T00:00:00+08:00",
            "source_url": TENCENT_MODELS_URL,
            "models": [
                {
                    "model_id": "Long",
                    "display_name": "Long",
                    "summary": self.OVER_LIMIT,
                    "capabilities": ["文本生成"],
                    "lifecycle": "active",
                    "specifications": {"brand": "Test"},
                }
            ],
        }
        mirror = build_tencent_mirror(capture)
        # The capture keeps what the console exported, word for word.
        self.assertEqual(mirror["models"][0]["summary"], self.OVER_LIMIT)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tencent.json"
            path.write_text(json.dumps(mirror), encoding="utf-8")
            result = TencentMirrorDescriptionSource(None, path).describe("Long")
        self.assertIsNotNone(result)
        self.assertEqual(result["summary"], self.OVER_LIMIT)
        self.assertTrue(result["summary_needs_condensing"])

    def test_an_absent_introduction_carries_the_flag_too(self):
        """One record shape, so a consumer reads the key without a default."""
        absent = unavailable_description("m1")
        self.assertFalse(absent["summary_needs_condensing"])


class ResolverTests(unittest.TestCase):
    def test_only_the_hosting_platform_source_is_used(self):
        vendor = FakeDescriptionSource("deepseek", "原厂介绍")
        resolver = DescriptionResolver(
            {
                "deepseek": vendor,
                "aliyun": FakeDescriptionSource("aliyun", "平台介绍"),
            }
        )
        result = resolver.resolve(
            [
                {
                    "model_id": "deepseek-v4.1-flash",
                    "display_name": "DeepSeek-V4.1-Flash",
                    "provider_id": "aliyun",
                }
            ]
        )
        self.assertEqual(result["summary"], "平台介绍")
        self.assertEqual(result["model_id"], "deepseek-v4.1-flash")
        self.assertEqual(result["provider"]["id"], "aliyun")
        self.assertEqual(vendor.calls, [])

    def test_identical_model_ids_keep_their_channel_introductions(self):
        resolver = DescriptionResolver({
            name: FakeDescriptionSource(name, f"{name} 的说明")
            for name in ("deepseek", "tencent")
        })
        descriptions = resolver.resolve_many([
            {"model_id": "deepseek-flash", "provider_id": name}
            for name in ("deepseek", "tencent")
        ])
        self.assertEqual([item["summary"] for item in descriptions], ["deepseek 的说明", "tencent 的说明"])
        self.assertEqual([item["provider"]["id"] for item in descriptions], ["deepseek", "tencent"])

    def test_model_aliases_on_one_channel_are_not_merged(self):
        descriptions = DescriptionResolver({"fake": FakeDescriptionSource("fake", "说明")}).resolve_many([
            {"model_id": name, "provider_id": "fake"}
            for name in ("deepseek-v4-flash", "deepseek-flash")
        ])
        self.assertEqual([item["model_id"] for item in descriptions], ["deepseek-v4-flash", "deepseek-flash"])

    def test_missing_and_broken_channel_sources_never_fall_back_to_vendor(self):
        class MissingSource(FakeDescriptionSource):
            def describe(self, *args, **kwargs):
                return None

        for source, expected in ((None, "not_found"), (MissingSource("tencent", ""), "not_found"), (BrokenDescriptionSource("tencent", ""), "source_error")):
            with self.subTest(expected=expected, source=source):
                vendor = FakeDescriptionSource("deepseek", "不应借用的原厂介绍")
                sources = {"deepseek": vendor}
                if source is not None:
                    sources["tencent"] = source
                result = DescriptionResolver(sources).resolve([{
                    "model_id": "deepseek-flash", "provider_id": "tencent",
                    "reference_url": "https://channel.test/models",
                }])
                self.assertEqual(result["status"], expected)
                self.assertEqual(result["provider"]["id"], "tencent")
                self.assertEqual(result["reference_url"], "https://channel.test/models")
                self.assertEqual(vendor.calls, [])

    def test_a_direct_resolution_rejects_mixed_channels(self):
        with self.assertRaises(ValueError):
            DescriptionResolver({}).resolve([
                {"model_id": "m1", "provider_id": name}
                for name in ("aliyun", "tencent")
            ])

    def test_an_unknown_model_returns_an_explicit_absence(self):
        result = DescriptionResolver({}).resolve(
            [{"model_id": "old-model", "display_name": "Old Model"}]
        )
        self.assertEqual(result["status"], "not_found")
        self.assertIn("旧型号", result["note"])


class EmptyPriceSource:
    provider_id = "fake"
    provider_name = "假渠道"
    source_url = "https://example.test/prices"
    source_kind = "test"

    def search(self, model, exact=False):
        return []


class QueryIntegrationTests(unittest.TestCase):
    def test_a_query_without_prices_still_gets_a_model_introduction_status(self):
        payload = query_adapters(
            [EmptyPriceSource()],
            "unknown-model",
            descriptions=DescriptionResolver({}),
        )
        self.assertEqual(payload["model_descriptions"][0]["status"], "not_found")
        message = comparison_message(payload)
        self.assertIn("【模型介绍】", message)
        self.assertIn("未找到官方独立介绍", message)

    def test_an_introduction_failure_keeps_prices_and_renders_the_error(self):
        class SinglePriceSource(EmptyPriceSource):
            def search(self, model, exact=False):
                return [priced_record(model)]

        payload = query_adapters(
            [SinglePriceSource()],
            "m1",
            descriptions=DescriptionResolver(
                {"fake": BrokenDescriptionSource("fake", "unused")}
            ),
        )
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["model_descriptions"][0]["status"], "source_error")
        self.assertIn("介绍来源读取失败", comparison_message(payload))


def priced_record(model_id, amount="1"):
    return make_record(
        "fake",
        "假渠道",
        model_id,
        model_id.upper(),
        "中国区",
        [
            {
                "name": "standard",
                "conditions": {},
                "prices": [
                    price_item(
                        "input", "输入", amount, "CNY_per_million_tokens"
                    )
                ],
            }
        ],
        "https://example.test/prices",
        "test",
        "2026-09-17T00:00:00+08:00",
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


class NamedDescriptionSource(FakeDescriptionSource):
    """An introduction whose words name the model they belong to."""

    def describe(self, model_id, display_name="", *, record=None):
        return description_record(
            model_id,
            display_name or model_id,
            f"{model_id} 的官方说明",
            self.source_url,
            self.source_kind,
            source_name=self.source_name,
            lifecycle="active",
        )


def crowded_catalogue(count=30):
    """A catalogue that grew by ``count`` models since the baseline."""
    return [
        *[priced_record("base")],
        *[priced_record(f"new-{position}") for position in range(count)],
    ]


class DeltaLimitTests(unittest.TestCase):
    """A scan over the character budget is reported whole, and says it is over."""

    def scan(self, resolver, catalogue, *, max_chars=None, extra=()):
        """Build a scan that moved, then render it for a channel."""
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            scan_providers(
                [ScannedProvider([priced_record("base")]), *extra],
                store,
                captured_at="2026-09-16T00:00:00+08:00",
                descriptions=resolver,
            )
            payload = scan_providers(
                [ScannedProvider(catalogue), *extra],
                store,
                captured_at="2026-09-17T00:00:00+08:00",
                descriptions=resolver,
            )
        if max_chars is None:
            return scan_message(payload)
        return scan_message(payload, max_chars=max_chars)

    def test_a_crowded_scan_still_introduces_every_model_it_reports(self):
        """Thirty new models make a long message; none of them is dropped for it."""
        resolver = DescriptionResolver({"fake": NamedDescriptionSource("fake", "")})
        message = self.scan(resolver, crowded_catalogue())
        self.assertGreater(len(message), 3000)
        self.assertIn("【模型能力】", message)
        self.assertLess(message.index("【模型能力】"), message.index("【模型价格】"))
        for position in (0, 29):
            self.assertIn(f"new-{position} 的官方说明", message)
        self.assertIn("发送前需总结压缩到 3000 字内", message)

    def test_an_over_budget_scan_still_names_every_channel_it_scanned(self):
        """A channel the scan reached is a fact about the scan, not a detail."""
        resolver = DescriptionResolver({"fake": NamedDescriptionSource("fake", "")})
        message = self.scan(
            resolver,
            crowded_catalogue(),
            extra=[
                ScannedProvider(
                    [priced_record("base")],
                    provider_id="quiet",
                    provider_name="安静渠道",
                ),
                ScannedProvider(
                    error=SourceError("official document changed shape"),
                    provider_id="broken",
                    provider_name="坏渠道",
                ),
            ],
        )
        for name in ("假渠道", "安静渠道", "坏渠道"):
            self.assertIn(name, message)
        # A channel that answered with nothing keeps its reason too.
        self.assertIn("official document changed shape", message)
        # And what the summary has to preserve is named for whoever writes it.
        self.assertIn("保留标题、渠道状态与已展示的全部金额", message)

    def test_a_scan_that_fits_carries_no_such_note(self):
        resolver = DescriptionResolver({"fake": NamedDescriptionSource("fake", "")})
        message = self.scan(resolver, crowded_catalogue(1))
        self.assertNotIn("需总结压缩", message)


class DeltaDescriptionTests(unittest.TestCase):
    def test_only_changed_models_are_described_and_rendered(self):
        resolver = DescriptionResolver(
            {"fake": FakeDescriptionSource("fake", "适合代码与智能体任务")}
        )
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            scan_providers(
                [ScannedProvider([priced_record("m1")])],
                store,
                captured_at="2026-09-16T00:00:00+08:00",
                descriptions=resolver,
            )
            payload = scan_providers(
                [ScannedProvider([priced_record("m1"), priced_record("m2")])],
                store,
                captured_at="2026-09-17T00:00:00+08:00",
                descriptions=resolver,
            )
        descriptions = payload["providers"][0]["model_descriptions"]
        self.assertEqual([item["model_id"] for item in descriptions], ["m2"])
        message = scan_message(payload)
        self.assertIn("【模型能力】", message)
        self.assertIn("适合代码与智能体任务", message)
        # The channel's introduction opens the scan, before its price changes.
        self.assertLess(message.index("【模型能力】"), message.index("【模型价格】"))
        self.assertEqual(message.count("适合代码与智能体任务"), 1)

    def test_a_removed_model_keeps_its_introduction_in_the_delta(self):
        resolver = DescriptionResolver(
            {"fake": FakeDescriptionSource("fake", "旧型号的官方说明")}
        )
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            scan_providers(
                [ScannedProvider([priced_record("m1"), priced_record("m2")])],
                store,
                captured_at="2026-09-16T00:00:00+08:00",
                descriptions=resolver,
            )
            payload = scan_providers(
                [ScannedProvider([priced_record("m1")])],
                store,
                captured_at="2026-09-17T00:00:00+08:00",
                descriptions=resolver,
            )
        descriptions = payload["providers"][0]["model_descriptions"]
        self.assertEqual([item["model_id"] for item in descriptions], ["m2"])
        message = scan_message(payload)
        self.assertIn("下架模型", message)
        self.assertIn("旧型号的官方说明", message)


if __name__ == "__main__":
    unittest.main()


OPENROUTER_CATALOGUE = json.dumps(
    {
        "data": [
            {
                "id": "qwen/qwen3.8-27b:free",
                "name": "Qwen: Qwen3.8 27B (free)",
                "description": "An open-weight vision-language model from Qwen.",
                "context_length": 262144,
                "architecture": {
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text"],
                    "tokenizer": "Qwen",
                },
                "pricing": {"prompt": "0", "completion": "0"},
                "supported_parameters": ["tools"],
                "top_provider": {"max_completion_tokens": 65536},
            },
            {
                "id": "bytedance/seedance-2.5",
                "name": "ByteDance: Seedance 2.5",
                "description": "A video generation model from ByteDance.",
                "architecture": {
                    "input_modalities": ["text"],
                    "output_modalities": ["video"],
                },
                "pricing": {"prompt": "0", "completion": "0"},
            },
            {
                "id": "bytedance-seed/seed-1.6",
                "name": "ByteDance Seed: Seed 1.6",
                "description": "A general model.",
                "expiration_date": "2026-11-11",
                "architecture": {"output_modalities": ["text"]},
                "pricing": {"prompt": "0.0000001", "completion": "0.0000005"},
            },
        ]
    }
)

OPENROUTER_VIDEOS = json.dumps(
    {
        "data": [
            {
                "id": "bytedance/seedance-2.5",
                "pricing_skus": {"duration_seconds_720p": "0.1"},
            }
        ]
    }
)

KLING_CAPABILITY_VIDEO = """# 视频能力地图

## Models

| Model | Description | Input | Generation Range | Resolution |
| --- | --- | --- | --- | --- |
| Kling 3.0 | 音画同步升级，支持多镜头叙事 | 文本、图片、视频 | 3~15s | 720P、1080P、4K |

## Global Capabilities

| Capability | Value | Description |
| --- | --- | --- |
| 对口型 | 不区分模型版本 | 可结合文案或音频，驱动视频中角色的口型 |
"""

KLING_CAPABILITY_IMAGE = """# 图片能力地图

## Models

| Model | Description | Input | Generation Range | Resolution |
| --- | --- | --- | --- | --- |
| Kling Image 3.0 | 强化一致性，全面效果升级 | 文本、图片 | 16:9、1:1 | 1K, 2K |
"""


class OpenRouterDescriptionTests(unittest.TestCase):
    def source(self):
        return OpenRouterDescriptionSource(
            MappingClient(
                {
                    OPENROUTER_MODELS_URL: OPENROUTER_CATALOGUE,
                    OPENROUTER_VIDEOS_URL: OPENROUTER_VIDEOS,
                }
            )
        )

    def test_a_free_entry_is_described_as_charging_nothing(self):
        description = self.source().describe("qwen/qwen3.8-27b:free")
        self.assertEqual(
            description["summary"], "An open-weight vision-language model from Qwen."
        )
        self.assertEqual(description["capabilities"][0], "输入：文本、图像")
        self.assertIn(
            "官方公布价格为 0（免费档位或测试期），未作为价格记录",
            description["capabilities"],
        )
        self.assertEqual(description["specifications"]["context_window"], 262144)

    def test_a_model_the_video_document_prices_is_not_described_as_free(self):
        description = self.source().describe("bytedance/seedance-2.5")
        self.assertNotIn(
            "官方公布价格为 0（免费档位或测试期），未作为价格记录",
            description["capabilities"],
        )
        self.assertEqual(description["capabilities"], ["输入：文本", "输出：视频"])

    def test_a_dated_entry_is_described_as_scheduled_to_end(self):
        description = self.source().describe("bytedance-seed/seed-1.6")
        self.assertEqual(description["lifecycle"], "legacy")
        self.assertEqual(
            description["specifications"]["sunset_note"], "2026-11-11 停止提供"
        )

    def test_an_unknown_model_has_no_description(self):
        self.assertIsNone(self.source().describe("vendor/absent"))


class KlingDescriptionTests(unittest.TestCase):
    def source(self):
        return KlingDescriptionSource(
            MappingClient(
                {
                    KLING_VIDEO_CAPABILITY_URL: KLING_CAPABILITY_VIDEO,
                    KLING_IMAGE_CAPABILITY_URL: KLING_CAPABILITY_IMAGE,
                }
            )
        )

    def test_a_model_is_described_from_the_page_its_modality_is_on(self):
        description = self.source().describe("kling-3.0")
        self.assertEqual(description["summary"], "音画同步升级，支持多镜头叙事")
        self.assertEqual(
            description["capabilities"],
            [
                "Input：文本、图片、视频",
                "Generation Range：3~15s",
                "Resolution：720P、1080P、4K",
            ],
        )
        self.assertEqual(description["source"]["url"], KLING_VIDEO_CAPABILITY_URL)
        image = self.source().describe("Kling Image 3.0")
        self.assertEqual(image["source"]["url"], KLING_IMAGE_CAPABILITY_URL)

    def test_a_capability_the_price_page_also_prices_is_described(self):
        description = self.source().describe("对口型")
        self.assertEqual(description["summary"], "可结合文案或音频，驱动视频中角色的口型")

    def test_the_range_separator_survives_the_markdown_reduction(self):
        description = self.source().describe("kling-3.0")
        self.assertIn("3~15s", " ".join(description["capabilities"]))

    def test_an_unknown_model_has_no_description(self):
        self.assertIsNone(self.source().describe("kling-9.9"))
