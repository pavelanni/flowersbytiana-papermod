import hashlib
import tempfile
import unittest
from pathlib import Path

from publish_album_updates import (
    canonical_index,
    local_canonical_files,
    md5_of_file,
    normalize_local_filenames,
)


class TestMd5OfFile(unittest.TestCase):
    def test_matches_hashlib(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.jpg"
            path.write_bytes(b"hello world")
            expected = hashlib.md5(b"hello world").hexdigest()
            self.assertEqual(md5_of_file(path), expected)


class TestCanonicalIndex(unittest.TestCase):
    def test_matches_canonical_name(self):
        self.assertEqual(canonical_index("kingfisher-007.jpg", "kingfisher"), 7)

    def test_rejects_short_name(self):
        self.assertIsNone(canonical_index("7.jpg", "kingfisher"))

    def test_rejects_wrong_slug_prefix(self):
        self.assertIsNone(canonical_index("dragons-007.jpg", "kingfisher"))

    def test_rejects_non_three_digit(self):
        self.assertIsNone(canonical_index("kingfisher-7.jpg", "kingfisher"))


class TestNormalizeLocalFilenames(unittest.TestCase):
    def test_renames_short_names_to_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_slug_dir = Path(tmp)
            (staging_slug_dir / "0.jpg").write_bytes(b"new content for 0")
            (staging_slug_dir / "7.jpg").write_bytes(b"new content for 7")

            normalize_local_filenames(staging_slug_dir, "kingfisher")

            self.assertFalse((staging_slug_dir / "0.jpg").exists())
            self.assertFalse((staging_slug_dir / "7.jpg").exists())
            self.assertEqual(
                (staging_slug_dir / "kingfisher-000.jpg").read_bytes(),
                b"new content for 0",
            )
            self.assertEqual(
                (staging_slug_dir / "kingfisher-007.jpg").read_bytes(),
                b"new content for 7",
            )

    def test_overwrites_existing_canonical_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_slug_dir = Path(tmp)
            (staging_slug_dir / "kingfisher-000.jpg").write_bytes(b"old content")
            (staging_slug_dir / "0.jpg").write_bytes(b"replacement content")

            normalize_local_filenames(staging_slug_dir, "kingfisher")

            self.assertEqual(
                (staging_slug_dir / "kingfisher-000.jpg").read_bytes(),
                b"replacement content",
            )

    def test_leaves_already_canonical_files_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_slug_dir = Path(tmp)
            (staging_slug_dir / "dragons-008.jpg").write_bytes(b"content")

            normalize_local_filenames(staging_slug_dir, "dragons")

            self.assertEqual(
                (staging_slug_dir / "dragons-008.jpg").read_bytes(), b"content"
            )


class TestLocalCanonicalFiles(unittest.TestCase):
    def test_returns_only_canonical_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_slug_dir = Path(tmp)
            (staging_slug_dir / "kingfisher-000.jpg").write_bytes(b"x")
            (staging_slug_dir / "kingfisher-001.jpg").write_bytes(b"y")
            (staging_slug_dir / "notes.txt").write_bytes(b"z")

            result = local_canonical_files(staging_slug_dir, "kingfisher")

            self.assertEqual(
                set(result.keys()), {"kingfisher-000.jpg", "kingfisher-001.jpg"}
            )
            self.assertEqual(
                result["kingfisher-000.jpg"], staging_slug_dir / "kingfisher-000.jpg"
            )


if __name__ == "__main__":
    unittest.main()
