import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import control_state


class ControlStateTests(unittest.TestCase):
    def test_defaults_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(control_state, "STATE_PATH", Path(directory) / "state.json"):
            self.assertEqual(control_state.load(), {
                "auto_reply": False,
                "hourly_announcement": False,
                "persona_voice_commands": True,
                "music_playback": False,
                "parrot_replay": False,
                "parrot_restore_auto_reply": False,
                "net_checkin": False,
            })
            self.assertFalse(control_state.save({"auto_reply": False})["auto_reply"])
            self.assertFalse(control_state.load()["hourly_announcement"])
            self.assertFalse(control_state.save({"persona_voice_commands": False})["persona_voice_commands"])
            self.assertTrue(control_state.save({"music_playback": True})["music_playback"])
            self.assertTrue(control_state.save({"parrot_replay": True})["parrot_replay"])
            self.assertTrue(control_state.save({"net_checkin": True})["net_checkin"])

    def test_auto_reply_is_open_to_all_but_hourly_is_admin_only(self):
        command = control_state.parse_voice_command("BH1JSS", "我是机婶婶，关闭自动回复。")
        self.assertEqual(command["name"], "auto_reply")
        self.assertFalse(command["value"])
        self.assertEqual(command["mode"], "off")
        command = control_state.parse_voice_command("BH1JSS", "我是机身身，关闭自动回复")
        self.assertEqual(command["value"], False)
        command = control_state.parse_voice_command("BH1JSS", "我是机婶，关闭自动回")
        self.assertEqual(command["name"], "auto_reply")
        self.assertEqual(control_state.parse_voice_command("BG1ABC", "我是机婶婶关闭自动回复")["value"], False)
        self.assertIsNone(control_state.parse_voice_command("BG1ABC", "我是机婶婶我命令关闭整点报时"))
        self.assertIsNone(control_state.parse_voice_command("BH1JSS", "机婶婶关闭自动回复"))
        self.assertIsNone(control_state.parse_voice_command("BH1JSS", "请关闭自动回复"))
        self.assertIsNone(control_state.parse_voice_command("BH1JSS", "我是机婶婶自动回复"))
        self.assertIsNone(control_state.parse_voice_command("BH1JSS", "我是机婶婶开启然后关闭自动回复"))

    def test_auto_reply_uses_sixty_percent_threshold(self):
        with patch.object(control_state, "AUTO_REPLY_MATCH_THRESHOLD", 0.60):
            command = control_state.parse_voice_command("BG9XYZ", "我是机婶婶关闭回复")
            self.assertIsNotNone(command)
            self.assertEqual(command["name"], "auto_reply")

    def test_rejects_unknown_web_control(self):
        with self.assertRaises(ValueError):
            control_state.save({"mqtt": False})

    def test_reply_modes_are_atomic_and_legacy_boolean_writes_cannot_enable_both(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(control_state, "STATE_PATH", Path(directory) / "state.json"):
            self.assertEqual(control_state.reply_mode(), "off")
            value = control_state.set_reply_mode("parrot_replay")
            self.assertFalse(value["auto_reply"]); self.assertTrue(value["parrot_replay"])
            value = control_state.save({"auto_reply": True})
            self.assertTrue(value["auto_reply"]); self.assertFalse(value["parrot_replay"])
            value = control_state.save({"auto_reply": False})
            self.assertEqual(control_state.reply_mode(), "off")
            with self.assertRaises(ValueError):
                control_state.save({"auto_reply": True, "parrot_replay": True})

    def test_legacy_conflict_migrates_to_off_without_enabling_either_mode(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(control_state, "STATE_PATH", Path(directory) / "state.json"):
            control_state.STATE_PATH.write_text('{"auto_reply":true,"parrot_replay":true}', encoding="utf-8")
            self.assertEqual(control_state.reply_mode(), "off")
            self.assertFalse(control_state.load()["auto_reply"])
            self.assertFalse(control_state.load()["parrot_replay"])

    def test_closing_non_current_reply_mode_keeps_current_mode(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(control_state, "STATE_PATH", Path(directory) / "state.json"):
            control_state.set_reply_mode("parrot_replay")
            command = control_state.parse_voice_command("BG1ABC", "我是机婶婶关闭自动回复")
            self.assertFalse(command["noop"])
            control_state.set_reply_mode(command["mode"])
            self.assertEqual(control_state.reply_mode(), "off")
            control_state.set_reply_mode("auto_reply")
            command = control_state.parse_voice_command("BG1ABC", "我是机婶婶我命令关闭鹦鹉海螺功能")
            self.assertTrue(command["noop"])
            self.assertIn("自动回复模式保持不变", command["reply"])
            self.assertEqual(control_state.reply_mode(), "auto_reply")

    def test_parrot_restores_only_the_previous_auto_reply_state(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(control_state, "STATE_PATH", Path(directory) / "state.json"):
            close = "我是机婶婶我命令关闭鹦鹉海螺功能"
            control_state.set_reply_mode("auto_reply")
            control_state.set_reply_mode("parrot_replay")
            control_state.set_reply_mode("parrot_replay")
            self.assertEqual(control_state.parse_voice_command("BG1ABC", close)["mode"], "auto_reply")
            self.assertTrue(control_state.save({"parrot_replay": False})["auto_reply"])
            control_state.set_reply_mode("off")
            control_state.set_reply_mode("parrot_replay")
            self.assertEqual(control_state.parse_voice_command("BG1ABC", close)["mode"], "off")
            control_state.set_reply_mode("auto_reply")
            control_state.set_reply_mode("parrot_replay")
            self.assertFalse(control_state.save({"auto_reply": False})["parrot_replay"])
            self.assertFalse(control_state.load()["parrot_restore_auto_reply"])

    def test_public_voice_commands_atomically_switch_reply_modes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(control_state, "STATE_PATH", Path(directory) / "state.json"):
            command = control_state.parse_voice_command("BG1ABC", "我是机婶婶，我命令开启鹦鹉海螺功能")
            control_state.set_reply_mode(command["mode"])
            self.assertEqual(control_state.reply_mode(), "parrot_replay")
            self.assertFalse(control_state.load()["auto_reply"])

            command = control_state.parse_voice_command("BG1ABC", "我是机婶婶开启自动回复")
            control_state.set_reply_mode(command["mode"])
            self.assertEqual(control_state.reply_mode(), "auto_reply")
            self.assertFalse(control_state.load()["parrot_replay"])

            command = control_state.parse_voice_command("BG1ABC", "我是机婶婶我命令关闭鹦鹉海螺功能")
            self.assertTrue(command["noop"])
            self.assertEqual(control_state.reply_mode(), "auto_reply")

            command = control_state.parse_voice_command("BG1ABC", "我是机婶婶关闭自动回复")
            control_state.set_reply_mode(command["mode"])
            self.assertEqual(control_state.reply_mode(), "off")

    def test_parrot_switch_is_public_and_uses_text_similarity(self):
        for callsign in ("BH1JSS", "BG1ABC"):
            opened = control_state.parse_voice_command(callsign, "我是机婶婶，我命令开启鹦鹉海螺功能")
            self.assertEqual(opened["name"], "parrot_replay")
            self.assertTrue(opened["value"])
        with patch.object(control_state, "PARROT_MATCH_THRESHOLD", 0.60):
            variant = control_state.parse_voice_command("BG9XYZ", "我是机婶我命令打开鹦鹉海螺")
            self.assertIsNotNone(variant)
            self.assertTrue(variant["value"])
            self.assertGreaterEqual(variant["similarity"], 0.60)
        for variant in (
            "我是机婶婶我命令开启鹦鹉海罗功能",
            "我是机婶婶我命令开启鹦鹉海裸功能",
            "我是机婶婶我命令开启鹦鹉海螺工能",
        ):
            self.assertTrue(control_state.parse_voice_command("BG1ABC", variant)["value"], variant)

    def test_parrot_action_is_unambiguous_and_not_quoted_or_negated(self):
        rejected = (
            "我是机婶婶我命令开启然后关闭鹦鹉海螺功能",
            "我是机婶婶我命令不要开启鹦鹉海螺功能",
            "我是机婶婶我命令不要关闭鹦鹉海螺功能",
            "如果说我是机婶婶我命令开启鹦鹉海螺功能",
            "我是机婶婶重复这句话开启鹦鹉海螺功能",
            "我是机婶婶我命令鹦鹉海螺功能",
        )
        for transcript in rejected:
            self.assertIsNone(control_state.parse_voice_command("BG1ABC", transcript), transcript)

    def test_rejected_parrot_phrase_never_falls_into_another_fuzzy_control(self):
        for transcript in (
            "我是机婶婶我命令不要关闭鹦鹉海罗功能",
            "我是机婶婶开启关闭鹦鹉海裸功能",
            "我是机婶婶引用关闭鹦鹉海螺功能这句话",
        ):
            self.assertIsNone(control_state.parse_voice_command("BG1ABC", transcript), transcript)

    def test_parrot_threshold_boundary(self):
        text = "我是机婶我命令打开鹦鹉海螺"
        score = control_state.parse_parrot_voice_command("BG1ABC", text)["similarity"]
        with patch.object(control_state, "PARROT_MATCH_THRESHOLD", score):
            self.assertIsNotNone(control_state.parse_parrot_voice_command("BG1ABC", text))
        with patch.object(control_state, "PARROT_MATCH_THRESHOLD", min(1.0, score + 0.001)):
            self.assertIsNone(control_state.parse_parrot_voice_command("BG1ABC", text))


if __name__ == "__main__":
    unittest.main()
