"""Every supported price source, in the order reports should list them."""

from __future__ import annotations

from .aliyun import AliyunAdapter
from .anthropic import AnthropicAdapter
from .aws_bedrock import AWSBedrockAdapter
from .baidu import BaiduAdapter
from .deepseek import DeepSeekAdapter
from .google import GeminiAdapter
from .google_cloud import GoogleCloudAdapter
from .kimi import KimiAdapter
from .kling import KlingAdapter
from .minimax import MiniMaxAdapter
from .openai import OpenAIAdapter
from .openrouter import OpenRouterAdapter
from .tencent import TencentAdapter
from .volcengine import VolcengineAdapter
from .xai import XAIAdapter
from .xiaomi import XiaomiAdapter
from .zhipu import ZhipuAdapter

# Order drives the default `compare` provider list, so keep it stable.
DOMESTIC_PROVIDERS = (
    AliyunAdapter,
    VolcengineAdapter,
    TencentAdapter,
    BaiduAdapter,
    DeepSeekAdapter,
    KimiAdapter,
    ZhipuAdapter,
    MiniMaxAdapter,
    XiaomiAdapter,
    KlingAdapter,
)

# The vendors a model name can be inferred to belong to come first; the aggregator
# and the clouds that resell other vendors' models follow, because a model name
# says nothing about which of them carries it. All of them are reached by
# `--include-overseas` or by naming the provider.
OVERSEAS_PROVIDERS = (
    OpenAIAdapter,
    AnthropicAdapter,
    GeminiAdapter,
    XAIAdapter,
    OpenRouterAdapter,
    GoogleCloudAdapter,
    AWSBedrockAdapter,
)

ALL_PROVIDERS = (*DOMESTIC_PROVIDERS, *OVERSEAS_PROVIDERS)
