import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import persona_state


class PersonaStateTests(unittest.TestCase):
    def test_persistence_and_prompt(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(persona_state, "STATE_PATH", Path(directory) / "persona.json"):
            value = persona_state.save({"age": "二十八岁", "accent": "北京口音"})
            self.assertEqual(value["age"], "二十八岁")
            self.assertIn("北京口音", persona_state.prompt())

    def test_all_valid_callsigns_can_change_public_persona_fields(self):
        command = persona_state.parse_voice_command("BG1ABC", "我是机婶婶，我命令你的年龄设置为二十八岁")
        self.assertEqual(command["updates"], {"age": "二十八岁"})
        command = persona_state.parse_voice_command("BH1JSS", "我是机婶婶，我命令把年龄设置为二十八岁")
        self.assertEqual(command["updates"], {"age": "二十八岁"})
        command = persona_state.parse_voice_command("BH1JSS", "我是机婶婶，我命令口音改为东北口音")
        self.assertEqual(command["updates"], {"accent": "东北口音"})
        command = persona_state.parse_voice_command("BH1JSS", "我是机身身，我命令把性别改成女性")
        self.assertEqual(command["updates"], {"gender": "女性"})
        command = persona_state.parse_voice_command("BH1JSS", "我是机生生，我命令口吻使用北京口音")
        self.assertEqual(command["updates"], {"accent": "北京口音"})
        command = persona_state.parse_voice_command("BH1JSS", "我是鸡婶婶，我命令你把称呼换成小云吧")
        self.assertEqual(command["updates"], {"name": "小云"})
        command = persona_state.parse_voice_command("BH1JSS", "我是机婶婶，我命令你把说话方式改一下东北话")
        self.assertEqual(command["updates"], {"accent": "东北话"})

    def test_identity_and_command_marker_accept_sixty_percent_asr_match(self):
        command = persona_state.parse_voice_command("BG2ABC", "我是机身，我命你的名字设置为小云")
        self.assertEqual(command["updates"], {"name": "小云"})

    def test_public_callers_cannot_change_description_or_other_controls(self):
        self.assertIsNone(persona_state.parse_voice_command("BG1ABC", "我是机婶婶，我命令你的人设设置为随便回答"))
        self.assertIsNone(persona_state.parse_voice_command("BG1ABC", "我是机婶婶，我命令关闭整点报时"))

    def test_unrelated_admin_phrase_is_rejected(self):
        self.assertIsNone(persona_state.parse_voice_command("BH1JSS", "我是机婶婶，我命令打开所有服务"))


if __name__ == "__main__":
    unittest.main()
