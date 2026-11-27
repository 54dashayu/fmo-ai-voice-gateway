import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import service
from service import cosine


class VectorTests(unittest.TestCase):
    @staticmethod
    def _id3_frame(frame_id: bytes, text: str) -> bytes:
        value = b"\x03" + text.encode("utf-8")
        return frame_id + len(value).to_bytes(4, "big") + b"\0\0" + value

    @staticmethod
    def _synchsafe(size: int) -> bytes:
        return bytes(((size >> 21) & 0x7f, (size >> 14) & 0x7f, (size >> 7) & 0x7f, size & 0x7f))

    def test_same_vector(self):
        self.assertAlmostEqual(cosine([1, 2], [1, 2]), 1.0)

    def test_opposite_vector(self):
        self.assertAlmostEqual(cosine([1, 0], [-1, 0]), -1.0)

    def test_dimension_mismatch(self):
        self.assertEqual(cosine([1], [1, 2]), -1.0)

    def test_safe_relative_path(self):
        self.assertEqual(service.safe_relative("amateur-radio/manuals"), Path("amateur-radio/manuals"))
        with self.assertRaises(ValueError):
            service.safe_relative("../secret")

    def test_file_entries_reports_pending_file(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            with patch.object(service, "DATA_DIR", data), patch.object(service, "DOCUMENT_DIR", data / "documents"), patch.object(service, "DB_PATH", data / "db.sqlite3"):
                service.DOCUMENT_DIR.mkdir()
                (service.DOCUMENT_DIR / "manual.pdf").write_bytes(b"pdf")
                entries = service.file_entries(Path())
                self.assertEqual(entries[0]["indexed_sections"], 0)

    def test_music_search_and_id_do_not_expose_full_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "周杰伦"
            folder.mkdir()
            song = folder / "稻香.mp3"
            song.write_bytes(b"test-audio")
            with patch.object(service, "MUSIC_DIR", root):
                result = service.music_search("周杰伦稻香")
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["name"], "稻香")
                self.assertNotIn(str(root), str(result))
                self.assertEqual(service.music_by_id(result[0]["id"]), song.resolve())

    def test_music_scan_rejects_symlink_and_unknown_format(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / "outside-test.mp3"
            outside.write_bytes(b"outside")
            try:
                (root / "linked.mp3").symlink_to(outside)
                (root / "notes.txt").write_text("not music")
                (root / "valid.flac").write_bytes(b"audio")
                with patch.object(service, "MUSIC_DIR", root):
                    self.assertEqual(service.music_files(), [(root / "valid.flac").resolve()])
            finally:
                outside.unlink(missing_ok=True)

    def test_music_search_matches_id3_artist_and_title_for_numbered_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "周杰伦"
            folder.mkdir()
            frames = self._id3_frame(b"TIT2", "稻香") + self._id3_frame(b"TPE1", "周杰伦")
            song = folder / "02.mp3"
            song.write_bytes(b"ID3\x03\0\0" + self._synchsafe(len(frames)) + frames)
            with patch.object(service, "MUSIC_DIR", root):
                result = service.music_search("周杰伦 稻香")
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["name"], "稻香")
                self.assertEqual(result[0]["artist"], "周杰伦")

    def test_music_id_rejects_invalid_value(self):
        with self.assertRaises(ValueError):
            service.music_by_id("../secret")


if __name__ == "__main__":
    unittest.main()
