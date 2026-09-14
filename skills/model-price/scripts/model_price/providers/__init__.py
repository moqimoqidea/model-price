"""Every supported price source, in the order reports should list them."""

from __future__ import annotations

from .aliyun import AliyunAdapter
from .anthropic import AnthropicAdapter
from .deepseek import DeepSeekAdapter
from .google import GeminiAdapter
from .kimi import KimiAdapter
from .minimax import MiniMaxAdapter
from .openai import OpenAIAdapter
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
    DeepSeekAdapter,
    KimiAdapter,
    ZhipuAdapter,
    MiniMaxAdapter,
    XiaomiAdapter,
)

OVERSEAS_PROVIDERS = (
    OpenAIAdapter,
    AnthropicAdapter,
    GeminiAdapter,
    XAIAdapter,
)

ALL_PROVIDERS = (*DOMESTIC_PROVIDERS, *OVERSEAS_PROVIDERS)
