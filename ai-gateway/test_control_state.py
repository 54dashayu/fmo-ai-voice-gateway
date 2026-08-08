import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import control_state


class ControlStateTests(unittest.TestCase):
    def test_defaults_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(control_state, "STATE_PATH", Path(directory) / "state.json"):
            self.assertEqual(control_state.load(), {"auto_reply": True, "hourly_announcement": True, "persona_voice_commands": True})
            self.assertFalse(control_state.save({"auto_reply": False})["auto_reply"])
            self.assertTrue(control_state.load()["hourly_announcement"])
            self.assertFalse(control_state.save({"persona_voice_commands": False})["persona_voice_commands"])

    def test_auto_reply_is_open_to_all_but_hourly_is_admin_only(self):
        command = control_state.parse_voice_command("BH1JSS", "我是机婶婶，关闭自动回复。")
        self.assertEqual(command, {"name": "auto_reply", "value": False, "reply": "自动回复已关闭"})
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


if __name__ == "__main__":
    unittest.main()
