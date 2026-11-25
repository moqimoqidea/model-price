"""Validate paid-call boundaries and stream timing with synthetic clocks and no network."""

from __future__ import annotations

import io
import http.client
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from performance.config import Target, load_targets, token_count
from performance.domestic import PROFILES, build_target, fuzzy_models, parse_route
from performance.prompts import CALIBRATION_TOKENS, DEFAULT_CHARS_PER_TOKEN, corpus, prompt
from performance.protocols import (NoRedirect, Observation, classify_http, classify_network,
                                   measure, request_parts)
from performance.providers import generation_parameters
from performance.runner import aggregate, display, execute, plan


def target(protocol="chat", **kwargs):
    fields = dict(id="fixture", protocol=protocol, url="https://example.test/v1/chat/completions",
                  model="fixture-model", api_key_env="FIXTURE_API_KEY",
                  params={"thinking": {"type": "disabled"}} if protocol == "chat" else {})
    fields.update(kwargs)
    return Target(**fields)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Response(io.BytesIO):
    headers = {"Content-Type": "text/event-stream; charset=utf-8"}

    def __init__(self, lines, clock):
        super().__init__(lines.encode())
        self.clock = clock

    def readline(self, limit=-1):
        self.clock.now += 0.5
        return super().readline(limit)


def stream(items):
    return "".join("data: " + (item if isinstance(item, str) else json.dumps(item)) + "\n\n" for item in items)


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.environment = mock.patch.dict(os.environ, {"FIXTURE_API_KEY": "test-only-not-a-key"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def measurement(self, protocol, items, **kwargs):
        clock = Clock()
        response = Response(stream(items), clock)
        return measure(target(protocol, **kwargs), "Hello", 16, False, 120,
                       opener=lambda *args, **kwargs: response, clock=clock)

    def test_chat_skips_role_and_usage_for_ttft(self):
        result = self.measurement("chat", [
            {"choices": [{"delta": {"role": "assistant"}}], "system_fingerprint": "fp-fixture"},
            {"choices": [{"delta": {"reasoning_content": "reason"}}]},
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " world"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 10,
                                       "prompt_cache_hit_tokens": 12,
                                       "completion_tokens_details": {"reasoning_tokens": 4}}},
            "[DONE]",
        ])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["ttft_seconds"], 3)
        self.assertEqual(result["first_content_seconds"], 2)
        self.assertEqual(result["output_tps"], 4.5)
        self.assertEqual(result["visible_output_tps"], 5)
        self.assertEqual(result["cache_hit_ratio"], 0.6)
        self.assertEqual(result["system_fingerprint"], "fp-fixture")

    def test_nonthinking_without_details_can_measure_reported_output(self):
        result = self.measurement("chat", [
            {"choices": [{"delta": {"content": "one"}}]},
            {"choices": [{"delta": {"content": "two"}}]},
            {"usage": {"completion_tokens": 5}}, "[DONE]",
        ])
        self.assertEqual(result["output_tps"], 4)
        self.assertIsNone(result["reasoning_tokens"])
        self.assertEqual(result["tps_basis"], "requested_non_thinking")

    def test_missing_usage_is_unknown_not_character_count(self):
        result = self.measurement("chat", [{"choices": [{"delta": {"content": "Hello"}}]}, "[DONE]"])
        self.assertIsNone(result["output_tps"])
        self.assertIsNone(result["output_tokens"])
        self.assertEqual(result["cache_observed"], "unknown")

    def test_interrupted_and_error_streams_are_failures(self):
        for items, expected in [
            ([{"choices": [{"delta": {"content": "partial"}}]}], "stream_interrupted"),
            ([{"error": {"message": "test-only-not-a-key"}}], "provider_stream_error"),
            (["{invalid"], "invalid_stream_json"),
        ]:
            with self.subTest(expected=expected):
                result = self.measurement("chat", items)
                self.assertEqual(result["error"], expected)
                self.assertNotIn("test-only-not-a-key", json.dumps(result))

    def test_anthropic_cumulative_usage_and_cache_input(self):
        result = self.measurement("anthropic", [
            {"type": "message_start", "message": {"model": "fixture", "usage": {
                "input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 20,
                "cache_creation_input_tokens": 30}}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}},
            {"type": "message_delta", "usage": {"output_tokens": 5}},
            {"type": "message_delta", "usage": {"output_tokens": 8}, "delta": {"stop_reason": "end_turn"}},
            {"type": "message_stop"},
        ])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["input_tokens"], 60)
        self.assertEqual(result["output_tokens"], 8)
        self.assertEqual(result["cache_write_tokens"], 30)

    def test_responses_hidden_reasoning_does_not_invent_generation_tps(self):
        result = self.measurement("responses", [
            {"type": "response.created", "response": {}},
            {"type": "response.reasoning_summary_text.delta", "delta": "summary"},
            {"type": "response.output_text.delta", "delta": "Hello"},
            {"type": "response.output_text.delta", "delta": "world"},
            {"type": "response.completed", "response": {"model": "fixture", "usage": {
                "input_tokens": 50, "output_tokens": 20, "input_tokens_details": {"cached_tokens": 40},
                "output_tokens_details": {"reasoning_tokens": 10}}}},
        ])
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["output_tps"])
        self.assertEqual(result["visible_output_tps"], 9)
        self.assertEqual(result["cache_observed"], "hit")

    def test_responses_output_limit_is_valid_but_failure_is_not(self):
        limited = self.measurement("responses", [{"type": "response.incomplete", "response": {
            "incomplete_details": {"reason": "max_output_tokens"}, "usage": {"output_tokens": 16}}}])
        self.assertEqual(limited["status"], "ok")
        failed = self.measurement("responses", [{"type": "response.failed", "response": {}}])
        self.assertEqual(failed["status"], "error")

    def test_gemini_usage_and_thoughts_are_not_double_counted(self):
        result = self.measurement("gemini", [
            {"candidates": [{"content": {"parts": [{"text": "hello"}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "world"}]}, "finishReason": "STOP"}],
             "usageMetadata": {"promptTokenCount": 50, "cachedContentTokenCount": 30,
                               "candidatesTokenCount": 8, "thoughtsTokenCount": 12}},
        ])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_tokens"], 20)
        self.assertEqual(result["visible_output_tps"], 7)
        self.assertIsNone(result["output_tps"])

    def test_auth_headers_and_protocol_limit_fields(self):
        for protocol in ("chat", "responses", "anthropic", "gemini"):
            route = target(protocol)
            url, headers, body = request_parts(route, "Hello", 16, True)
            self.assertNotIn("test-only-not-a-key", url)
            self.assertNotIn("test-only-not-a-key", json.dumps(body))
            if protocol == "anthropic":
                self.assertIn("x-api-key", headers)
                self.assertEqual(body["max_tokens"], 16)
            elif protocol == "gemini":
                self.assertIn("x-goog-api-key", headers)
                self.assertEqual(body["generationConfig"]["maxOutputTokens"], 16)
            else:
                self.assertIn("Authorization", headers)
        explicit = target("anthropic", cache_policy="anthropic_ephemeral")
        self.assertIn("cache_control", request_parts(explicit, "Hello", 16, True)[2]["messages"][0]["content"][0])
        self.assertNotIn("cache_control", request_parts(explicit, "Hello", 16, False)[2]["messages"][0]["content"][0])

    def test_redirect_does_not_forward_secret(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 307, "", {}, "https://other.test"))

    def test_http_error_never_echoes_body_url_or_reason(self):
        def opener(*args, **kwargs):
            raise urllib.error.HTTPError("https://test-only-not-a-key.test", 401,
                                         "test-only-not-a-key", {}, io.BytesIO(b"test-only-not-a-key"))
        result = measure(target(), "Hello", 16, False, opener=opener)
        self.assertEqual(result["error"], "http_401")
        self.assertNotIn("test-only-not-a-key", json.dumps(result))

    def test_broken_http_stream_is_a_redacted_failure(self):
        def opener(*args, **kwargs):
            raise http.client.IncompleteRead(b"test-only-not-a-key")
        result = measure(target(), "Hello", 16, False, opener=opener)
        self.assertEqual(result["error"], "stream_interrupted")
        self.assertNotIn("test-only-not-a-key", json.dumps(result))

    def test_provider_thinking_is_not_a_universal_parameter(self):
        self.assertEqual(generation_parameters("aliyun", "chat", "on", {}), {"enable_thinking": True})
        self.assertEqual(generation_parameters("deepseek", "chat", "off", {}), {"thinking": {"type": "disabled"}})
        self.assertEqual(generation_parameters("deepseek", "responses", "off", {}), {"reasoning": {"effort": "none"}})
        with self.assertRaises(ValueError):
            generation_parameters("unknown", "chat", "on", {})
        with self.assertRaises(ValueError):
            generation_parameters("aliyun", "chat", "on", {"thinking": {"type": "enabled"}})

    def test_workspace_endpoint_resolves_without_recording_workspace(self):
        with mock.patch.dict(os.environ, {"DASHSCOPE_WORKSPACE_ID": "fixture-workspace"}):
            routes = load_targets(ROOT / "performance/targets.example.json", ["aliyun"])
            self.assertIn("fixture-workspace.cn-beijing.maas.aliyuncs.com", routes[0].url)
            self.assertNotIn("fixture-workspace", json.dumps(routes[0].public()))
            self.assertEqual(routes[0].generation_params(), {"enable_thinking": False})

    def test_missing_workspace_is_not_sent_to_legacy_host(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            route = load_targets(ROOT / "performance/targets.example.json", ["aliyun"])[0]
            self.assertIn("DASHSCOPE_WORKSPACE_ID", route.missing_env)
            self.assertFalse(route.ready())
            changed = replace(route, thinking_mode="on")
            self.assertEqual(changed.missing_env, route.missing_env)
            self.assertFalse(changed.ready())

    def test_every_missing_provider_key_is_named_and_no_paid_probe_runs(self):
        expected = {
            "deepseek": {"DEEPSEEK_API_KEY"},
            "aliyun": {"DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE_ID"},
            "tencent": {"TENCENT_API_KEY"},
            "volcengine": {"ARK_API_KEY"},
        }
        with mock.patch.dict(os.environ, {}, clear=True), tempfile.TemporaryDirectory() as folder:
            routes = load_targets(ROOT / "performance/targets.example.json")
            for route in routes:
                self.assertEqual(set(route.missing_environment()), expected[route.id])
                self.assertEqual(set(route.public()["missing_environment"]), expected[route.id])
            probe = mock.Mock(side_effect=AssertionError("must not send requests without environment"))
            result = execute(routes, plan(routes, [100], ["cold"], 3, 16, 500000),
                             Path(folder) / "result.json", probe=probe)
        probe.assert_not_called()
        self.assertEqual(result["samples"], [])
        for row in result["preflight"]:
            self.assertEqual(set(row["missing_environment"]), expected[row["target"]])
            for name in expected[row["target"]]:
                self.assertIn(name, display(result))

    def test_empty_and_whitespace_exports_are_missing(self):
        with mock.patch.dict(os.environ, {"FIXTURE_API_KEY": " \n\t"}):
            route = target()
            self.assertFalse(route.ready())
            self.assertEqual(route.missing_environment(), ("FIXTURE_API_KEY",))
        with mock.patch.dict(os.environ, {"DASHSCOPE_API_KEY": "", "DASHSCOPE_WORKSPACE_ID": "  "}, clear=True):
            route = load_targets(ROOT / "performance/targets.example.json", ["aliyun"])[0]
            self.assertEqual(set(route.missing_environment()), {"DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE_ID"})

    def test_ark_base_url_supports_base_and_full_endpoint_without_persisting_either(self):
        for base in ("https://fixture.example/api/v3", "https://fixture.example/api/v3/",
                     "https://fixture.example/api/v3/chat/completions"):
            with self.subTest(base=base), mock.patch.dict(os.environ, {"ARK_BASE_URL": base}):
                route = load_targets(ROOT / "performance/targets.example.json", ["volcengine"])[0]
                self.assertEqual(route.url, "https://fixture.example/api/v3/chat/completions")
                self.assertNotIn("fixture.example", json.dumps(route.public()))

    def test_ark_base_url_is_optional_with_official_default(self):
        with mock.patch.dict(os.environ, {"ARK_API_KEY": "fixture-key"}, clear=True):
            route = load_targets(ROOT / "performance/targets.example.json", ["volcengine"])[0]
            self.assertTrue(route.ready())
            self.assertEqual(route.url, "https://ark.cn-beijing.volces.com/api/v3/chat/completions")

    def test_selected_target_ignores_unrelated_environment_values(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fixture-key", "ARK_BASE_URL": "invalid",
                                         "DASHSCOPE_WORKSPACE_ID": "invalid workspace"}, clear=True):
            route = load_targets(ROOT / "performance/targets.example.json", ["deepseek"])[0]
        self.assertEqual(route.id, "deepseek")

    def test_full_url_override_takes_precedence_over_base_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"targets": [{"id": "fixture", "protocol": "chat",
                "url_env": "FIXTURE_URL", "base_url_env": "FIXTURE_BASE", "model": "fixture",
                "api_key_env": "FIXTURE_API_KEY"}]}))
            with mock.patch.dict(os.environ, {"FIXTURE_URL": "https://full.example/v1/chat/completions",
                                             "FIXTURE_BASE": "https://base.example/v1"}):
                route = load_targets(path)[0]
                self.assertEqual(route.url, "https://full.example/v1/chat/completions")
            with mock.patch.dict(os.environ, {"FIXTURE_BASE": "https://base.example/v1"}, clear=True):
                route = load_targets(path)[0]
                self.assertEqual(route.url, "https://base.example/v1/chat/completions")
                self.assertEqual(route.missing_env, ())

    def test_aliyun_full_endpoint_does_not_require_workspace(self):
        with mock.patch.dict(os.environ, {"DASHSCOPE_API_KEY": "fixture-key",
                                         "ALIYUN_BENCHMARK_URL": "https://fixture.example/chat/completions"}, clear=True):
            route = load_targets(ROOT / "performance/targets.example.json", ["aliyun"])[0]
            self.assertTrue(route.ready())
            self.assertEqual(route.missing_environment(), ())

    def test_env_command_is_offline_and_names_missing_exports(self):
        from performance.benchmark import main
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("urllib.request.build_opener") as network, \
                mock.patch("performance.benchmark.execute") as paid, \
                mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            status = main(["env"])
        self.assertEqual(status, 1)
        for name in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE_ID", "TENCENT_API_KEY", "ARK_API_KEY"):
            self.assertIn(name, output.getvalue())
        network.assert_not_called()
        paid.assert_not_called()

    def test_env_command_never_exposes_loaded_values(self):
        from performance.benchmark import main
        values = {name: "fixture-only-hidden-value" for name in
                  ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE_ID", "TENCENT_API_KEY", "ARK_API_KEY")}
        with mock.patch.dict(os.environ, values, clear=True), mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            status = main(["env"])
        self.assertEqual(status, 0)
        self.assertNotIn("fixture-only-hidden-value", output.getvalue())

    def test_config_rejects_credentials_and_invalid_endpoints(self):
        for change in ({"api_key": "not-real"}, {"url": "http://example.test"},
                       {"url": "https://example.test?key=not-real"},
                       {"params": {"thinking": {"api_key": "not-real"}}}):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "config.json"
                entry = dict(id="fixture", protocol="chat", url="https://example.test",
                             model="fixture", api_key_env="FIXTURE_API_KEY")
                path.write_text(json.dumps({"targets": [{**entry, **change}]}))
                with self.assertRaises(ValueError):
                    load_targets(path)

    def test_cold_nonce_changes_first_prefix_but_warm_is_identical(self):
        content = corpus(10000)
        self.assertEqual(content, corpus(10000))
        self.assertNotEqual(prompt(content, "a")[:80], prompt(content, "b")[:80])
        self.assertEqual(prompt(content, "a"), prompt(content, "a"))
        self.assertLess(abs(len(prompt(content, "x" * 64)) / DEFAULT_CHARS_PER_TOKEN - 10000), 100)

    def test_plan_includes_paid_warmup_and_rejects_large_matrix(self):
        report = plan([target()], [10000], ["cold", "warm"], 3, 256, 500000)
        self.assertEqual(report["benchmark_requests"], 7)
        self.assertEqual(report["input_tokens_estimate"], 70000)
        large = plan([target()], [10000, 50000, 500000, 800000], ["cold", "warm"], 3, 128000, 500000)
        self.assertGreater(len(large["problems"]), 0)
        self.assertEqual(token_count("96K"), 96000)
        self.assertEqual(token_count("131072"), 131072)

    def test_runner_preflights_once_warmups_excluded_and_checkpoint_private(self):
        calls = []
        def probe(route, text, limit, warm, timeout):
            calls.append((text, limit, warm))
            return {"status": "ok", "error": None, "input_tokens": 100, "output_tokens": 8,
                    "ttft_seconds": 0.2, "output_tps": 30, "cache_observed": "hit" if warm else "miss"}
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "result.json"
            report = execute([target()], plan([target()], [128], ["cold", "warm"], 3, 32, 500000, calibrate=False),
                             output, probe=probe, sleep=lambda _: None, run_id="fixture")
            self.assertEqual(len(calls), 8)
            self.assertEqual(calls[0], ("Hello", 16, False))
            self.assertEqual(calls[4][1], 16)
            self.assertEqual(len({text for text, _, warm in calls[1:] if not warm}), 3)
            self.assertEqual(len({text for text, _, warm in calls[1:] if warm}), 1)
            self.assertEqual(sum(row["attempts"] for row in report["summary"]), 6)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("test-only-not-a-key", output.read_text())
            with self.assertRaises(FileExistsError):
                execute([target()], report["plan"], output, probe=probe)

    def test_failed_preflight_never_sends_large_prompt(self):
        calls = []
        def probe(route, text, *args):
            calls.append(text)
            return {"status": "error", "error": "http_401"}
        with tempfile.TemporaryDirectory() as folder:
            result = execute([target()], plan([target()], [800000], ["cold"], 3, 16, 10000000),
                             Path(folder) / "result.json", probe=probe, sleep=lambda _: None)
        self.assertEqual(calls, ["Hello"])
        self.assertEqual(result["samples"], [])
        self.assertEqual(result["state"], "partial")

    def test_usage_over_estimate_stops_next_paid_request(self):
        calls = []
        def probe(route, text, *args):
            calls.append(text)
            return {"status": "ok", "input_tokens": 2 if text == "Hello" else 500,
                    "output_tokens": 3, "cache_observed": "miss"}
        with tempfile.TemporaryDirectory() as folder:
            result = execute([target()], plan([target()], [100], ["cold"], 3, 16, 500, calibrate=False),
                             Path(folder) / "result.json", probe=probe, sleep=lambda _: None)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["state"], "budget_stopped")
        self.assertEqual(result["samples"][0]["input_target_within_10_percent"], False)

    def test_limits_stop_default_large_run_before_any_network(self):
        from performance.benchmark import main
        with mock.patch("performance.benchmark.execute") as paid, mock.patch("sys.stderr", new_callable=io.StringIO):
            status = main(["run", "--target", "deepseek", "--input-tokens", "800K"])
        self.assertEqual(status, 2)
        paid.assert_not_called()

    def test_plan_never_opens_a_connection(self):
        from performance.benchmark import main
        with mock.patch("urllib.request.build_opener") as network, mock.patch("sys.stdout", new_callable=io.StringIO):
            status = main(["plan", "--target", "deepseek"])
        self.assertEqual(status, 0)
        network.assert_not_called()

    def test_interruption_retains_inflight_attempt(self):
        calls = []
        def probe(*args):
            calls.append(True)
            if len(calls) > 1:
                raise KeyboardInterrupt
            return {"status": "ok", "input_tokens": 2, "output_tokens": 3}
        with tempfile.TemporaryDirectory() as folder:
            result = execute([target()], plan([target()], [100], ["cold"], 3, 16, 100000, calibrate=False),
                             Path(folder) / "result.json", probe=probe, sleep=lambda _: None)
        self.assertEqual(result["state"], "interrupted")
        self.assertEqual(result["samples"][0]["error"], "interrupted")

    def test_aggregation_separates_hits_and_misses_and_excludes_failed_timing(self):
        base = dict(target="fixture", input_target=100, mode="warm", phase="measurement", status="ok")
        rows = [dict(base, cache_observed="hit", ttft_seconds=1),
                dict(base, cache_observed="miss", ttft_seconds=2),
                dict(base, cache_observed="hit", ttft_seconds=999, status="error")]
        summary = aggregate(rows)
        self.assertEqual(len(summary), 2)
        self.assertEqual(summary[0]["metrics"]["ttft_seconds"]["mean"], 1)
        self.assertEqual(summary[0]["failures"], 1)

    def test_corpus_density_is_a_parameter_not_a_fixed_four_characters(self):
        self.assertEqual(CALIBRATION_TOKENS, 2000)
        self.assertLess(len(corpus(1000, 4.0)), len(corpus(1000, DEFAULT_CHARS_PER_TOKEN)))

    def test_plan_accounts_for_one_density_probe_per_route(self):
        probed = plan([target()], [10000], ["cold"], 3, 16, 500000)
        bare = plan([target()], [10000], ["cold"], 3, 16, 500000, calibrate=False)
        self.assertEqual(probed["calibration_requests"], 1)
        self.assertEqual(bare["calibration_requests"], 0)
        self.assertGreater(probed["total_tokens_estimate"], bare["total_tokens_estimate"])

    def test_calibration_probe_replaces_the_constant_with_the_route_density(self):
        calls = []

        def probe(route, text, limit, warm, timeout):
            calls.append((len(text), limit))
            return {"status": "ok", "error": None, "input_tokens": round(len(text) / 5.8),
                    "output_tokens": 1, "ttft_seconds": 0.1, "output_tps": 1}

        with tempfile.TemporaryDirectory() as folder:
            report = execute([target()], plan([target()], [100], ["cold"], 1, 4, 500000),
                             Path(folder) / "result.json", probe=probe, sleep=lambda _: None, run_id="fixture")
        self.assertEqual(calls[0], (5, 16))
        self.assertEqual(calls[1][1], 1)
        density = report["calibration"]["fixture"]["chars_per_token"]
        self.assertAlmostEqual(density, 5.8, places=1)
        self.assertTrue(report["samples"][0]["input_target_within_10_percent"])
        self.assertEqual(report["samples"][0]["chars_per_token"], density)

    def test_display_names_the_requested_model_and_any_answered_alias(self):
        rows = [
            dict(target="volcengine", input_target=100, phase="measurement", status="ok",
                 model="deepseek-v4-1-flash-261017", response_model="deepseek-v4-1-flash-261017",
                 mode="cold", cache_observed="miss", ttft_seconds=1, output_tps=100),
            dict(target="aliyun", input_target=100, phase="measurement", status="ok",
                 model="deepseek-v4.1-flash", response_model="deepseek-v4-flash",
                 mode="cold", cache_observed="miss", ttft_seconds=1, output_tps=100),
        ]
        text = display({"state": "completed", "preflight": [], "summary": aggregate(rows)})
        self.assertIn("model=deepseek-v4-1-flash-261017", text)
        self.assertIn("model=deepseek-v4.1-flash（响应 deepseek-v4-flash）", text)

    def test_display_reports_deviation_and_spread(self):
        base = dict(target="fixture", model="fixture-model", input_target=10000,
                    phase="measurement", status="ok", mode="cold", cache_observed="miss")
        rows = [dict(base, input_tokens=6870, ttft_seconds=value, output_tps=100,
                     headers_seconds=0.2) for value in (1.0, 1.2)]
        text = display({"state": "completed", "preflight": [], "summary": aggregate(rows)})
        self.assertIn("偏离 -31%", text)
        self.assertIn("±", text)
        self.assertIn("headers=0.200s", text)

    def test_domestic_profiles_use_the_agreed_environment_names(self):
        expected = {
            "deepseek": "DEEPSEEK_API_KEY", "volcengine": "ARK_API_KEY",
            "aliyun": "DASHSCOPE_API_KEY", "tencent": "TENCENT_API_KEY",
            "moonshot": "MOONSHOT_API_KEY", "zhipu": "ZHIPU_API_KEY",
            "minimax": "MINIMAX_API_KEY", "baidu": "BAIDU_LLM_API_KEY",
        }
        for provider, variable in expected.items():
            self.assertEqual(PROFILES[provider].api_key_env, variable)
            self.assertNotIn("api_key", json.dumps(PROFILES[provider].url).lower())

    def test_route_builds_a_literal_vendor_model_and_keeps_the_endpoint_shape(self):
        with mock.patch.dict(os.environ, {"MOONSHOT_API_KEY": "fixture"}), \
                mock.patch.dict(os.environ, {"DASHSCOPE_API_KEY": "fixture",
                                             "DASHSCOPE_WORKSPACE_ID": "fixture-ws"}):
            moonshot = build_target("moonshot", "kimi-k3.1-pro-highspeed")
            self.assertEqual(moonshot.id, "moonshot-kimi-k3.1-pro-highspeed")
            self.assertEqual(moonshot.model, "kimi-k3.1-pro-highspeed")
            self.assertEqual(moonshot.url, "https://api.moonshot.cn/v1/chat/completions")
            self.assertEqual(moonshot.output_limit_field, "max_completion_tokens")
            self.assertTrue(moonshot.ready())
            bailian = build_target("bailian", "kimi-k3.1-pro-prime")
            self.assertEqual(bailian.id, "aliyun-kimi-k3.1-pro-prime")
            self.assertIn("fixture-ws.cn-beijing.maas.aliyuncs.com", bailian.url)
            self.assertNotIn("fixture-ws", json.dumps(bailian.public()))

    def test_route_parsing_and_unknown_provider_are_reported(self):
        self.assertEqual(parse_route("moonshot=kimi-k3"), ("moonshot", "kimi-k3"))
        self.assertEqual(parse_route("kimi=kimi-k3.1-pro"), ("kimi", "kimi-k3.1-pro"))
        with self.assertRaises(ValueError):
            parse_route("moonshot")
        with self.assertRaises(ValueError):
            build_target("nonexistent", "model")

    def test_base_url_override_replaces_the_profile_endpoint(self):
        with mock.patch.dict(os.environ, {"MOONSHOT_API_KEY": "fixture"}):
            route = build_target("moonshot", "kimi-k3", base_url="https://gateway.test/v1/chat/completions")
            self.assertEqual(route.url, "https://gateway.test/v1/chat/completions")
            with self.assertRaises(ValueError):
                build_target("moonshot", "kimi-k3", base_url="http://insecure.test/v1/chat/completions")

    def test_a_base_url_export_is_completed_once_and_a_full_endpoint_is_not_doubled(self):
        with mock.patch.dict(os.environ, {"ARK_API_KEY": "fixture",
                                          "ARK_BASE_URL": "https://ark.fixture.test/api/v3"}):
            self.assertEqual(build_target("volcengine", "model-x").url,
                             "https://ark.fixture.test/api/v3/chat/completions")
        with mock.patch.dict(os.environ, {"ARK_API_KEY": "fixture",
                                          "ARK_BASE_URL": "https://ark.fixture.test/api/v3/chat/completions"}):
            self.assertEqual(build_target("volcengine", "model-x").url,
                             "https://ark.fixture.test/api/v3/chat/completions")

    def test_fuzzy_matching_ranks_literal_ids_without_inventing_one(self):
        candidates = ["kimi-k2.7-code", "kimi-k3", "kimi-k3.1-pro", "glm-5.3"]
        self.assertEqual(fuzzy_models(candidates, "kimi-k3")[0], "kimi-k3")
        matches = fuzzy_models(candidates, "k3.1")
        self.assertIn("kimi-k3.1-pro", matches)
        self.assertNotIn("glm-5.3", matches)
        self.assertEqual(fuzzy_models(candidates, "zzzz"), [])

    def test_http_failures_are_classified_without_a_vendor_body(self):
        self.assertEqual(classify_http(401)[1], "auth_failed")
        self.assertEqual(classify_http(403)[1], "auth_failed")
        self.assertEqual(classify_http(429)[1], "rate_limited")
        self.assertEqual(classify_http(402)[1], "insufficient_balance")
        self.assertEqual(classify_http(404, b'{"error":{"message":"model not found"}}')[1],
                         "model_not_found")
        self.assertEqual(classify_http(404, b"no route here")[1], "endpoint_not_found")
        self.assertEqual(classify_http(400, b'{"code":"InvalidParameter","message":"model not exist"}')[1],
                         "model_not_found")
        self.assertEqual(classify_http(400, b'{"message":"bad temperature"}')[1], "invalid_request")
        self.assertEqual(classify_http(503)[1], "provider_error")
        self.assertEqual(classify_network(socket.gaierror()), "dns_error")
        self.assertEqual(classify_network(ConnectionRefusedError()), "connection_refused")

    def test_http_error_adds_a_kind_and_hint_without_echoing_the_body(self):
        def opener(*args, **kwargs):
            raise urllib.error.HTTPError("https://test-only-not-a-key.test", 404,
                                         "test-only-not-a-key", {},
                                         io.BytesIO(b'{"message":"model not found test-only-not-a-key"}'))
        result = measure(target(), "Hello", 16, False, opener=opener)
        self.assertEqual(result["error"], "http_404")
        self.assertEqual(result["error_kind"], "model_not_found")
        self.assertIn("model", result["error_hint"])
        self.assertNotIn("test-only-not-a-key", json.dumps(result, ensure_ascii=False))

    def test_missing_key_is_classified_for_the_report(self):
        with mock.patch.dict(os.environ, {}, clear=True), tempfile.TemporaryDirectory() as folder:
            route = target()
            result = execute([route], plan([route], [100], ["cold"], 1, 16, 500000),
                             Path(folder) / "result.json",
                             probe=mock.Mock(side_effect=AssertionError("no request")))
        row = result["preflight"][0]
        self.assertEqual(row["error_kind"], "missing_key")
        self.assertIn("API Key", row["error_hint"])
        self.assertIn("未能完成测试的路由", display(result))


if __name__ == "__main__":
    unittest.main()
