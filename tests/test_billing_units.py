"""The billing-unit vocabulary and the rates one cell publishes.

These are the readings every adapter now depends on, so they are tested on the
vendor wordings they were written from rather than only through a provider's
document: a unit that stops being recognised, or a cell that starts returning one
amount where it published four, would otherwise surface as a price that quietly
changed rather than as a failure.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.parsing import (
    cell_rates,
    describes_request,
    header_unit_phrase,
    monetary_amount,
    price_column_kinds,
    price_kind,
    price_unit_code,
)
from model_price.pricing import (
    discount_multiplier,
    discounted_amount,
    is_commitment_unit,
    unit_code,
    unit_measure,
    unit_parts,
    without_discount_terms,
)
from model_price.reporting import amount_with_unit, unit_label


class UnitVocabularyTests(unittest.TestCase):
    """One unit, one code, however the vendor spelled it."""

    def test_a_unit_is_read_in_the_wording_a_vendor_publishes(self):
        for label, code in (
            ("元/百万 Tokens", "CNY_per_million_tokens"),
            ("每百万tokens", "CNY_per_million_tokens"),
            ("1M tokens", "CNY_per_million_tokens"),
            ("输入 / 1M tokens", "CNY_per_million_tokens"),
            ("元/百万 Tokens/小时", "CNY_per_million_tokens_per_hour"),
            ("元/万字符", "CNY_per_10k_characters"),
            ("元/千 Tokens", "CNY_per_thousand_tokens"),
            ("每张", "CNY_per_image"),
            ("每秒", "CNY_per_second"),
            ("元/小时", "CNY_per_hour"),
            ("¥0.5 /小时", "CNY_per_hour"),
            ("元/个", "CNY_per_item"),
            ("元/首", "CNY_per_song"),
            ("元/视频", "CNY_per_video"),
            ("元/页", "CNY_per_page"),
            ("元/次", "CNY_per_request"),
            ("次", "CNY_per_request"),
            ("每 1,000 requests", "CNY_per_thousand_requests"),
            ("元/千次", "CNY_per_thousand_requests"),
        ):
            with self.subTest(label=label):
                self.assertEqual(unit_code(label), code)

    def test_a_usd_unit_is_read_as_the_currency_the_adapter_reads_in(self):
        for label, code in (
            ("$0.04 / image", "USD_per_image"),
            ("per second in USD", "USD_per_second"),
            ("per frame", "USD_per_frame"),
            ("per 1M tokens", "USD_per_million_tokens"),
            ("$15.00 / 1M characters", "USD_per_million_characters"),
            ("$0.006 / minute", "USD_per_minute"),
        ):
            with self.subTest(label=label):
                self.assertEqual(unit_code(label, "USD"), code)

    def test_a_measure_a_vendor_counts_out_is_not_matched_without_its_separator(self):
        # "秒" alone would also match a duration column, so only a counted unit
        # ("元/秒", "每秒") names one.
        self.assertIsNone(unit_measure("输入视频时长（秒）"))
        self.assertIsNone(unit_measure("输出视频分辨率"))

    def test_a_monthly_rate_is_a_commitment_rather_than_a_use(self):
        self.assertTrue(is_commitment_unit("预付费价格（单位：元/个/月）"))
        self.assertIsNone(unit_measure("元/kTPM/月"))
        self.assertIsNone(unit_measure("元/个/月"))
        self.assertEqual(unit_measure("元/个"), "item")

    def test_a_unit_the_vendor_bills_in_keeps_the_vendors_own_wording(self):
        self.assertEqual(unit_code("积分/次"), "积分/次")
        self.assertEqual(unit_code("元/算力单元/天"), "元/算力单元/天")
        self.assertEqual(unit_code(""), "provider_defined")

    def test_a_code_carries_its_currency_and_its_measure(self):
        self.assertEqual(unit_parts("CNY_per_million_tokens"), ("CNY", "million_tokens"))
        self.assertEqual(
            unit_parts("USD_per_million_tokens_per_hour"),
            ("USD", "million_tokens_per_hour"),
        )
        self.assertIsNone(unit_parts("积分/次"))
        self.assertIsNone(unit_parts("每张"))

    def test_a_code_is_written_once_and_read_once(self):
        # The report writes a unit back in its own wording, not the vendor's: the
        # code is the one place a unit is named, and this is what reads it back.
        self.assertEqual(unit_label("CNY_per_million_tokens"), "元/百万 tokens")
        self.assertEqual(unit_label("USD_per_second"), "美元/秒")
        self.assertEqual(unit_label("CNY_per_image"), "元/张")
        self.assertEqual(unit_label("CNY_per_10k_characters"), "元/万字符")
        self.assertEqual(unit_label("积分/次"), "积分/次")

    def test_a_price_with_no_published_unit_shows_its_amount_alone(self):
        self.assertEqual(unit_label("provider_defined"), "")
        self.assertEqual(amount_with_unit("1", "provider_defined"), "1")
        self.assertEqual(amount_with_unit("0.5", "CNY_per_hour"), "0.5 元/小时")


class DiscountTests(unittest.TestCase):
    """A 折 the vendor printed, read as the multiplier it means."""

    def test_folds_are_counted_in_tenths_and_written_either_way(self):
        for written, multiplier in (
            ("限时75折", "0.75"),
            ("7.5折", "0.75"),
            ("4折", "0.4"),
            ("85折", "0.85"),
            ("10折", "1"),
            ("满减", None),
        ):
            with self.subTest(written=written):
                self.assertEqual(discount_multiplier(written), multiplier)

    def test_a_multiplier_reduces_the_rate_it_was_printed_beside(self):
        self.assertEqual(discounted_amount("37.00", "0.75"), ("27.75", "37.00"))
        self.assertEqual(discounted_amount("37.00", "1"), ("37.00", None))
        self.assertEqual(discounted_amount("37.00", None), ("37.00", None))

    def test_a_rate_read_under_a_fold_keeps_the_rate_it_reduces(self):
        self.assertEqual(without_discount_terms("原价 37.00`限时75折`").strip(), "原价 37.00")


class PriceColumnTests(unittest.TestCase):
    """Which column prices a charge, and which one only describes a request."""

    def test_a_header_naming_a_charge_is_a_price_column(self):
        for header, kind in (
            ("输入（命中缓存）", "cache_hit"),
            ("输入（未命中缓存）", "input"),
            ("输出", "output"),
            ("缓存存储（元/百万 Tokens/小时）", "cache_storage"),
            ("在线推理 元/百万token", "million_tokens"),
            ("输出图单价（元/张）", "output"),
            ("输出单价 元/次", "output"),
        ):
            with self.subTest(header=header):
                self.assertEqual(price_kind(header), kind)

    def test_a_header_describing_the_request_is_a_condition(self):
        for header in (
            "条件 输入长度：千 token",
            "条件<br><br>Context length",
            "输入视频时长（秒）",
            "分辨率",
            "说明",
            "Context",
        ):
            with self.subTest(header=header):
                self.assertIsNone(price_kind(header))
        self.assertTrue(describes_request("条件 输入长度：千 token"))

    def test_a_column_whose_cells_are_money_is_a_price_column(self):
        # 小米 heads its ASR column with the billed quantity and prints the money
        # in the cell; the column is a price column and the header still says which
        # direction the charge runs.
        table = [["模型名称", "输入音频时长"], ["mimo-v2.5-asr", "¥0.5 /小时"]]
        self.assertEqual(
            price_column_kinds(table[0], table, currency="CNY"), {1: "input"}
        )

    def test_a_duration_column_is_not_a_price_column(self):
        table = [
            ["模型名称", "输出视频时长（秒）"],
            ["doubao-seedance-2.5", "5"],
        ]
        self.assertEqual(price_column_kinds(table[0], table, currency="CNY"), {})

    def test_a_header_names_its_unit_in_its_brackets(self):
        self.assertEqual(header_unit_phrase("推理输入（元/百万 tokens）"), "元/百万 tokens")
        self.assertEqual(header_unit_phrase("积分单价（元/积分）"), "元/积分")
        self.assertEqual(header_unit_phrase("输入（命中缓存）"), "命中缓存")
        self.assertEqual(header_unit_phrase("在线推理 元/百万token"), "在线推理 元/百万token")

    def test_a_unit_comes_from_the_cell_before_the_header(self):
        self.assertEqual(price_unit_code("/ 1M characters", "Output / cost", currency="USD", default="x"), "USD_per_million_characters")
        self.assertEqual(price_unit_code("", "输出图单价（元/张）", currency="CNY", default="x"), "CNY_per_image")
        self.assertEqual(price_unit_code("", "单价", currency="CNY", default="CNY_per_million_tokens"), "CNY_per_million_tokens")

    def test_a_currency_is_named_before_a_bare_number_is_money(self):
        self.assertEqual(monetary_amount("0.5", "单价（元/百万 Tokens）", "CNY"), "0.5")
        self.assertIsNone(monetary_amount("0.5", "单价", "CNY"))
        self.assertIsNone(monetary_amount("2~30", "输入视频时长（秒）", "CNY"))
        self.assertEqual(monetary_amount("免费", "单价", "CNY"), "0")
        # A free allowance beside a rate is not that row's whole price.
        self.assertIsNone(monetary_amount("首张免费 / 第 2 张起：0.02", "单价", "CNY"))


class CellRateTests(unittest.TestCase):
    """One cell, several tiers: every amount read, under the vendor's own scope."""

    def rates(self, cell, header="", currency="CNY"):
        return [
            (rate.amount, "；".join(rate.conditions), rate.list_amount, rate.discount)
            for rate in cell_rates(cell, header=header, currency=currency)
        ]

    def test_a_cell_prices_each_tier_it_lists(self):
        cell = (
            "**单图生成场景：** <br>* ≤ 261 万像素（分辨率 1.5K 及以下）：0.30"
            "<br>* > 261 万像素（分辨率 1.5K 以上）：0.60"
            "<br>**图层拆分场景：** <br>* ≤ 261 万像素（分辨率 1.5K 及以下）：0.15"
        )
        self.assertEqual(
            self.rates(cell, "输出图单价（元/张）"),
            [
                ("0.30", "单图生成场景；≤ 261 万像素（分辨率 1.5K 及以下）", None, None),
                ("0.60", "单图生成场景；> 261 万像素（分辨率 1.5K 以上）", None, None),
                ("0.15", "图层拆分场景；≤ 261 万像素（分辨率 1.5K 及以下）", None, None),
            ],
        )

    def test_a_scope_line_scopes_the_rates_under_it(self):
        cell = (
            "* 输出视频分辨率为 480p，720p<br>   * 输入不含视频：70.00"
            "<br>   * 输入包含视频：42.00<br>* 输出视频分辨率为 1080p"
            "<br>   * 输入不含视频：77.00"
        )
        self.assertEqual(
            self.rates(cell, "在线推理<br><br>元/百万token"),
            [
                ("70.00", "输出视频分辨率为 480p，720p；输入不含视频", None, None),
                ("42.00", "输出视频分辨率为 480p，720p；输入包含视频", None, None),
                ("77.00", "输出视频分辨率为 1080p；输入不含视频", None, None),
            ],
        )

    def test_a_stated_free_charge_is_a_rate_of_nothing(self):
        self.assertEqual(
            self.rates("* 首张免费<br>* 第 2 张起：0.02", "输入图单价（元/张）"),
            [("0", "首张", None, None), ("0.02", "第 2 张起", None, None)],
        )

    def test_a_rate_printed_beside_its_list_price_keeps_both(self):
        cell = "* 输入不含视频：原价 37.00`限时75折`"
        self.assertEqual(
            self.rates(cell, "在线推理 元/百万token"),
            [("27.75", "输入不含视频", "37.00", "0.75")],
        )

    def test_a_struck_rate_keeps_what_it_is_reduced_from(self):
        self.assertEqual(
            self.rates("~~4.20~~ 2.10", "输入价格 元/百万 tokens"),
            [("2.10", "", "4.20", None)],
        )

    def test_labels_on_one_line_each_price_their_own_rate(self):
        self.assertEqual(
            self.rates("音频：0.18 元/分钟；视频：1.2 元/分钟", "单价"),
            [("0.18", "音频", None, None), ("1.2", "视频", None, None)],
        )

    def test_a_bracket_after_an_amount_describes_that_amount(self):
        self.assertEqual(
            self.rates("$0.40 (720p and 1080p) $0.60 (4k)", "Paid Tier, per second in USD", "USD"),
            [
                ("0.40", "(720p and 1080p)", None, None),
                ("0.60", "(4k)", None, None),
            ],
        )

    def test_a_conversion_printed_beside_a_rate_is_not_a_second_charge(self):
        self.assertEqual(
            self.rates("输入：16 元/百万 Tokens（约 0.0002 元/秒）", "单价"),
            [("16", "输入", None, None)],
        )

    def test_the_vendors_own_credit_is_read_and_kept(self):
        rates = cell_rates("480p：2 积分/次\n720p/1080p：15～60积分/次", header="输出消耗")
        self.assertEqual(
            [(rate.amount, rate.unit_phrase) for rate in rates],
            [("2", "积分/次"), ("15～60", "积分/次")],
        )

    def test_a_sentence_that_explains_a_rate_is_not_a_rate(self):
        # The vendor's working is quoted after ">", and reading it would price the
        # model at the derived rate instead of the one it bills.
        self.assertEqual(
            self.rates("2.40<br>> `3.00 万 token / 次` * `0.80 元/万 token`", "输出单价<br><br>元/次"),
            [("2.40", "", None, None)],
        )

    def test_a_number_inside_a_sentence_is_not_a_price(self):
        self.assertEqual(self.rates("输出视频分辨率为 1080p", "在线推理 元/百万token"), [])

    def test_a_cell_billed_in_another_unit_carries_that_unit(self):
        rates = cell_rates("$15.00 / 1M characters", header="Output / cost", currency="USD")
        self.assertEqual(
            [(rate.amount, rate.unit_phrase) for rate in rates], [("15.00", "/ 1M characters")]
        )
        rates = cell_rates("¥0.5 /小时", header="输入音频时长", currency="CNY")
        self.assertEqual([(rate.amount, rate.unit_phrase) for rate in rates], [("0.5", "/小时")])


if __name__ == "__main__":
    unittest.main()
