"""Measure streaming content rather than SSE metadata, without retaining prompts or secrets."""

from __future__ import annotations

import hashlib
import http.client
import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import quote

from .config import Target
from .providers import non_thinking_requested


class StreamError(Exception):
    """A deliberately value-free public failure code."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward an authenticated request to a redirected host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_parts(target: Target, text: str, limit: int, warm: bool) -> tuple[str, dict, dict]:
    key = target.key()
    if not target.ready():
        raise StreamError("missing_environment" if target.missing_env else "missing_key")
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    params = target.generation_params()
    url = target.url
    if target.protocol == "chat":
        headers["Authorization"] = "Bearer " + key
        body = {**params, "model": target.model, "messages": [{"role": "user", "content": text}],
                "stream": True, "stream_options": {"include_usage": True},
                target.output_limit_field: limit, "n": 1}
    elif target.protocol == "responses":
        headers["Authorization"] = "Bearer " + key
        body = {**params, "model": target.model, "input": text,
                "stream": True, "max_output_tokens": limit, "store": False}
    elif target.protocol == "anthropic":
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
        block = {"type": "text", "text": text}
        if warm and target.cache_policy == "anthropic_ephemeral":
            block["cache_control"] = {"type": "ephemeral"}
        body = {**params, "model": target.model, "messages": [{"role": "user", "content": [block]}],
                "stream": True, "max_tokens": limit}
    else:
        # Gemini keeps the credential in a header, never in the URL query string.
        headers["x-goog-api-key"] = key
        url = url.replace("{model}", quote(target.model, safe="")) + "?alt=sse"
        body = {"contents": [{"role": "user", "parts": [{"text": text}]}],
                "generationConfig": {**params.get("generationConfig", {}),
                                     "maxOutputTokens": limit, "candidateCount": 1}}
        for source, destination in (("temperature", "temperature"), ("top_p", "topP")):
            if source in params:
                body["generationConfig"][destination] = params[source]
    return url, headers, body


def events(response, clock, deadline: float):
    """Read multiline SSE data, ignoring comments and bounding a single event's size."""
    data, event_name, size = [], "", 0
    while True:
        if clock() >= deadline:
            raise StreamError("deadline_exceeded")
        line = response.readline(4 * 1024 * 1024 + 1)
        arrived = clock()
        if arrived > deadline:
            raise StreamError("deadline_exceeded")
        if not line:
            if data:
                yield event_name, "\n".join(data), arrived
            return
        size += len(line)
        if size > 4 * 1024 * 1024:
            raise StreamError("oversized_event")
        try:
            line = line.decode("utf-8-sig").rstrip("\r\n")
        except UnicodeDecodeError:
            raise StreamError("invalid_utf8") from None
        if not line:
            if data:
                yield event_name, "\n".join(data), arrived
            data, event_name, size = [], "", 0
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        elif line.startswith("event:"):
            event_name = line[6:].strip()


def number(value):
    return value if type(value) is int and value >= 0 else None


@dataclass
class Observation:
    """Keep server usage and client timing separate; missing usage never becomes an estimate."""

    start: float
    first_text: float | None = None
    last_text: float | None = None
    first_content: float | None = None
    last_content: float | None = None
    first_reasoning: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    cache_write_tokens: int | None = None
    finished: bool = False
    finish_reason: str | None = None
    model: str | None = None
    usage: dict = field(default_factory=dict)
    protocol: str = ""

    def content(self, value, now: float, reasoning: bool = False) -> None:
        if not isinstance(value, str) or not value:
            return
        if self.first_content is None:
            self.first_content = now
        self.last_content = now
        if reasoning:
            if self.first_reasoning is None:
                self.first_reasoning = now
        else:
            if self.first_text is None:
                self.first_text = now
            self.last_text = now

    def consume(self, protocol: str, event: str, item: dict, now: float) -> None:
        self.protocol = protocol
        if item.get("error") or item.get("type") == "error" or event == "error":
            raise StreamError("provider_stream_error")
        if protocol == "chat":
            self.model = item.get("model") or self.model
            for choice in item.get("choices", []):
                delta = choice.get("delta", {})
                self.content(delta.get("content"), now)
                self.content(delta.get("reasoning_content") or delta.get("reasoning"), now, True)
                if choice.get("finish_reason"):
                    self.finish_reason = choice["finish_reason"]
            usage = item.get("usage") or {}
            if usage:
                self.usage.update(usage)
                usage = self.usage
                self.input_tokens = number(usage.get("prompt_tokens"))
                self.output_tokens = number(usage.get("completion_tokens"))
                self.cached_tokens = number(usage.get("prompt_cache_hit_tokens"))
                if self.cached_tokens is None:
                    self.cached_tokens = number((usage.get("prompt_tokens_details") or {}).get("cached_tokens"))
                self.reasoning_tokens = number((usage.get("completion_tokens_details") or {}).get("reasoning_tokens"))
        elif protocol == "responses":
            kind = item.get("type", event)
            if kind == "response.output_text.delta":
                self.content(item.get("delta"), now)
            elif kind in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
                self.content(item.get("delta"), now, True)
            response = item.get("response") or {}
            self.model = response.get("model") or self.model
            if kind in {"response.completed", "response.incomplete", "response.failed"}:
                if kind == "response.failed" or response.get("error"):
                    raise StreamError("provider_response_failed")
                reason = (response.get("incomplete_details") or {}).get("reason")
                if kind == "response.incomplete" and reason != "max_output_tokens":
                    raise StreamError("provider_response_incomplete")
                self.finished = True
                self.finish_reason = reason or "completed"
                usage = response.get("usage") or {}
                self.input_tokens = number(usage.get("input_tokens"))
                self.output_tokens = number(usage.get("output_tokens"))
                self.cached_tokens = number((usage.get("input_tokens_details") or {}).get("cached_tokens"))
                self.reasoning_tokens = number((usage.get("output_tokens_details") or {}).get("reasoning_tokens"))
        elif protocol == "anthropic":
            kind = item.get("type", event)
            if kind == "message_start":
                message = item.get("message") or {}
                self.model = message.get("model") or self.model
                self.usage.update(message.get("usage") or {})
            elif kind == "content_block_start":
                block = item.get("content_block") or {}
                self.content(block.get("text"), now)
                self.content(block.get("thinking"), now, True)
            elif kind == "content_block_delta":
                delta = item.get("delta") or {}
                self.content(delta.get("text"), now)
                self.content(delta.get("thinking"), now, True)
            elif kind == "message_delta":
                # Cumulative counters replace prior values; adding them double-counts tokens.
                self.usage.update(item.get("usage") or {})
                self.finish_reason = (item.get("delta") or {}).get("stop_reason") or self.finish_reason
            elif kind == "message_stop":
                self.finished = True
            self.cached_tokens = number(self.usage.get("cache_read_input_tokens"))
            self.cache_write_tokens = number(self.usage.get("cache_creation_input_tokens"))
            base = number(self.usage.get("input_tokens"))
            if base is not None:
                self.input_tokens = base + (self.cached_tokens or 0) + (self.cache_write_tokens or 0)
            self.output_tokens = number(self.usage.get("output_tokens"))
        else:
            self.model = item.get("modelVersion") or self.model
            if (item.get("promptFeedback") or {}).get("blockReason"):
                raise StreamError("provider_content_blocked")
            for candidate in item.get("candidates", []):
                for part in (candidate.get("content") or {}).get("parts", []):
                    self.content(part.get("text"), now, bool(part.get("thought")))
                if candidate.get("finishReason"):
                    self.finished = True
                    self.finish_reason = candidate["finishReason"]
            usage = item.get("usageMetadata") or {}
            if usage:
                self.usage.update(usage)
                self.input_tokens = number(self.usage.get("promptTokenCount"))
                visible = number(self.usage.get("candidatesTokenCount"))
                self.reasoning_tokens = number(self.usage.get("thoughtsTokenCount"))
                if visible is not None:
                    # Gemini reports candidate text and hidden thought tokens separately.
                    self.output_tokens = visible + (self.reasoning_tokens or 0)
                self.cached_tokens = number(self.usage.get("cachedContentTokenCount"))

    def result(self, end: float, non_thinking: bool = False) -> dict:
        def offset(value):
            return value - self.start if value is not None else None

        span = (self.last_content - self.first_content
                if self.first_content is not None and self.last_content is not None else 0)
        text_span = (self.last_text - self.first_text
                     if self.first_text is not None and self.last_text is not None else 0)
        reasoning_count = self.reasoning_tokens
        # A requested non-thinking route can measure its reported output without inventing a usage field.
        requested_visible = non_thinking and self.first_reasoning is None and not reasoning_count
        visible = (self.output_tokens - reasoning_count
                   if self.output_tokens is not None and reasoning_count is not None else
                   self.output_tokens if requested_visible else None)
        # Hidden reasoning has no client arrival clock. Do not invent a generation-only TPS for it.
        observable = (requested_visible or reasoning_count == 0 or
                      (self.protocol in {"chat", "anthropic"} and self.first_reasoning is not None))
        total_tps = ((self.output_tokens - 1) / span if span > 0 and self.output_tokens
                     and observable else None)
        visible_tps = ((visible - 1) / text_span if text_span > 0 and visible and visible > 1 else None)
        if self.protocol == "gemini" and self.reasoning_tokens is None:
            # candidate counts are observable text, even when thought counts were not published.
            visible_tps = ((self.output_tokens - 1) / text_span
                           if text_span > 0 and self.output_tokens and self.output_tokens > 1 else None)
        return {
            "ttft_seconds": offset(self.first_text),
            "first_content_seconds": offset(self.first_content),
            "first_reasoning_seconds": offset(self.first_reasoning),
            "elapsed_seconds": end - self.start, "generation_seconds": span or None,
            "output_tps": total_tps, "visible_output_tps": visible_tps,
            "tps_basis": ("requested_non_thinking" if requested_visible else
                          "streamed_output" if observable else "unobservable_reasoning"),
            "end_to_end_tps": (self.output_tokens / (end - self.start)
                               if self.output_tokens is not None and end > self.start else None),
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens, "cached_tokens": self.cached_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_observed": ("unknown" if self.cached_tokens is None else
                               "hit" if self.cached_tokens > 0 else "miss"),
            "cache_hit_ratio": (self.cached_tokens / self.input_tokens
                                if self.cached_tokens is not None and self.input_tokens else None),
            "response_model": self.model, "finish_reason": self.finish_reason,
            "has_text": self.first_text is not None,
        }


def measure(target: Target, text: str, limit: int, warm: bool, timeout: float = 120,
            *, opener=None, clock=time.perf_counter) -> dict:
    """Make exactly one paid attempt; all failures have fixed, credential-free diagnostics."""
    start = clock()
    observation = Observation(start)
    result = {"status": "error", "error": None,
              "prompt_sha256": hashlib.sha256(text.encode()).hexdigest(),
              "prompt_characters": len(text)}
    try:
        url, headers, body = request_parts(target, text, limit, warm)
        request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        open_request = opener or urllib.request.build_opener(NoRedirect()).open
        with open_request(request, timeout=timeout) as response:
            result["headers_seconds"] = clock() - start
            if "text/event-stream" not in response.headers.get("Content-Type", "").lower():
                raise StreamError("expected_event_stream")
            for name, data, now in events(response, clock, start + timeout):
                if data == "[DONE]":
                    if target.protocol == "chat":
                        observation.finished = True
                    break
                try:
                    item = json.loads(data)
                except ValueError:
                    raise StreamError("invalid_stream_json") from None
                if not isinstance(item, dict):
                    raise StreamError("invalid_stream_shape")
                observation.consume(target.protocol, name, item, now)
                if observation.finished and target.protocol in {"responses", "anthropic"}:
                    break
            if not observation.finished:
                raise StreamError("stream_interrupted")
            if observation.finish_reason in {"content_filter", "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"}:
                raise StreamError("provider_content_blocked")
            result["status"] = "ok"
    except urllib.error.HTTPError as exc:
        result["error"] = f"http_{exc.code}"
        exc.close()
    except (socket.timeout, TimeoutError):
        result["error"] = "timeout"
    except urllib.error.URLError:
        result["error"] = "network_error"
    except StreamError as exc:
        result["error"] = str(exc)
    except (ValueError, TypeError, KeyError, AttributeError):
        result["error"] = "invalid_stream_shape"
    except OSError:
        result["error"] = "network_error"
    except http.client.HTTPException:
        result["error"] = "stream_interrupted"
    result.update(observation.result(clock(), non_thinking_requested(target.generation_params())))
    return result
