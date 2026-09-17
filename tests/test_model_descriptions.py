import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.delta import scan_providers
from model_price.descriptions.core import DescriptionSource, description_record
from model_price.descriptions.resolver import DescriptionResolver
from model_price.descriptions.sources import (
    KIMI_MODELS_URL,
    KimiDescriptionSource,
    OpenAIDescriptionSource,
    TencentMirrorDescriptionSource,
)
from model_price.descriptions.tencent_mirror import (
    TENCENT_MODELS_URL,
    build_tencent_mirror,
    validate_tencent_mirror,
)
from model_price.pricing import make_record, price_item
from model_price.registry import query_adapters
from model_price.reporting import delta_to_markdown, to_markdown
from model_price.snapshots import SnapshotStore


class MappingClient:
    def __init__(self, mapping):
        self.mapping = mapping

    def get_text(self, url):
        return self.mapping[url]


class FakeDescriptionSource(DescriptionSource):
    source_kind = "test"

    def __init__(self, source_id, summary):
        self.source_id = source_id
        self.source_name = source_id
        self.source_url = f"https://example.test/{source_id}"
        self.summary = summary

    def describe(self, model_id, display_name="", *, record=None):
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


class ResolverTests(unittest.TestCase):
    def test_first_party_source_precedes_the_hosting_platform(self):
        resolver = DescriptionResolver(
            {
                "deepseek": FakeDescriptionSource("deepseek", "原厂介绍"),
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
        self.assertEqual(result["summary"], "原厂介绍")

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
        report = to_markdown(payload)
        self.assertIn("## 模型介绍", report)
        self.assertIn("未找到官方独立介绍", report)

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
        self.assertIn("介绍来源读取失败", to_markdown(payload))


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
    provider_id = "fake"
    provider_name = "假渠道"
    source_url = "https://example.test/prices"
    source_kind = "test"

    def __init__(self, records):
        self.records = records

    def catalog_records(self):
        return self.records


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
        report = delta_to_markdown(payload)
        self.assertIn("**模型介绍**", report)
        self.assertIn("适合代码与智能体任务", report)

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
        report = delta_to_markdown(payload)
        self.assertIn("下架模型", report)
        self.assertIn("旧型号的官方说明", report)


if __name__ == "__main__":
    unittest.main()
