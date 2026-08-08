import struct
import unittest

import audio_cues


class AudioCueTests(unittest.TestCase):
    def test_command_chime_is_aligned_short_and_audible(self):
        pcm = audio_cues.command_dingdong()
        self.assertEqual(len(pcm) % 1280, 0)
        self.assertLessEqual(len(pcm), 16000 * 2 * 2)
        samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
        self.assertGreater(max(abs(value) for value in samples), 1000)


if __name__ == "__main__":
    unittest.main()
