import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import persona_state
import speech


class SpeechVoiceTests(unittest.TestCase):
    def test_voice_tracks_gender_age_and_accent(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(persona_state, "STATE_PATH", Path(directory) / "persona.json"):
            persona_state.save({"gender": "女性", "age": "16岁", "accent": "北京话"})
            self.assertEqual(speech.selected_voice(), "longdaiyu_v3")
            persona_state.save({"age": "28岁"})
            self.assertEqual(speech.selected_voice(), "longanwen_v3")
            persona_state.save({"accent": "台湾口音"})
            self.assertEqual(speech.selected_voice(), "longantai_v3")
            self.assertTrue(speech.tts_profile()["accent_effective"])
            persona_state.save({"accent": "东北话"})
            self.assertEqual(speech.selected_voice(), "longanhuan_v3")
            self.assertEqual(speech.selected_instruction(), "请用东北话表达。")
            persona_state.save({"accent": "北京话"})
            self.assertFalse(speech.tts_profile()["accent_effective"])
            persona_state.save({"gender": "男性", "age": "32岁", "accent": "普通话"})
            self.assertEqual(speech.selected_voice(), "longanyang")
            persona_state.save({"gender": "男性", "age": "18岁", "accent": "东北话"})
            self.assertEqual(speech.selected_voice(), "longlaotie_v3")
            self.assertIsNone(speech.selected_instruction())
            self.assertTrue(speech.tts_profile()["accent_effective"])


if __name__ == "__main__":
    unittest.main()
