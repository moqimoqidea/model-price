"""Xiaomi MiMo first-party pay-as-you-go pricing.

The documentation publishes each model twice: once in CNY for mainland China
and once in USD for overseas. This adapter reads the CNY tables, because
``monetary_amount`` only accepts amounts that name the adapter's currency, which
leaves the overseas rows without a parseable price.
"""

from __future__ import annotations

from .base import TabularTokenPricingAdapter

XIAOMI_URL = "https://mimo.mi.com/docs/zh-CN/price/pay-as-you-go"


class XiaomiAdapter(TabularTokenPricingAdapter):
    provider_id = "xiaomi"
    provider_name = "小米 MiMo"
    source_url = XIAOMI_URL
    source_kind = "official_html"
    currency = "CNY"
    region = "中国区"
