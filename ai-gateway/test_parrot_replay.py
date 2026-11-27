import unittest

import parrot_replay


class ParrotReplayTests(unittest.TestCase):
    def test_raw_pcm_is_bounded_even_and_amplitude_unchanged(self):
        raw = (b"\x34\x12\xcc\xed") * 20000
        clipped = parrot_replay.clip_pcm(raw, 16000, 1000)
        self.assertEqual(len(clipped), 32000)
        self.assertEqual(clipped, raw[:32000])
        self.assertEqual(len(clipped) % 2, 0)

    def test_single_turn_replaces_ai_but_not_commands_or_priority_modes(self):
        self.assertTrue(parrot_replay.should_replace_ai(enabled=True, handled_command=False, priority_mode=False))
        self.assertFalse(parrot_replay.should_replace_ai(enabled=True, handled_command=True, priority_mode=False))
        self.assertFalse(parrot_replay.should_replace_ai(enabled=True, handled_command=False, priority_mode=True))
        self.assertFalse(parrot_replay.should_replace_ai(enabled=False, handled_command=False, priority_mode=False))

    def test_disable_disconnect_new_input_and_priority_cancel_pending_replay(self):
        base = dict(generation=4, current_generation=4, active_frames=0, connected=True,
                    audio_exclusive=False, net_checkin_active=False)
        self.assertTrue(parrot_replay.allowed(enabled=True, **base))
        for change in (
            {"enabled": False}, {"connected": False}, {"current_generation": 5},
            {"active_frames": 1}, {"audio_exclusive": True}, {"net_checkin_active": True},
        ):
            values = {"enabled": True, **base, **change}
            self.assertFalse(parrot_replay.allowed(**values), change)


if __name__ == "__main__":
    unittest.main()
