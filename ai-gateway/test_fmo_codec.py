import audioop
import ctypes.util
import struct
import unittest

import fmo_codec


class FmoCodecGainTests(unittest.TestCase):
    @unittest.skipUnless(ctypes.util.find_library("opus"), "libopus is unavailable")
    def test_numeric_offset_diagnostic_selects_canonical_payload(self):
        pcm = b"".join(struct.pack("<h", value) for value in (1200, -1200) * 320)
        encoded = fmo_codec.encode_pcm_to_opus(pcm)
        payloads = fmo_codec.build_fmo_payloads(encoded, uid=42, callsign="BH1TEST")
        result = fmo_codec.diagnose_opus_offsets(payloads, 42, "BH1TEST")
        self.assertEqual(result["packet_count"], 1)
        self.assertTrue(result["candidates"]["10"]["valid"])
        self.assertGreater(result["candidates"]["10"]["rms"], 0)

    def test_quiet_pcm_is_amplified_with_a_hard_cap(self):
        pcm = b"".join(struct.pack("<h", value) for value in (10, -10, 20, -20) * 100)
        normalized, gain = fmo_codec.normalize_quiet_pcm16(pcm)
        self.assertEqual(gain, 64.0)
        self.assertGreater(audioop.rms(normalized, 2), audioop.rms(pcm, 2))

    def test_normal_pcm_is_unchanged(self):
        pcm = b"".join(struct.pack("<h", value) for value in (1000, -1000) * 100)
        normalized, gain = fmo_codec.normalize_quiet_pcm16(pcm)
        self.assertEqual(gain, 1.0)
        self.assertEqual(normalized, pcm)


if __name__ == "__main__":
    unittest.main()
