import hashlib
import tempfile
import unittest
from pathlib import Path

from publish_album_updates import (
    Action,
    album_title,
    append_img_lines,
    canonical_index,
    compute_plan,
    list_remote_objects,
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


class FakeS3Client:
    """Minimal stand-in for boto3's S3 client, scoped to what this script uses."""

    def __init__(self, objects: dict[str, str]):
        # objects: key -> etag (unquoted)
        self.objects = dict(objects)
        self.uploaded = {}

    def get_paginator(self, operation_name):
        assert operation_name == "list_objects_v2"
        return self

    def paginate(self, Bucket, Prefix):
        contents = [
            {"Key": key, "ETag": '"%s"' % etag}
            for key, etag in self.objects.items()
            if key.startswith(Prefix)
        ]
        yield {"Contents": contents}

    def upload_file(self, Filename, Bucket, Key):
        self.uploaded[Key] = Path(Filename).read_bytes()


class TestListRemoteObjects(unittest.TestCase):
    def test_strips_prefix_and_quotes(self):
        client = FakeS3Client(
            {
                "kingfisher/kingfisher-000.jpg": "abc123",
                "dragons/dragons-000.jpg": "zzz999",
            }
        )
        result = list_remote_objects(client, "flowersbytiana", "kingfisher/")
        self.assertEqual(result, {"kingfisher-000.jpg": "abc123"})


class TestComputePlan(unittest.TestCase):
    def _write(self, staging_dir, name, content):
        path = staging_dir / name
        path.write_bytes(content)
        return path

    def test_all_unchanged_when_md5_matches_etag(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir = Path(tmp)
            path0 = self._write(staging_dir, "kingfisher-000.jpg", b"same content")
            etag = md5_of_file(path0)
            local_files = {"kingfisher-000.jpg": path0}
            remote_etags = {"kingfisher-000.jpg": etag}

            actions = compute_plan("kingfisher", local_files, remote_etags)

            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0].kind, "unchanged")
            self.assertEqual(actions[0].index, 0)

    def test_changed_when_md5_differs_from_etag(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir = Path(tmp)
            path0 = self._write(staging_dir, "kingfisher-000.jpg", b"updated content")
            local_files = {"kingfisher-000.jpg": path0}
            remote_etags = {"kingfisher-000.jpg": "some-old-etag"}

            actions = compute_plan("kingfisher", local_files, remote_etags)

            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0].kind, "changed")
            self.assertEqual(actions[0].filename, "kingfisher-000.jpg")

    def test_new_photo_chains_across_consecutive_indexes(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir = Path(tmp)
            path8 = self._write(staging_dir, "dragons-008.jpg", b"eight")
            path9 = self._write(staging_dir, "dragons-009.jpg", b"nine")
            local_files = {
                "dragons-008.jpg": path8,
                "dragons-009.jpg": path9,
            }
            remote_etags = {
                "dragons-%03d.jpg" % i: "etag-%d" % i for i in range(8)
            }  # dragons-000.jpg .. dragons-007.jpg already remote

            actions = compute_plan("dragons", local_files, remote_etags)

            self.assertEqual([a.kind for a in actions], ["new", "new"])
            self.assertEqual([a.index for a in actions], [8, 9])

    def test_gap_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir = Path(tmp)
            path9 = self._write(staging_dir, "dragons-009.jpg", b"nine")
            local_files = {"dragons-009.jpg": path9}
            remote_etags = {
                "dragons-%03d.jpg" % i: "etag-%d" % i for i in range(8)
            }  # max remote index is 7; local jumps straight to 9

            with self.assertRaises(ValueError) as ctx:
                compute_plan("dragons", local_files, remote_etags)

            self.assertIn("dragons-009.jpg", str(ctx.exception))


SAMPLE_INDEX_MD = """---
date: '2026-08-02T12:00:00-04:00'
draft: false
title: 'Dragons'
weight: 110
tags:
  - dragons
  - animals
cover:
  image: "dragons-cover.jpg"
---

{{< img "dragons/dragons-000.jpg" "Dragons 000" >}}

{{< img "dragons/dragons-001.jpg" "Dragons 001" >}}
"""


class TestAlbumTitle(unittest.TestCase):
    def test_extracts_title_from_front_matter(self):
        with tempfile.TemporaryDirectory() as tmp:
            index_md_path = Path(tmp) / "index.md"
            index_md_path.write_text(SAMPLE_INDEX_MD)

            self.assertEqual(album_title(index_md_path), "Dragons")


class TestAppendImgLines(unittest.TestCase):
    def test_appends_one_new_entry_matching_existing_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            index_md_path = Path(tmp) / "index.md"
            index_md_path.write_text(SAMPLE_INDEX_MD)

            new_action = Action("new", "dragons-002.jpg", 2, Path("/unused"))
            append_img_lines(index_md_path, "dragons", "Dragons", [new_action])

            text = index_md_path.read_text()
            self.assertTrue(
                text.endswith(
                    '{{< img "dragons/dragons-001.jpg" "Dragons 001" >}}\n\n'
                    '{{< img "dragons/dragons-002.jpg" "Dragons 002" >}}\n'
                )
            )

    def test_appends_multiple_entries_in_given_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            index_md_path = Path(tmp) / "index.md"
            index_md_path.write_text(SAMPLE_INDEX_MD)

            actions = [
                Action("new", "dragons-002.jpg", 2, Path("/unused")),
                Action("new", "dragons-003.jpg", 3, Path("/unused")),
            ]
            append_img_lines(index_md_path, "dragons", "Dragons", actions)

            text = index_md_path.read_text()
            self.assertIn(
                '{{< img "dragons/dragons-002.jpg" "Dragons 002" >}}\n\n'
                '{{< img "dragons/dragons-003.jpg" "Dragons 003" >}}\n',
                text,
            )

    def test_no_op_when_no_new_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            index_md_path = Path(tmp) / "index.md"
            index_md_path.write_text(SAMPLE_INDEX_MD)

            append_img_lines(index_md_path, "dragons", "Dragons", [])

            self.assertEqual(index_md_path.read_text(), SAMPLE_INDEX_MD)


if __name__ == "__main__":
    unittest.main()
