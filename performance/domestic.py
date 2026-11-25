"""Name domestic inference routes once, and discover their model IDs without guessing.

Every credential stays in the environment. The profiles below carry only public endpoints,
the variable *names* that must hold a key, and the request shape each vendor documents.
A route is still an individual provider/model/region combination, never a family guess.
"""

from __future__ import annotations

import difflib
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import Target, endpoint_from_base, environment_value, validate_url


@dataclass(frozen=True)
class Profile:
    """One vendor's documented chat route, before a model is chosen."""

    provider: str
    name: str
    url: str
    api_key_env: str
    # Extra variables the endpoint template cannot be resolved without.
    required_env: tuple[str, ...] = ()
    # A full-endpoint override, for a different region or a private gateway.
    url_env: str | None = None
    protocol: str = "chat"
    thinking_mode: str = "provider-default"
    output_limit_field: str = "max_tokens"
    context_window: int = 1048576
    max_output_tokens: int = 131072
    # Public list endpoint, when the vendor documents one; None means "ask for a literal ID".
    models_url: str | None = None
    region: str = "cn"
    note: str = ""


PROFILES: dict[str, Profile] = {
    "deepseek": Profile(
        provider="deepseek", name="深度求索", url="https://api.deepseek.com/chat/completions",
        api_key_env="DEEPSEEK_API_KEY", models_url="https://api.deepseek.com/models",
        note="官方 Chat Completions",
    ),
    "aliyun": Profile(
        provider="aliyun", name="阿里云百炼",
        url=("https://${DASHSCOPE_WORKSPACE_ID}.cn-beijing.maas.aliyuncs.com"
             "/compatible-mode/v1/chat/completions"),
        api_key_env="DASHSCOPE_API_KEY", required_env=("DASHSCOPE_WORKSPACE_ID",),
        url_env="ALIYUN_BENCHMARK_URL",
        models_url=("https://${DASHSCOPE_WORKSPACE_ID}.cn-beijing.maas.aliyuncs.com"
                    "/compatible-mode/v1/models"),
        region="cn-beijing",
        note="工作空间端点；缺少 workspace 时不退回旧地址",
    ),
    "volcengine": Profile(
        provider="volcengine", name="火山引擎方舟",
        url="https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        api_key_env="ARK_API_KEY", url_env="ARK_BASE_URL",
        models_url="https://ark.cn-beijing.volces.com/api/v3/models",
        region="cn-beijing", note="接入点 ID 也可作为 model",
    ),
    "tencent": Profile(
        provider="tencent", name="腾讯云 TokenHub",
        url="https://tokenhub.tencentmaas.com/v1/chat/completions",
        api_key_env="TENCENT_API_KEY", models_url="https://tokenhub.tencentmaas.com/v1/models",
        region="guangzhou",
    ),
    "moonshot": Profile(
        provider="custom", name="月之暗面 Kimi",
        url="https://api.moonshot.cn/v1/chat/completions",
        api_key_env="MOONSHOT_API_KEY", models_url="https://api.moonshot.cn/v1/models",
        output_limit_field="max_completion_tokens",
        note="K3 系列始终思考，不接受 thinking 开关；max_tokens 已弃用",
    ),
    "zhipu": Profile(
        provider="zhipu", name="智谱 BigModel",
        url="https://open.bigmodel.cn/api/paas/v4/chat/completions",
        api_key_env="ZHIPU_API_KEY",
        models_url="https://open.bigmodel.cn/api/paas/v4/models",
        note="官方也使用 ZAI_API_KEY；本工具只读 ZHIPU_API_KEY",
    ),
    "minimax": Profile(
        provider="custom", name="稀宇 MiniMax",
        url="https://api.minimax.cn/v1/chat/completions",
        api_key_env="MINIMAX_API_KEY", output_limit_field="max_completion_tokens",
        note="M3.1-Flash 只接受 thinking.type=adaptive；未注册自动开关",
    ),
    "baidu": Profile(
        provider="custom", name="百度智能云千帆",
        url="https://qianfan.baidubce.com/v2/chat/completions",
        api_key_env="BAIDU_LLM_API_KEY",
        note="Bearer 使用千帆 API Key（bce-v3/...）；思考参数未核实，只走 provider-default",
    ),
}

ALIASES = {
    "moonshot": "moonshot", "kimi": "moonshot", "月之暗面": "moonshot",
    "zhipu": "zhipu", "bigmodel": "zhipu", "glm": "zhipu", "智谱": "zhipu",
    "minimax": "minimax", "稀宇": "minimax",
    "baidu": "baidu", "qianfan": "baidu", "千帆": "baidu", "ernie": "baidu",
    "aliyun": "aliyun", "bailian": "aliyun", "dashscope": "aliyun", "百炼": "aliyun",
    "tencent": "tencent", "tokenhub": "tencent", "腾讯": "tencent",
    "volcengine": "volcengine", "ark": "volcengine", "方舟": "volcengine", "火山": "volcengine",
    "deepseek": "deepseek", "深度求索": "deepseek",
}


def profile_key(name: str) -> str:
    """Canonical profile key, so a route ID names the vendor rather than its parameter dialect."""
    key = ALIASES.get(name.strip().lower(), name.strip().lower())
    if key not in PROFILES:
        raise ValueError("unknown provider; choose from " + ", ".join(sorted(PROFILES)))
    return key


def resolve_provider(name: str) -> Profile:
    return PROFILES[profile_key(name)]


def _substitute(template: str) -> tuple[str, list[str]]:
    """Resolve documented ${VARS} without ever persisting their values."""
    missing = []
    for name in re.findall(r"\$\{([A-Z0-9_]+)\}", template):
        value = environment_value(name)
        if not value:
            missing.append(name)
            value = "missing-" + name.lower()
        template = template.replace("${" + name + "}", value)
    return template, missing


def build_target(provider: str, model: str, *, base_url: str | None = None,
                 revision: str = "unverified", thinking_mode: str | None = None) -> Target:
    """Build one route from a documented profile plus the exact model the user named."""
    profile = resolve_provider(provider)
    key = profile_key(provider)
    # An override may be a base URL (the usual export) or a complete endpoint; the
    # protocol decides the route either way, and a complete endpoint is not doubled.
    override = base_url or (environment_value(profile.url_env) if profile.url_env else "")
    template = endpoint_from_base(override, profile.protocol) if override else profile.url
    url, missing = _substitute(template)
    validate_url(url)
    target = Target(
        id=f"{key}-{re.sub(r'[^A-Za-z0-9_.-]+', '-', model)}",
        protocol=profile.protocol, url=url, model=model,
        api_key_env=profile.api_key_env, provider=profile.provider,
        thinking_mode=thinking_mode or profile.thinking_mode,
        region=profile.region, revision=revision,
        context_window=profile.context_window, max_output_tokens=profile.max_output_tokens,
        output_limit_field=profile.output_limit_field,
    )
    object.__setattr__(target, "missing_env", tuple(missing))
    return target


def parse_route(value: str) -> tuple[str, str]:
    """Parse PROVIDER=MODEL; the model may contain dots, slashes, and dashes."""
    provider, separator, model = value.partition("=")
    if not separator or not provider.strip() or not model.strip():
        raise ValueError("route must look like PROVIDER=MODEL")
    resolve_provider(provider)
    return provider.strip(), model.strip()


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def fuzzy_models(candidates: list[str], query: str, limit: int = 20) -> list[str]:
    """Rank literal IDs by containment first, then edit similarity; never invent an ID."""
    needle = _normalise(query)
    if not needle:
        return candidates[:limit]
    scored = []
    for candidate in candidates:
        haystack = _normalise(candidate)
        if needle == haystack:
            score = 3.0
        elif needle in haystack:
            score = 2.0 + len(needle) / max(len(haystack), 1)
        else:
            score = difflib.SequenceMatcher(None, needle, haystack).ratio()
        if score >= 0.6:
            scored.append((score, candidate))
    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return [candidate for _, candidate in scored[:limit]]


def list_models(profile_name: str, *, timeout: float = 30.0, opener=None) -> dict:
    """Read a vendor's public model list; failures stay typed and value-free."""
    profile = resolve_provider(profile_name)
    identity = {"provider": profile_name, "name": profile.name, "api_key_env": profile.api_key_env}
    key = environment_value(profile.api_key_env)
    if not key:
        return {**identity, "status": "missing_key",
                "missing_environment": [profile.api_key_env], "models": []}
    if not profile.models_url:
        return {**identity, "status": "no_list_endpoint",
                "error": "该平台未核实公开模型列表接口；请用精确模型 ID，"
                         "或运行 python3 scripts/query_model_prices.py list <provider>",
                "models": []}
    url, missing = _substitute(profile.models_url)
    if missing:
        return {**identity, "status": "missing_environment",
                "missing_environment": missing, "models": []}
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + key,
                                                   "Accept": "application/json"})
    try:
        open_request = opener or urllib.request.urlopen
        with open_request(request, timeout=timeout) as response:
            payload = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        exc.close()
        return {"provider": profile_name, "status": "error", "error": f"http_{exc.code}",
                "http_status": exc.code, "models": []}
    except urllib.error.URLError:
        return {"provider": profile_name, "status": "error", "error": "network_error", "models": []}
    except (ValueError, TypeError, OSError):
        return {"provider": profile_name, "status": "error", "error": "invalid_response", "models": []}
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        items = payload.get("models") if isinstance(payload, dict) else None
    models = []
    for item in items or []:
        identifier = item.get("id") if isinstance(item, dict) else item
        if isinstance(identifier, str) and identifier:
            models.append(identifier)
    return {"provider": profile_name, "status": "ok", "count": len(models),
            "models": sorted(dict.fromkeys(models))}
