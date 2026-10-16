"""Keep private capture data and transport diagnostics out of shared reports."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.delta import scan_providers
from model_price.descriptions.resolver import DescriptionResolver
from model_price.descriptions.tencent_mirror import build_tencent_mirror, validate_tencent_mirror
from model_price.errors import HttpSourceError, SkillUpdateError, SourceError
from model_price.lifecycle import _failed
from model_price.privacy import contains_private_data, safe_diagnostic
from model_price.registry import query_adapters
from model_price.rendering import render_saved_scan
from model_price.snapshots import SnapshotStore
from test_change_reports import ChannelAdapter, ChannelIntroduction, model_record
import test_tencent_mirror_capture as capture_fixtures


def private_diagnostic() -> tuple[str, list[str]]:
    """Generate unmistakably synthetic private values, never a usable credential."""
    key = "sk-" + "synthetic-private-value" * 2
    password = "synthetic-proxy-password"
    local_path = "/".join(["", "Users", "example-user", "private", "proxy.conf"])
    email = "example-person" + "@" + "mail.invalid"
    token = "synthetic-query-token"
    text = (
        f"HTTP 403; proxy https://example-user:{password}@proxy.example.test; "
        f"Authorization: Bearer {key}; config '{local_path}'; owner {email}; "
        f"source https://docs.example.test/models?model=m1&access_token={token}"
    )
    return text, [key, password, local_path, email, token]


class DiagnosticPrivacyTests(unittest.TestCase):
    def assert_private_values_absent(self, value: object) -> None:
        _, private_values = private_diagnostic()
        for private in private_values:
            self.assertNotIn(private, str(value))

    def test_public_exceptions_redact_private_values_and_keep_useful_failure_facts(self) -> None:
        message, _ = private_diagnostic()
        for error_type in (SourceError, SkillUpdateError):
            error = error_type(message)
            self.assert_private_values_absent(error)
            self.assertIn("HTTP 403", str(error))
            self.assertIn("docs.example.test/models", str(error))
            self.assertIn("model=m1", str(error))

    def test_typed_http_errors_do_not_expose_sensitive_urls_through_attributes(self) -> None:
        error = HttpSourceError("HTTP 403", url="https://docs.example.test/models?model=m1&api_key=synthetic-private-query", status=403)
        self.assertNotIn("synthetic-private-query", error.url)
        self.assertEqual(error.status, 403)
        self.assertIn("model=m1", error.url)

    def test_redaction_is_idempotent_and_preserves_public_url_spelling(self) -> None:
        message, _ = private_diagnostic()
        redacted = safe_diagnostic(message)
        self.assertEqual(safe_diagnostic(redacted), redacted)
        for url in ("HTTPS://docs.example.test/Models?", "https://docs.example.test/?model=a%2Fb&region=us-east-1#scope"):
            self.assertEqual(safe_diagnostic(url), url)
            self.assertFalse(contains_private_data(url))

    def test_generic_price_errors_are_sanitized_without_overwriting_history(self) -> None:
        source = ChannelAdapter([model_record("m1")])
        message, _ = private_diagnostic()
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory))
            scan_providers([source], store, captured_at="2026-10-14T09:00:00+08:00")
            previous = {path: path.read_bytes() for path in store.root.rglob("*.json")}
            with mock.patch.object(source, "catalog_records", side_effect=RuntimeError(message)):
                payload = scan_providers([source], store, captured_at="2026-10-15T09:00:00+08:00")
            self.assertEqual(payload["providers"][0]["status"], "source_error")
            self.assert_private_values_absent(json.dumps(payload))
            self.assertEqual(previous, {path: path.read_bytes() for path in store.root.rglob("*.json")})

    def test_generic_query_description_and_notice_errors_share_the_same_protection(self) -> None:
        message, _ = private_diagnostic()
        source = ChannelAdapter([model_record("m1")])
        with mock.patch.object(source, "search", side_effect=RuntimeError(message)):
            self.assert_private_values_absent(json.dumps(query_adapters([source], "m1")))
        introduction = ChannelIntroduction("aggregator")
        with mock.patch.object(introduction, "describe", side_effect=RuntimeError(message)):
            payload = query_adapters([source], "m1", descriptions=DescriptionResolver({"aggregator": introduction}))
            self.assert_private_values_absent(json.dumps(payload))
            self.assertEqual(payload["model_descriptions"][0]["status"], "source_error")
        self.assert_private_values_absent(_failed(None, message, datetime.now(timezone.utc)))

    def test_saved_older_errors_are_redacted_without_rewriting_the_original_artifact(self) -> None:
        message, _ = private_diagnostic()
        source = ChannelAdapter([model_record("m1")])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = scan_providers([source], SnapshotStore(root / "history"))
            payload["providers"][0].update(status="source_error", error=message)
            path = root / "scan.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            original = path.read_bytes()
            self.assert_private_values_absent(render_saved_scan(path))
            self.assertEqual(path.read_bytes(), original)


class CapturePrivacyTests(unittest.TestCase):
    def test_checked_in_mirror_remains_usable_with_its_official_api_placeholders(self) -> None:
        path = SCRIPTS / "model_price/descriptions/data/tencent-models.json"
        mirror = json.loads(path.read_text(encoding="utf-8"))
        validate_tencent_mirror(mirror)
        self.assertTrue(mirror["models"])

    def test_nested_account_credential_and_contact_fields_are_rejected(self) -> None:
        for field in ("Authorization", "SecretId", "SecretAccessKey", "access_token", "refreshToken",
                      "session_id", "password", "email", "phoneNumber", "openDingTalkId", "userId"):
            with self.subTest(field=field):
                capture = capture_fixtures.TencentConsoleCaptureTests().capture()
                capture["model_cards"][0]["ModelSpec"][field] = "synthetic-private-value"
                with self.assertRaisesRegex(ValueError, "credentials|account|private"):
                    build_tencent_mirror(capture)

    def test_legacy_capture_cannot_smuggle_private_metadata_or_unknown_top_level_fields(self) -> None:
        clean = build_tencent_mirror(capture_fixtures.TencentConsoleCaptureTests().capture())
        for target in ("top-level", "model"):
            with self.subTest(target=target):
                capture = copy.deepcopy(clean)
                owner = capture if target == "top-level" else capture["models"][0]["console_metadata"]
                owner["authorization"] = "Bearer synthetic-private-value"
                with self.assertRaisesRegex(ValueError, "credentials|account|private"):
                    build_tencent_mirror(capture)

    def test_freeform_private_urls_and_cookies_are_rejected_but_official_templates_still_work(self) -> None:
        for value in (
            "Authorization: Bearer synthetic-private-value",
            "Cookie: session=synthetic-private-value",
            "https://console.example.test/models?%75in=synthetic-private-value",
            "https://example-user:synthetic-private-value@console.example.test/models",
        ):
            with self.subTest(value=value):
                capture = capture_fixtures.TencentConsoleCaptureTests().capture()
                capture["capability_sets"]["example-model"][-1]["CapabilityValue"] = value
                with self.assertRaisesRegex(ValueError, "credentials|account|private"):
                    build_tencent_mirror(capture)
        clean = build_tencent_mirror(capture_fixtures.TencentConsoleCaptureTests().capture())
        self.assertEqual(len(clean["models"]), 2)


if __name__ == "__main__":
    unittest.main()
