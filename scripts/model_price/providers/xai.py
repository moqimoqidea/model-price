"""xAI (Grok) first-party pay-as-you-go pricing.

The documentation also bills some models at a higher rate once a request reaches
a prompt-token threshold. Those tiers live in the model cell, so the shared
tabular adapter keeps the parenthetical as a ``context_tier`` condition.
"""

from __future__ import annotations

from .base import TabularTokenPricingAdapter

XAI_URL = "https://docs.x.ai/developers/pricing"
XAI_MARKDOWN_URL = f"{XAI_URL}.md"


class XAIAdapter(TabularTokenPricingAdapter):
    provider_id = "xai"
    provider_name = "xAI"
    # The rendered HTML page splits the text-pricing header across two rows with
    # merged cells; the official Markdown variant keeps a single header row.
    source_url = XAI_MARKDOWN_URL
    source_kind = "official_markdown"
    currency = "USD"
    region = "全球"
