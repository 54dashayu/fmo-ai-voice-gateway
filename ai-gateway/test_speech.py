import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import speech


class SpeechSafetyTests(unittest.TestCase):
    def test_rejects_untrusted_tts_url(self):
        with self.assertRaisesRegex(RuntimeError, "untrusted"):
            speech._safe_audio_url("https://example.com/audio.wav")

    def test_upgrades_trusted_audio_url_to_https(self):
        value = speech._safe_audio_url("http://result.oss-cn-beijing.aliyuncs.com/a.wav?x=1")
        self.assertTrue(value.startswith("https://"))

    def test_rejects_empty_asr_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.wav"
            path.write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "audio size"):
                speech.transcribe(path)

    def test_native_endpoint_is_derived_from_workspace_url(self):
        env = {"DASHSCOPE_BASE_URL": "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(speech._native_base(), "https://workspace.cn-beijing.maas.aliyuncs.com")


if __name__ == "__main__":
    unittest.main()
