import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import music_control


class MusicControlTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patch = patch.object(music_control, "STATE_PATH", Path(self.directory.name) / "music.json")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.directory.cleanup()

    def test_authenticated_callsign_can_use_natural_play_and_stop_phrases(self):
        self.assertEqual(music_control.parse_voice_command("BH1JSS", "我是机婶婶，我命令播放一段音乐")["mode"], "random")
        self.assertEqual(music_control.parse_voice_command("BG1ABC", "可以播放歌曲吗")["action"], "play")
        self.assertEqual(music_control.parse_voice_command("BG1ABC", "来首歌听听")["mode"], "random")
        for phrase in ("听歌", "播放歌曲", "唱歌"):
            self.assertEqual(music_control.parse_voice_command("BG1ABC", phrase)["mode"], "random")
        self.assertEqual(music_control.parse_voice_command("BG1ABC", "请停止播放")["action"], "stop")
        self.assertIsNone(music_control.parse_voice_command("", "播放歌曲"))
        self.assertIsNone(music_control.parse_voice_command("BH1JSS", "先播放歌曲然后停止播放"))
        self.assertIsNone(music_control.parse_voice_command("BH1JSS", "这首歌曲很好听"))

    def test_artist_and_title_requests_become_library_searches(self):
        intent = music_control.parse_voice_command("BG1ABC", "播放周杰伦的《稻香》")
        self.assertEqual(intent, {"action": "play", "query": "周杰伦 稻香", "mode": "search"})
        intent = music_control.parse_voice_command("BG1ABC", "我想听稻香")
        self.assertEqual(intent["query"], "稻香")
        intent = music_control.parse_voice_command("BG1ABC", "歌手周杰伦歌名稻香")
        self.assertEqual(intent["query"], "周杰伦 稻香")
        intent = music_control.parse_voice_command("BG1ABC", "我想听周华健的歌")
        self.assertEqual(intent["query"], "周华健")

    def test_play_requires_enabled_and_clear_priority_gates(self):
        self.assertEqual(music_control.request_play(enabled=False)["action"], "disabled")
        blocked = music_control.request_play(enabled=True, blocked_by=["net_checkin_active"])
        self.assertEqual(blocked["action"], "blocked")
        self.assertEqual(music_control.load()["status"], "idle")

    def test_request_and_stop_are_persistent(self):
        requested = music_control.request_play(enabled=True, query="周杰伦 稻香", now=100)
        self.assertEqual(requested["action"], "play_requested")
        self.assertEqual(music_control.load()["status"], "requested")
        self.assertEqual(music_control.load()["requested_query"], "周杰伦 稻香")
        self.assertEqual(music_control.request_play(enabled=True)["action"], "already_active")
        stopped = music_control.request_stop(now=101)
        self.assertEqual(stopped["action"], "stop_requested")
        self.assertTrue(music_control.load()["stop_requested"])

    def test_stop_is_safe_when_idle(self):
        self.assertEqual(music_control.request_stop()["action"], "already_stopped")

    def test_lifecycle_markers_return_to_idle(self):
        music_control.request_play(enabled=True, now=100)
        self.assertEqual(music_control.mark("preparing")["status"], "preparing")
        self.assertEqual(music_control.mark("playing")["status"], "playing")
        finished = music_control.mark("idle", result="completed", now=101)
        self.assertEqual(finished["last_result"], "completed")

    def test_restart_recovers_orphaned_preparation(self):
        music_control.request_play(enabled=True, now=100)
        music_control.mark("preparing", now=101)
        recovered = music_control.recover_after_restart(now=102)
        self.assertEqual(recovered["status"], "idle")
        self.assertEqual(recovered["last_result"], "service_restarted")


if __name__ == "__main__":
    unittest.main()
