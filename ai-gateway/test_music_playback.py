import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import music_playback
import nas_music


class FakeResponse(io.BytesIO):
    headers = {"Content-Length": "4"}
    def __enter__(self): return self
    def __exit__(self, *_args): self.close()


class MusicPlaybackTests(unittest.TestCase):
    def test_search_returns_only_streamable_results(self):
        payload = json.dumps({"results": [{"id": "ok"}, {"name": "bad"}]}).encode()
        with patch.object(music_playback.urllib.request, "urlopen", return_value=FakeResponse(payload)):
            with patch.object(music_playback, "_request"):
                self.assertEqual(music_playback.search("稻香"), [{"id": "ok"}])

    def test_download_honours_preemption_and_deletes_partial_source(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "source"
            with patch.object(music_playback.urllib.request, "urlopen", return_value=FakeResponse(b"data")):
                with patch.object(music_playback, "_request"):
                    with self.assertRaisesRegex(nas_music.MusicError, "preempted"):
                        music_playback._download("id", target, lambda: True)

    def test_pcm_chunks_accept_expected_wire_format(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clip.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
                output.writeframes(b"\0\0" * 160)
            clip = music_playback.PreparedClip(path, 0.01, 2.0, "测试歌曲")
            self.assertEqual(sum(map(len, music_playback.pcm_chunks(clip))), 320)

    def test_prepared_clip_carries_a_bounded_display_title(self):
        clip = music_playback.PreparedClip(Path("/tmp/unused"), 30.0, 120.0, "测试歌曲")
        self.assertEqual(clip.title, "测试歌曲")


if __name__ == "__main__":
    unittest.main()
