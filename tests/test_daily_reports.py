"""Verify one-scan report editing without source reads or lost billing facts."""

from __future__ import annotations

import copy
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import query_model_prices as cli
from model_price.descriptions.core import unavailable_description
from model_price.delta import scan_providers
from model_price.descriptions.resolver import DescriptionResolver
from model_price.descriptions.sources import OpenRouterDescriptionSource
from model_price.lifecycle_sources import event
from model_price.messages import scan_message, scan_notes
from model_price.reporting import model_title
from model_price.providers.openrouter import OpenRouterAdapter
from model_price.snapshots import SnapshotStore
from model_price.updating import GitSkillUpdater, SELF_UPDATE_ENV, update_skill_before_refresh
from test_change_reports import model_record, scanned_pair
from test_query_model_prices import FakeGitRunner, SKILL_ROOT, git_update_responses
from test_openrouter_scan import FixtureClient, model_entry


def daily_payload() -> dict:
    """A long official introduction beside an exact, conditional price change."""
    payload = scanned_pair([model_record("m1")], [model_record("m1", "0.8125")])
    description = payload["providers"][0]["model_descriptions"][0]
    description.update(
        summary="An official long introduction. " * 30,
        summary_needs_condensing=True,
        capabilities=["text", "reasoning"],
        specifications={"context_window": 128000},
        lifecycle="preview",
    )
    return payload


class OfflineReportTests(unittest.TestCase):
    def render(self, payload: dict, introductions: object | None = None, *, notes: bool = False) -> str:
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            source = root / "scan.json"
            source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            arguments = ["tool", "render", str(source), "--max-chars", "100000"]
            if notes:
                arguments.append("--notes")
            if introductions is not None:
                summaries = root / "introductions.json"
                summaries.write_text(json.dumps(introductions, ensure_ascii=False), encoding="utf-8")
                arguments += ["--introductions", str(summaries)]
            # Offline rendering must dispatch before even constructing I/O clients.
            for name in ("DocumentClient", "build_adapters", "build_description_resolver", "SnapshotStore", "update_skill_before_refresh"):
                stack.enter_context(mock.patch.object(cli, name, side_effect=AssertionError(name)))
            stack.enter_context(mock.patch.object(sys, "argv", arguments))
            output = stack.enter_context(redirect_stdout(io.StringIO()))
            original = source.read_bytes()
            self.assertEqual(cli.main(), 0)
            self.assertEqual(source.read_bytes(), original)
            return output.getvalue()

    def test_saved_payload_renders_identically_without_any_source_or_git_io(self) -> None:
        payload = daily_payload()
        self.assertEqual(self.render(payload), scan_message(payload, max_chars=100000))

    def test_summary_replaces_repeated_prose_only_and_preserves_facts(self) -> None:
        payload = daily_payload()
        original = copy.deepcopy(payload)
        text = self.render(payload, [{
            "provider": "aggregator", "model_id": "m1",
            "summary": "支持文本推理，上下文 128000 tokens。",
        }])
        self.assertIn("支持文本推理，上下文 128000 tokens", text)
        self.assertNotIn("An official long introduction", text)
        self.assertNotIn("主打能力", text)
        self.assertNotIn("规格：", text)
        for fact in ("【价格调整】m1", "预览/实验", "context_tier=short",
                     "1.125 美元/百万 tokens → 0.8125 美元/百万 tokens",
                     "https://aggregator.example/models/m1", "aggregator：有变化",
                     "对比基线：2026-09-29 09:00（UTC+8）"):
            self.assertIn(fact, text)
        self.assertEqual(payload, original)
        self.assertLess(len(text), len(scan_message(payload, max_chars=100000)))

    def test_overrides_are_scoped_to_provider_and_literal_id(self) -> None:
        payload = daily_payload()
        other = copy.deepcopy(payload["providers"][0])
        other["provider"] = {"id": "other", "name": "other"}
        payload["providers"].append(other)
        text = self.render(payload, [{"provider": "aggregator", "model_id": "m1", "summary": "本渠道文本推理。"}])
        self.assertEqual(text.count("本渠道文本推理"), 1)
        self.assertEqual(text.count("An official long introduction"), 30)

    def test_invalid_or_fabricated_summaries_are_rejected(self) -> None:
        summary = {"provider": "aggregator", "model_id": "m1", "summary": "文本推理。"}
        for invalid in (
            [{**summary, "provider": "other"}],
            [{**summary, "model_id": "M1"}],
            [{**summary, "source": "https://invented.example"}],
            [{**summary, "summary": ""}],
            [{**summary, "summary": "字" * 301}],
            [{**summary, "summary": "介绍\n伪造价格"}],
            [{**summary, "summary": "介绍\u2028伪造价格"}],
            [summary, summary],
            {"m1": "missing provider"},
        ):
            with self.subTest(invalid=invalid), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    self.render(daily_payload(), invalid)
                self.assertEqual(error.exception.code, 2)
        payload = daily_payload()
        payload["providers"][0]["model_descriptions"][0] = unavailable_description("m1")
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.render(payload, [summary])

    def test_scan_saves_the_same_payload_before_rendering_without_a_second_scan(self) -> None:
        payload = daily_payload()
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            path = Path(directory) / "run.json"
            stack.enter_context(mock.patch.object(sys, "argv", ["tool", "delta", "--provider", "aggregator", "--save-payload", str(path), "--snapshot-dir", str(Path(directory) / "snapshots")]))
            stack.enter_context(mock.patch.object(cli, "update_skill_before_refresh", return_value=None))
            stack.enter_context(mock.patch.object(cli, "DocumentClient"))
            stack.enter_context(mock.patch.object(cli, "build_adapters", return_value={"aggregator": object()}))
            stack.enter_context(mock.patch.object(cli, "build_description_resolver"))
            scan = stack.enter_context(mock.patch.object(cli, "scan_providers", return_value=payload))
            output = stack.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(cli.main(), 0)
            self.assertEqual(scan.call_count, 1)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)
            self.assertEqual(output.getvalue(), scan_message(payload))

    def test_existing_artifact_is_rejected_before_any_update_or_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.json"
            path.write_text("retained report", encoding="utf-8")
            with mock.patch.object(sys, "argv", ["tool", "delta", "--save-payload", str(path)]), \
                 mock.patch.object(cli, "update_skill_before_refresh", side_effect=AssertionError("scan started")), \
                 redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.main()
            self.assertEqual(path.read_text(encoding="utf-8"), "retained report")

    def test_batch_replacements_and_independent_failures_survive_summarization(self) -> None:
        payload = scanned_pair(
            [model_record("m1"), model_record("m2")],
            [model_record("m1", "0.8125"), model_record("m2", "0.9375")],
        )
        report = payload["providers"][0]
        report["lifecycle"] = {"status": "changed", "changes": [{
            "kind": "detail_revised", "field": "replacement",
            "before": "old-successor", "after": "new-successor",
            "event": event("m1", "https://aggregator.example/notices", replacement="new-successor"),
        }]}
        report["announcements"] = {"status": "source_error", "error": "HTTP 403 publication"}
        text = self.render(payload, [{"provider": "aggregator", "model_id": "m1", "summary": "文本推理。"}])
        self.assertEqual(text.count("m1、m2"), 2)
        self.assertIn("【价格调整】【替代模型更新】m1", text)
        for value in ("old-successor → new-successor", "https://aggregator.example/notices"):
            self.assertIn(value, text)
        notes = scan_notes(payload)
        for value in ("HTTP 403 publication", "模型发布来源：读取失败"):
            self.assertIn(value, notes)
            self.assertNotIn(value, text)
        self.assertNotIn("0.8125", text)
        self.assertNotIn("0.9375", text)
        self.assertNotIn("无变化：aggregator", text)

    def test_offline_render_prints_only_the_delivery_note_on_request(self) -> None:
        payload = daily_payload()
        payload["providers"].append({
            "provider": {"id": "bad", "name": "坏渠道"},
            "status": "source_error",
            "error": "offline",
        })
        text = self.render(payload, notes=True)
        self.assertIn("坏渠道：来源解析失败；offline。", text)
        self.assertNotIn("【渠道结论】", text)
        self.assertNotIn("【模型能力】", text)

    def test_nine_long_openrouter_introductions_shorten_without_reintroducing_rates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            scan_providers([OpenRouterAdapter(FixtureClient([model_entry("base")]))], store,
                           captured_at="2026-10-01T09:00:00+08:00")
            models = [model_entry(f"vendor/new-{index}") for index in range(9)]
            for model in models:
                model["description"] = "An official text reasoning model. " * 20
                model["context_length"] = 128000
            client = FixtureClient([model_entry("base"), *models])
            payload = scan_providers(
                [OpenRouterAdapter(client)], store,
                captured_at="2026-10-02T09:00:00+08:00",
                descriptions=DescriptionResolver({"openrouter": OpenRouterDescriptionSource(client)}),
            )
            original = copy.deepcopy(payload)
            baselines = {path: path.read_bytes() for path in store.root.rglob("*.json")}
            text = self.render(payload, [
                {"provider": "openrouter", "model_id": model["id"], "summary": "文本推理，上下文 128000 tokens。"}
                for model in models
            ])
            for model in models:
                self.assertIn(f"【新增上架】{model['id']}", text)
            self.assertEqual(text.count("文本推理，上下文 128000 tokens"), 9)
            self.assertNotIn("【模型价格】", text)
            self.assertNotIn("美元", text)
            self.assertNotIn("An official text reasoning model", text)
            self.assertEqual(payload, original)
            self.assertEqual(baselines, {path: path.read_bytes() for path in store.root.rglob("*.json")})
            self.assertLess(len(text), len(scan_message(payload, max_chars=100000)))


class ConciseLayoutTests(unittest.TestCase):
    def test_equal_display_name_and_id_are_written_once(self) -> None:
        self.assertEqual(model_title({"model_id": "vendor/model", "display_name": "vendor/model"}), "vendor/model")
        self.assertEqual(model_title({"model_id": "vendor/model", "display_name": "Model"}), "Model（vendor/model）")

    def test_identical_missing_introductions_are_grouped_but_all_notice_facts_remain(self) -> None:
        records = [model_record(f"old-{index}") for index in range(5)]
        payload = scanned_pair(records, records)
        report = payload["providers"][0]
        report["model_descriptions"] = []
        report["lifecycle"] = {"status": "changed", "changes": [
            {"kind": "new_notice", "event": event(
                record["model_id"], "https://aggregator.example/notices",
                eom_at="2026-10-10", eos_at="2026-10-17",
            )} for record in records
        ]}
        original = copy.deepcopy(payload)
        text = scan_message(payload, max_chars=100000)
        self.assertEqual(text.count("未找到官方独立介绍"), 1)
        self.assertEqual(text.count("介绍来源"), 1)
        for record in records:
            self.assertIn(f"【退役公告新增】{record['model_id']}", text)
        for value in ("2026-10-10", "2026-10-17", "https://aggregator.example/notices"):
            self.assertIn(value, text)
        self.assertEqual(payload, original)

    def test_different_missing_statuses_reasons_and_sources_are_not_combined(self) -> None:
        payload = scanned_pair([model_record("base")], [model_record(name) for name in ("base", "m1", "m2", "m3")])
        report = payload["providers"][0]
        report["model_descriptions"] = [
            {**unavailable_description("m1", note="missing card"), "reference_url": "https://a.example/models"},
            {**unavailable_description("m2", note="missing card"), "reference_url": "https://b.example/models"},
            unavailable_description("m3", status="source_error", note="HTTP 403"),
        ]
        text = scan_message(payload, max_chars=100000)
        for value in ("missing card", "https://a.example/models", "https://b.example/models", "HTTP 403", "介绍来源读取失败"):
            self.assertIn(value, text)
        self.assertEqual(text.count("介绍来源"), 4)  # three sources and one failure label


class StrictVersionTests(unittest.TestCase):
    def test_dirty_tree_is_rejected_even_when_head_matches_upstream(self) -> None:
        responses = git_update_responses(" M local.txt\n")
        responses[("rev-parse", "@{upstream}")] = responses[("rev-parse", "HEAD")]
        runner = FakeGitRunner(responses)
        result = GitSkillUpdater(SKILL_ROOT, runner=runner).update(require_current=True)
        self.assertEqual(result["status"], "update_skipped")
        self.assertIn("local changes", result["reason"])

    def test_strict_mode_updates_the_entire_checkout_and_verifies_result(self) -> None:
        runner = FakeGitRunner(git_update_responses())
        def merging(command: list[str], **options: object) -> object:
            result = runner(command, **options)
            if command[1] == "merge":
                runner.responses[("rev-parse", "HEAD")] = runner.responses[("rev-parse", "@{upstream}")]
            return result
        result = GitSkillUpdater(SKILL_ROOT, runner=merging).update(require_current=True)
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["verified_revision"], result["to_revision"])
        self.assertEqual(result["skill_directory"], str(SKILL_ROOT))
        self.assertFalse(any(command[0] == "diff" for command in runner.commands))

    def test_a_merge_that_does_not_reach_the_target_is_not_verified(self) -> None:
        result = GitSkillUpdater(SKILL_ROOT, runner=FakeGitRunner(git_update_responses())).update(require_current=True)
        self.assertEqual(result["status"], "check_failed")
        self.assertNotIn("verified_revision", result)
        self.assertIn("HEAD does not match", result["reason"])

    def test_local_commits_are_not_current_even_if_the_skill_files_are_identical(self) -> None:
        responses = git_update_responses()
        before, after = "a" * 40, "b" * 40
        responses[("diff", "--quiet", f"{before}..{after}", "--", ".")] = (0, "", "")
        responses[("merge-base", "--is-ancestor", before, after)] = (1, "", "")
        runner = FakeGitRunner(responses)
        result = GitSkillUpdater(SKILL_ROOT, runner=runner).update(require_current=True)
        self.assertEqual(result["status"], "update_skipped")
        self.assertNotIn(("merge", "--ff-only", "origin/main"), runner.commands)

    def test_strict_cli_stops_before_source_construction_when_verification_fails(self) -> None:
        with mock.patch.object(sys, "argv", ["tool", "delta", "--require-current"]), \
             mock.patch.object(cli, "update_skill_before_refresh", return_value={"status": "check_failed", "reason": "offline"}), \
             mock.patch.object(cli, "DocumentClient", side_effect=AssertionError("source I/O")), \
             redirect_stderr(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as error:
                cli.main()
        self.assertEqual(error.exception.code, 2)
        self.assertIn("offline", output.getvalue())

    def test_carried_update_result_cannot_bypass_strict_verification(self) -> None:
        with mock.patch.dict(os.environ, {SELF_UPDATE_ENV: json.dumps({"status": "updated"})}), \
             mock.patch("model_price.updating.GitSkillUpdater") as updater:
            updater.return_value.update.return_value = {"status": "check_failed", "reason": "offline"}
            result = update_skill_before_refresh(True, require_current=True)
        updater.return_value.update.assert_called_once_with(require_current=True)
        self.assertEqual(result["status"], "check_failed")


if __name__ == "__main__":
    unittest.main()
