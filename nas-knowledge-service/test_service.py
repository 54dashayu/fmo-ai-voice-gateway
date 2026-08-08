import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import service
from service import cosine


class VectorTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
