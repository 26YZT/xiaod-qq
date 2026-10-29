"""Check the public account/configuration boundary without real credentials."""

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from astrbot.core.config.default import DEFAULT_CONFIG  # noqa: E402
from audit_public import audit  # noqa: E402
from configure_runtime import build_configs, expand  # noqa: E402


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.settings = json.loads((ROOT / "config.example.json").read_text())
        self.settings.update(
            bot_qq="123450001",
            owner_qq="123450002",
            group_qqs=["123450003", "123450004"],
            deepseek_api_key="EXAMPLE_NOT_A_REAL_CREDENTIAL",
            mimo_api_key="EXAMPLE_NOT_A_REAL_CREDENTIAL",
            private_proactive=False,
        )
        self.fallback, self.profiles, self.variables = build_configs(
            DEFAULT_CONFIG, self.settings, "EXAMPLE_WEBSOCKET_BINDING"
        )

    def test_accounts_and_groups_are_install_time_values(self):
        for name in (
            "astrbot_plugin_xiaod_music",
            "astrbot_plugin_xiaod_voice",
            "astrbot_plugin_dafeiyu_chat",
        ):
            template = json.loads(
                (ROOT / "presets/plugins" / (name + ".json")).read_text()
            )
            value = expand(template, self.variables)
            self.assertEqual(value["owner_id"], "123450002")
            self.assertEqual(
                value["group_sessions"],
                [
                    "qq-personal:GroupMessage:123450003",
                    "qq-personal:GroupMessage:123450004",
                ],
            )
            if name != "astrbot_plugin_dafeiyu_chat":
                self.assertEqual(value["account_id"], "123450001")
                self.assertEqual(
                    value["private_sessions"], ["qq-personal:FriendMessage:123450002"]
                )

    def test_tool_capability_and_group_private_separation_are_preserved(self):
        self.assertFalse(self.fallback["provider_settings"]["enable"])
        self.assertEqual(self.fallback["plugin_set"], [])
        for profile in self.profiles.values():
            self.assertTrue(profile["provider_settings"]["enable"])
            self.assertIn("tool_use", profile["provider"][0]["modalities"])
        self.assertFalse(self.profiles["group"]["platform_settings"]["unique_session"])
        self.assertTrue(self.profiles["private"]["platform_settings"]["unique_session"])
        self.assertEqual(self.fallback["dashboard"]["host"], "127.0.0.1")

    def test_no_social_publication_is_enabled_by_default(self):
        value = json.loads(
            (ROOT / "presets/plugins/astrbot_plugin_dafeiyu_presence.json").read_text()
        )
        self.assertFalse(value["enabled"])
        self.assertTrue(value["dry_run"])
        self.assertFalse(value["qzone_enabled"])
        self.assertFalse(value["signature_enabled"])

    def test_L_reference_and_singing_scheme_are_fixed(self):
        singing = json.loads((ROOT / "presets/singing.json").read_text())
        digest = hashlib.sha256((ROOT / "assets/voices/L.wav").read_bytes()).hexdigest()
        self.assertEqual(digest, singing["reference_sha256"])
        self.assertEqual(
            (
                singing["diffusion_steps"],
                singing["guidance"],
                singing["pitch_shift_semitones"],
                singing["length_adjust"],
            ),
            (50, 0.5, 3, 1.0),
        )
        library = json.loads((ROOT / "presets/voices.json").read_text())
        self.assertEqual(library["global_default_voice_id"], singing["voice_id"])
        self.assertEqual(library["user_defaults"], {})
        self.assertEqual(library["group_defaults"], {})

    def test_typed_template_expansion_does_not_turn_false_into_a_string(self):
        value = expand(
            {"enabled": "${PRIVATE_PROACTIVE}", "sessions": "${GROUP_SESSIONS}"},
            self.variables,
        )
        self.assertIs(value["enabled"], False)
        self.assertIsInstance(value["sessions"], list)

    def test_publication_tree_has_no_runtime_or_credentials(self):
        self.assertEqual(audit()["findings"], [])


if __name__ == "__main__":
    unittest.main()
