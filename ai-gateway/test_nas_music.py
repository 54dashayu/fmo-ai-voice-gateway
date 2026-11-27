import random
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import nas_music


class NasMusicTests(unittest.TestCase):
    def metadata(self, duration=120.0):
        return nas_music.AudioMetadata(Path("song.flac"), duration, "flac", 48000, 2, "flac")

    def test_default_plan_stays_in_middle_and_is_30_seconds(self):
        plan = nas_music.choose_plan(self.metadata(), rng=random.Random(7))
        self.assertEqual(plan.duration, 30.0)
        self.assertGreaterEqual(plan.start, 15.0)
        self.assertLessEqual(plan.start + plan.duration, 105.0)

    def test_hard_limit_and_short_track(self):
        self.assertEqual(nas_music.choose_plan(self.metadata(), requested=99).duration, 45.0)
        short = nas_music.choose_plan(self.metadata(12.0), requested=30)
        self.assertEqual(short.duration, 12.0)
        self.assertEqual(short.start, 0.0)

    def test_gate_defaults_closed_and_priorities_block(self):
        self.assertEqual(nas_music.PlaybackGate().blockers(), ["disabled", "admin_authorization_required"])
        gate = nas_music.PlaybackGate(enabled=True, admin_authorized=True, human_active=True,
                                      net_checkin_active=True, ai_reply_active=True)
        self.assertEqual(gate.blockers(), ["human_active", "net_checkin_active", "ai_reply_active"])

    def test_discovery_ignores_symlinks_and_unknown_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.mp3").touch()
            (root / "b.txt").touch()
            (root / "link.flac").symlink_to(root / "a.mp3")
            self.assertEqual(nas_music.discover(root), [(root / "a.mp3").resolve()])

    def test_rendered_fixture_is_16khz_mono_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fixture.wav"
            output = root / "clip.wav"
            with wave.open(str(source), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\x00\x00" * 16000 * 2)
            metadata = nas_music.probe(source)
            plan = nas_music.choose_plan(metadata, requested=1.25, rng=random.Random(1))
            nas_music.render(plan, output)
            with wave.open(str(output), "rb") as wav:
                self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (16000, 1, 2))
                self.assertLessEqual(wav.getnframes() / wav.getframerate(), 1.26)

    def test_controlled_stream_renders_without_loading_whole_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fixture.wav"
            output = root / "streamed.wav"
            with wave.open(str(source), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\x00\x00" * 16000 * 2)
            plan = nas_music.choose_plan(nas_music.probe(source), requested=1.0, rng=random.Random(2))
            with source.open("rb") as handle:
                nas_music.stream_to_ffmpeg(plan, handle, output, chunk_bytes=4096)
            with wave.open(str(output), "rb") as wav:
                self.assertLessEqual(wav.getnframes() / wav.getframerate(), 1.01)

    def test_preempted_stream_removes_partial_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fixture.wav"
            output = root / "partial.wav"
            with wave.open(str(source), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\x00\x00" * 16000)
            plan = nas_music.choose_plan(nas_music.probe(source), requested=1.0)
            with source.open("rb") as handle:
                with self.assertRaisesRegex(nas_music.MusicError, "preempted"):
                    nas_music.stream_to_ffmpeg(plan, handle, output, cancelled=lambda: True)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
