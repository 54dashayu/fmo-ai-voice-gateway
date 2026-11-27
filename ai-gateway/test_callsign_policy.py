import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

import callsign_policy


class CallsignPolicyTests(unittest.TestCase):
    def test_normalizes_and_rejects_invalid_callsigns(self):
        self.assertEqual(callsign_policy.normalize(" bh1jss "), "BH1JSS")
        with self.assertRaises(ValueError):
            callsign_policy.normalize("bad callsign")

    def test_prefixed_admin_keeps_identity(self):
        with patch.dict(os.environ, {"FMO_VOICE_CONTROL_CALLSIGN": "BG1ADM"}):
            for prefix in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                value = prefix + ".BG1ADM"
                self.assertEqual(callsign_policy.normalize(value.lower()), value)
                self.assertTrue(callsign_policy.is_configured_admin_callsign(value))

    def test_admin_stem_boundaries(self):
        for value in ['BG1ADM','X.BG1ADM','BG1ADM/P','BG1ADM-1','X.BG1ADM/P']:
            self.assertTrue(callsign_policy.is_admin_callsign(value, 'BG1ADM'))
            self.assertEqual(callsign_policy.normalize(value),value)
        for value in ['BG1ADM1','ABG1ADM','X.BG1ADM1','BG1ADM..P','BG1ABC']:
            self.assertFalse(callsign_policy.is_admin_callsign(value, 'BG1ADM'))

    def test_blacklist_is_persistent_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            callsign_policy, "BLACKLIST_PATH", Path(directory) / "blacklist.json"
        ):
            saved = callsign_policy.save_blacklist(["bh1jss", "BH1JSS", "BG1ABC"])
            self.assertEqual(saved, ["BG1ABC", "BH1JSS"])
            self.assertEqual(callsign_policy.load_blacklist(), {"BG1ABC", "BH1JSS"})


if __name__ == "__main__":
    unittest.main()
