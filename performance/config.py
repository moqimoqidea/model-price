"""Accept explicit endpoint identities while keeping all credentials in the environment."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .providers import generation_parameters

PROTOCOLS = {"chat", "responses", "anthropic", "gemini"}
PARAMETERS = {
    "temperature", "top_p", "seed", "thinking", "reasoning", "reasoning_effort",
    "output_config", "enable_thinking", "generationConfig",
}
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def positive(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def validate_url(value: str) -> None:
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or any(c.isspace() for c in value)):
        raise ValueError("endpoint must be HTTPS without credentials, query, or fragment")


@dataclass(frozen=True)
class Target:
    """An individual provider/protocol/region/model route, never a family-name guess."""

    id: str
    protocol: str
    url: str
    model: str
    api_key_env: str
    provider: str = "custom"
    thinking_mode: str = "custom"
    region: str = "unspecified"
    revision: str = "unverified"
    context_window: int = 1048576
    max_output_tokens: int = 131072
    params: dict = field(default_factory=dict)
    cache_policy: str = "implicit"
    output_limit_field: str = "max_tokens"
    missing_env: tuple = ()

    def key(self) -> str | None:
        return os.environ.get(self.api_key_env) or None

    def ready(self) -> bool:
        return bool(self.key()) and not self.missing_env

    def generation_params(self) -> dict:
        return generation_parameters(self.provider, self.protocol, self.thinking_mode, self.params)

    def public(self) -> dict:
        # Deliberately omit URLs (which can contain workspace IDs) and parameter values.
        import hashlib
        digest = hashlib.sha256(json.dumps(self.generation_params(), sort_keys=True).encode()).hexdigest()
        return {
            "id": self.id, "protocol": self.protocol, "model": self.model,
            "provider": self.provider, "thinking_mode": self.thinking_mode,
            "region": self.region, "revision": self.revision,
            "api_key_env": self.api_key_env, "key_present": bool(self.key()),
            "missing_environment": list(self.missing_env), "ready": self.ready(),
            "context_window": self.context_window, "max_output_tokens": self.max_output_tokens,
            "cache_policy": self.cache_policy, "params_sha256": digest,
        }


def load_targets(path: Path, selected: list[str] | None = None) -> list[Target]:
    def reject_constant(value):
        raise ValueError("non-finite JSON number")

    document = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    if not isinstance(document, dict) or set(document) != {"targets"} or not isinstance(document["targets"], list):
        raise ValueError("config must contain a targets array only")
    targets, ids = [], set()
    allowed = (set(Target.__dataclass_fields__) - {"missing_env"}) | {"url_env", "model_env"}
    for raw in document["targets"]:
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise ValueError("unknown target field; credentials belong in environment variables")
        item = dict(raw)
        for field_name in ("url", "model"):
            env = item.pop(field_name + "_env", None)
            if env is not None:
                if not isinstance(env, str) or not ENV_NAME.fullmatch(env):
                    raise ValueError("invalid environment variable name")
                item[field_name] = os.environ.get(env) or item.get(field_name, "")
        missing_env = []
        # Aliyun workspace routing is explicit; a missing workspace never falls back to a legacy host.
        template = item.get("url", "")
        if isinstance(template, str) and "${DASHSCOPE_WORKSPACE_ID}" in template:
            workspace = os.environ.get("DASHSCOPE_WORKSPACE_ID", "")
            if workspace and not re.fullmatch(r"[A-Za-z0-9_-]+", workspace):
                raise ValueError("invalid DASHSCOPE_WORKSPACE_ID hostname component")
            if not workspace:
                missing_env.append("DASHSCOPE_WORKSPACE_ID")
            item["url"] = template.replace("${DASHSCOPE_WORKSPACE_ID}", workspace or "missing-workspace")
        try:
            target = Target(**item)
        except TypeError:
            raise ValueError("target is missing required fields") from None
        if not isinstance(target.id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", target.id):
            raise ValueError("target id must use letters, digits, underscores, or hyphens")
        if target.id in ids:
            raise ValueError("duplicate target id")
        ids.add(target.id)
        if selected and target.id not in selected:
            continue
        object.__setattr__(target, "missing_env", tuple(missing_env))
        if target.protocol not in PROTOCOLS:
            raise ValueError("protocol must be chat, responses, anthropic, or gemini")
        if not isinstance(target.url, str):
            raise ValueError("url must be a string")
        validate_url(target.url)
        if not isinstance(target.model, str) or not target.model or any(c.isspace() for c in target.model):
            raise ValueError("model must be a literal nonempty API id")
        if not isinstance(target.api_key_env, str) or not ENV_NAME.fullmatch(target.api_key_env):
            raise ValueError("invalid api_key_env")
        for name in ("context_window", "max_output_tokens"):
            positive(getattr(target, name), name)
        if not isinstance(target.params, dict) or set(target.params) - PARAMETERS:
            raise ValueError("unsupported generation parameter; do not put keys or headers in config")
        nested_fields = {
            "thinking": {"type", "budget_tokens"}, "reasoning": {"effort", "summary"},
            "output_config": {"effort"},
        }
        for name, fields in nested_fields.items():
            if name in target.params and (not isinstance(target.params[name], dict)
                                          or set(target.params[name]) - fields):
                raise ValueError("unsupported nested generation parameter")
        if target.protocol == "gemini" and set(target.params) - {"generationConfig", "temperature", "top_p"}:
            raise ValueError("Gemini parameters must use generationConfig")
        if target.protocol != "gemini" and "generationConfig" in target.params:
            raise ValueError("generationConfig requires Gemini")
        if target.thinking_mode not in {"on", "off", "provider-default", "custom"}:
            raise ValueError("invalid thinking_mode")
        target.generation_params()
        if target.cache_policy not in {"implicit", "anthropic_ephemeral"}:
            raise ValueError("cache_policy must be implicit or anthropic_ephemeral")
        if target.cache_policy == "anthropic_ephemeral" and target.protocol != "anthropic":
            raise ValueError("anthropic_ephemeral requires the anthropic protocol")
        if target.output_limit_field not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("invalid chat output_limit_field")
        generation = target.params.get("generationConfig", {})
        if not isinstance(generation, dict) or set(generation) - {"temperature", "topP", "topK", "thinkingConfig"}:
            raise ValueError("unsupported Gemini generationConfig field")
        thinking_config = generation.get("thinkingConfig", {})
        if not isinstance(thinking_config, dict) or set(thinking_config) - {"thinkingBudget", "thinkingLevel", "includeThoughts"}:
            raise ValueError("unsupported Gemini thinkingConfig field")
        for name in ("region", "revision"):
            if not isinstance(getattr(target, name), str):
                raise ValueError(f"{name} must be a string")
        targets.append(target)
    if selected and set(selected) - ids:
        raise ValueError("unknown target selection")
    if not targets:
        raise ValueError("no targets selected")
    return targets


def token_count(value: str) -> int:
    match = re.fullmatch(r"(\d+)([kKmM]?)", value.strip())
    if not match:
        raise ValueError("token sizes must be integers, optionally suffixed K or M (decimal)")
    return positive(int(match[1]) * {"": 1, "k": 1000, "m": 1000000}[match[2].lower()], "token size")
