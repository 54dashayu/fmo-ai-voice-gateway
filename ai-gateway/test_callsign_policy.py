import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import callsign_policy


class CallsignPolicyTests(unittest.TestCase):
    def test_normalizes_and_rejects_invalid_callsigns(self):
        self.assertEqual(callsign_policy.normalize(" bh1jss "), "BH1JSS")
        with self.assertRaises(ValueError):
            callsign_policy.normalize("bad callsign")

    def test_blacklist_is_persistent_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            callsign_policy, "BLACKLIST_PATH", Path(directory) / "blacklist.json"
        ):
            saved = callsign_policy.save_blacklist(["bh1jss", "BH1JSS", "BG1ABC"])
            self.assertEqual(saved, ["BG1ABC", "BH1JSS"])
            self.assertEqual(callsign_policy.load_blacklist(), {"BG1ABC", "BH1JSS"})


if __name__ == "__main__":
    unittest.main()
