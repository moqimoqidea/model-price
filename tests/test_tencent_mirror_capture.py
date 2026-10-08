"""Keep console evidence scoped to literal IDs and reject incomplete refreshes."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_price.descriptions.sources import TencentMirrorDescriptionSource
from model_price.descriptions.tencent_mirror import TENCENT_MODELS_URL, build_tencent_mirror
from model_price.reporting import specification_text


class TencentConsoleCaptureTests(unittest.TestCase):
    def capture(self) -> dict[str, Any]:
        cards = [
            {
                "ModelId": "example-model",
                "DisplayName": "Example Model",
                "Description": "The platform's ordinary model introduction.",
                "Tags": ["文本生成"],
                "Status": "online",
                "Brand": "Example",
                "ReleaseAt": "2026-09-01T08:00:00+08:00",
                "ModelSpec": {"ContextLength": "256K", "TPM": "1000000"},
            },
            {
                "ModelId": "example/model",
                "DisplayName": "Example Model",
                "Description": "The platform's direct-service introduction.",
                "Tags": ["文本生成"],
                "Status": "online",
                "Provider": "原厂直供",
                "ModelSpec": {"ContextLength": "1M"},
                "ExtraModelIds": ["example/model-compatible"],
            },
        ]
        capabilities = [
            {"CapabilityName": "InputText", "DisplayName": "文本输入", "CapabilityValue": "true"},
            {"CapabilityName": "OutputText", "DisplayName": "文本输出", "CapabilityValue": "true"},
            {"CapabilityName": "FunctionCall", "DisplayName": "Function Calling", "CapabilityValue": "true"},
            {"CapabilityName": "StructOutput", "DisplayName": "结构化输出", "CapabilityValue": "false"},
            {"CapabilityName": "CompletionsAPI", "DisplayName": "Completions API", "CapabilityValue": "Authorization: Bearer YOUR_API_KEY"},
        ]
        return {
            "captured_at": "2026-10-08T17:00:00+08:00",
            "source_url": TENCENT_MODELS_URL,
            "catalogue_total": 2,
            "model_cards": cards,
            "capability_sets": {"example-model": capabilities, "example/model": []},
            "generation_configs": [
                {"ModelID": "example-model", "kind": "image", "PromptMaxLength": 1000}
            ],
        }

    def test_same_display_name_keeps_each_literal_ids_own_evidence(self) -> None:
        mirror = build_tencent_mirror(self.capture(), [
            {"model_id": "example-model", "display_name": "Example Model"},
            {"model_id": "ambiguous-model", "display_name": "Example Model"},
            {"model_id": "direct-compatible", "display_name": "Example Model 原厂直供"},
        ])
        self.assertEqual(mirror["schema_version"], 2)
        ordinary, direct = mirror["models"]
        self.assertEqual(ordinary["model_id"], "example-model")
        self.assertEqual(direct["model_id"], "example/model")
        self.assertEqual(ordinary["specifications"]["context_window"], "256K")
        self.assertEqual(direct["specifications"]["context_window"], "1M")
        self.assertNotIn("ambiguous-model", ordinary["aliases"] + direct["aliases"])
        self.assertEqual(direct["aliases"], ["example/model-compatible", "direct-compatible"])
        self.assertIn("Function Calling", ordinary["capabilities"])
        self.assertNotIn("结构化输出", ordinary["capabilities"])
        self.assertEqual(ordinary["specifications"]["input_modalities"], "文本")
        self.assertEqual(ordinary["console_metadata"]["model_spec"]["TPM"], "1000000")
        self.assertEqual(ordinary["console_metadata"]["generation_configs"][0]["PromptMaxLength"], 1000)

    def test_eom_does_not_become_eos_and_eos_keeps_its_offset(self) -> None:
        capture = self.capture()
        ordinary, direct = capture["model_cards"]
        ordinary.update(Status="discontinued", DiscontinuedAt="2026-10-01T00:00:00+08:00", OfflineAt="2026-10-31T00:00:00+08:00")
        direct.update(Status="discontinued", OfflineAt="2026-09-27T00:00:00+08:00")
        ordinary, direct = build_tencent_mirror(capture)["models"]
        self.assertEqual(ordinary["lifecycle"], "legacy")
        self.assertEqual(direct["lifecycle"], "retired")
        self.assertEqual(ordinary["specifications"]["offline_at"], "2026-10-31T00:00:00+08:00")

    def test_incomplete_or_private_captures_are_rejected(self) -> None:
        capture = self.capture()
        capture["catalogue_total"] = 3
        with self.assertRaisesRegex(ValueError, "catalogue_total"):
            build_tencent_mirror(capture)
        capture = self.capture()
        del capture["capability_sets"]["example/model"]
        with self.assertRaisesRegex(ValueError, "not captured"):
            build_tencent_mirror(capture)
        capture = self.capture()
        capture["model_cards"][0]["APIKey"] = "private"
        with self.assertRaisesRegex(ValueError, "credentials"):
            build_tencent_mirror(capture)
        capture = self.capture()
        capture["capability_sets"]["example-model"][-1]["CapabilityValue"] = "Authorization: Bearer sk-" + "a" * 32
        with self.assertRaisesRegex(ValueError, "credentials"):
            build_tencent_mirror(capture)

    def test_runtime_retains_raw_metadata_outside_message_specifications(self) -> None:
        mirror = build_tencent_mirror(self.capture())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mirror.json"
            path.write_text(json.dumps(mirror), encoding="utf-8")
            source = TencentMirrorDescriptionSource(None, path)
            result = source.describe("example/model-compatible")
            self.assertEqual(result["summary"], "The platform's direct-service introduction.")
            self.assertEqual(result["console_metadata"]["model_spec"]["ContextLength"], "1M")
            result = source.describe("example-model")
            prose = specification_text(result["specifications"])
            self.assertIn("目录 TPM=1000000", prose)
            self.assertNotIn("YOUR_API_KEY", prose)


if __name__ == "__main__":
    unittest.main()
