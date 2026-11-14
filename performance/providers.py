"""Translate benchmark intent only where a provider/protocol contract has been checked."""

from __future__ import annotations

# Deliberately independent of the price catalogue: catalogue access proves no inference API contract.
CHAT_THINKING = {
    "aliyun": lambda enabled: {"enable_thinking": enabled},
    "deepseek": lambda enabled: {"thinking": {"type": "enabled" if enabled else "disabled"}},
    "tencent": lambda enabled: {"thinking": {"type": "enabled" if enabled else "disabled"}},
    "volcengine": lambda enabled: {"thinking": {"type": "enabled" if enabled else "disabled"}},
}


def generation_parameters(provider: str, protocol: str, mode: str, params: dict) -> dict:
    """Do not mistake an SDK's extra_body wrapper for a field of the HTTP request."""
    result = dict(params)
    if mode == "custom":
        return result
    if set(result) & {"thinking", "enable_thinking", "reasoning", "reasoning_effort", "output_config"}:
        raise ValueError("profile thinking_mode conflicts with raw thinking parameters; use custom mode")
    if mode == "provider-default":
        return result
    enabled = mode == "on"
    if protocol == "chat" and provider in CHAT_THINKING:
        result.update(CHAT_THINKING[provider](enabled))
    elif provider == "deepseek" and protocol == "responses":
        result["reasoning"] = {"effort": "high" if enabled else "none"}
    else:
        raise ValueError("thinking mapping is not verified for this provider/protocol; use custom parameters")
    return result


def non_thinking_requested(params: dict) -> bool:
    return ((params.get("thinking") or {}).get("type") == "disabled"
            or params.get("enable_thinking") is False
            or (params.get("reasoning") or {}).get("effort") == "none")
