import contextlib
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from publish_album_updates import (
    Action,
    album_title,
    append_img_lines,
    canonical_index,
    compute_plan,
    list_remote_objects,
    local_canonical_files,
    local_canonical_files_preview,
    md5_of_file,
    normalize_local_filenames,
)
from publish_album_updates import load_env, main, process_album


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


class TestLocalCanonicalFilesPreview(unittest.TestCase):
    def test_matches_local_canonical_files_when_no_short_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_slug_dir = Path(tmp)
            (staging_slug_dir / "kingfisher-000.jpg").write_bytes(b"x")
            (staging_slug_dir / "kingfisher-001.jpg").write_bytes(b"y")
            (staging_slug_dir / "notes.txt").write_bytes(b"z")

            result = local_canonical_files_preview(staging_slug_dir, "kingfisher")

            self.assertEqual(
                set(result.keys()), {"kingfisher-000.jpg", "kingfisher-001.jpg"}
            )

    def test_lists_short_named_file_under_canonical_name_without_renaming(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_slug_dir = Path(tmp)
            short_path = staging_slug_dir / "7.jpg"
            short_path.write_bytes(b"new content for 7")

            result = local_canonical_files_preview(staging_slug_dir, "kingfisher")

            self.assertEqual(set(result.keys()), {"kingfisher-007.jpg"})
            self.assertEqual(result["kingfisher-007.jpg"], short_path)
            # Nothing was renamed on disk.
            self.assertTrue(short_path.exists())
            self.assertFalse((staging_slug_dir / "kingfisher-007.jpg").exists())

    def test_short_named_file_wins_over_colliding_canonical_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_slug_dir = Path(tmp)
            canonical_path = staging_slug_dir / "dragons-004.jpg"
            canonical_path.write_bytes(b"existing canonical content")
            short_path = staging_slug_dir / "4.jpg"
            short_path.write_bytes(b"mis-numbered replacement content")

            result = local_canonical_files_preview(staging_slug_dir, "dragons")

            self.assertEqual(set(result.keys()), {"dragons-004.jpg"})
            # The short-named file wins, matching what Path.rename would do.
            self.assertEqual(result["dragons-004.jpg"], short_path)
            # Neither file was touched on disk.
            self.assertEqual(
                canonical_path.read_bytes(), b"existing canonical content"
            )
            self.assertEqual(
                short_path.read_bytes(), b"mis-numbered replacement content"
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


class TestLoadEnv(unittest.TestCase):
    def test_parses_key_value_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "ACCESS_KEY_ID=abc123\n"
                "SECRET_ACCESS_KEY=def456\n"
                "# a comment\n"
                "\n"
                "CLOUDFLARE_API_TOKEN=ghi789\n"
            )

            env = load_env(env_path)

            self.assertEqual(
                env,
                {
                    "ACCESS_KEY_ID": "abc123",
                    "SECRET_ACCESS_KEY": "def456",
                    "CLOUDFLARE_API_TOKEN": "ghi789",
                },
            )


class TestProcessAlbum(unittest.TestCase):
    def _setup_dirs(self, tmp):
        staging_dir = Path(tmp) / "staging"
        staging_slug_dir = staging_dir / "dragons"
        staging_slug_dir.mkdir(parents=True)
        content_dir = Path(tmp) / "content"
        (content_dir / "dragons").mkdir(parents=True)
        (content_dir / "dragons" / "index.md").write_text(SAMPLE_INDEX_MD)
        return staging_dir, content_dir

    def test_dry_run_does_not_upload_or_write_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir, content_dir = self._setup_dirs(tmp)
            (staging_dir / "dragons" / "2.jpg").write_bytes(b"new painting")

            client = FakeS3Client(
                {
                    "dragons/dragons-000.jpg": "etag0",
                    "dragons/dragons-001.jpg": "etag1",
                }
            )

            actions = process_album(
                client, "flowersbytiana", staging_dir, content_dir, "dragons", False
            )

            self.assertEqual([a.kind for a in actions], ["new"])
            self.assertEqual(client.uploaded, {})
            self.assertEqual(
                (content_dir / "dragons" / "index.md").read_text(), SAMPLE_INDEX_MD
            )

    def test_apply_uploads_and_writes_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir, content_dir = self._setup_dirs(tmp)
            (staging_dir / "dragons" / "2.jpg").write_bytes(b"new painting")

            client = FakeS3Client(
                {
                    "dragons/dragons-000.jpg": "etag0",
                    "dragons/dragons-001.jpg": "etag1",
                }
            )

            process_album(
                client, "flowersbytiana", staging_dir, content_dir, "dragons", True
            )

            self.assertEqual(
                client.uploaded["dragons/dragons-002.jpg"], b"new painting"
            )
            index_text = (content_dir / "dragons" / "index.md").read_text()
            self.assertIn(
                '{{< img "dragons/dragons-002.jpg" "Dragons 002" >}}', index_text
            )

    def test_dry_run_does_not_rename_colliding_short_named_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir, content_dir = self._setup_dirs(tmp)
            staging_slug_dir = staging_dir / "dragons"
            existing_content = b"existing canonical content"
            (staging_slug_dir / "dragons-004.jpg").write_bytes(existing_content)
            colliding_content = b"mis-numbered replacement content"
            (staging_slug_dir / "4.jpg").write_bytes(colliding_content)

            remote_etag = hashlib.md5(existing_content).hexdigest()
            client = FakeS3Client({"dragons/dragons-004.jpg": remote_etag})

            actions = process_album(
                client, "flowersbytiana", staging_dir, content_dir, "dragons", False
            )

            # Correctly classified as "changed" -- diffed against the short
            # name's content, since that's what would win after normalize.
            self.assertEqual([a.kind for a in actions], ["changed"])
            self.assertEqual(actions[0].filename, "dragons-004.jpg")

            # Neither file was touched on disk; nothing was uploaded.
            self.assertTrue((staging_slug_dir / "4.jpg").exists())
            self.assertEqual(
                (staging_slug_dir / "dragons-004.jpg").read_bytes(), existing_content
            )
            self.assertEqual(
                (staging_slug_dir / "4.jpg").read_bytes(), colliding_content
            )
            self.assertEqual(client.uploaded, {})

    def test_apply_aborts_before_upload_when_title_lookup_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir, content_dir = self._setup_dirs(tmp)
            # Overwrite index.md with front matter that has no "title" key,
            # so album_title() raises KeyError.
            (content_dir / "dragons" / "index.md").write_text(
                "---\n"
                "date: '2026-08-02T12:00:00-04:00'\n"
                "draft: false\n"
                "weight: 110\n"
                "---\n"
                '\n{{< img "dragons/dragons-000.jpg" "Dragons 000" >}}\n'
            )
            (staging_dir / "dragons" / "2.jpg").write_bytes(b"new painting")

            client = FakeS3Client(
                {
                    "dragons/dragons-000.jpg": "etag0",
                    "dragons/dragons-001.jpg": "etag1",
                }
            )

            with self.assertRaises(KeyError):
                process_album(
                    client, "flowersbytiana", staging_dir, content_dir, "dragons", True
                )

            self.assertEqual(client.uploaded, {})

    def test_apply_with_only_changed_photo_does_not_touch_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_dir, content_dir = self._setup_dirs(tmp)
            (staging_dir / "dragons" / "dragons-000.jpg").write_bytes(b"updated")

            client = FakeS3Client({"dragons/dragons-000.jpg": "old-etag"})

            process_album(
                client, "flowersbytiana", staging_dir, content_dir, "dragons", True
            )

            self.assertEqual(
                client.uploaded["dragons/dragons-000.jpg"], b"updated"
            )
            self.assertEqual(
                (content_dir / "dragons" / "index.md").read_text(), SAMPLE_INDEX_MD
            )


class TestMain(unittest.TestCase):
    def _setup_repo(self, tmp):
        repo_root = Path(tmp)
        (repo_root / ".env").write_text(
            "ACCESS_KEY_ID=test\nSECRET_ACCESS_KEY=test\n"
        )
        staging_dir = repo_root / "staging"
        staging_dir.mkdir()
        return repo_root, staging_dir

    def test_no_slugs_given_scans_all_staging_subdirectories(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root, staging_dir = self._setup_repo(tmp)
            (staging_dir / "dragons").mkdir()
            (staging_dir / "kingfisher").mkdir()

            client = FakeS3Client({})
            buf = io.StringIO()
            with mock.patch(
                "publish_album_updates.build_s3_client", return_value=client
            ):
                with contextlib.redirect_stdout(buf):
                    result = main(argv=[str(staging_dir)], repo_root=repo_root)

            output = buf.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("dragons:", output)
            self.assertIn("kingfisher:", output)

    def test_one_slug_error_does_not_stop_the_others_and_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root, staging_dir = self._setup_repo(tmp)

            dragons_dir = staging_dir / "dragons"
            dragons_dir.mkdir()
            dragons_path = dragons_dir / "dragons-000.jpg"
            dragons_path.write_bytes(b"a dragon")
            dragons_etag = hashlib.md5(b"a dragon").hexdigest()

            raccoon_dir = staging_dir / "raccoon"
            raccoon_dir.mkdir()
            # Index 5 with no prior remote objects is a gap -> ValueError.
            (raccoon_dir / "raccoon-005.jpg").write_bytes(b"a raccoon")

            client = FakeS3Client({"dragons/dragons-000.jpg": dragons_etag})
            buf = io.StringIO()
            with mock.patch(
                "publish_album_updates.build_s3_client", return_value=client
            ):
                with contextlib.redirect_stdout(buf):
                    result = main(argv=[str(staging_dir)], repo_root=repo_root)

            output = buf.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("dragons:", output)
            self.assertIn("unchanged", output)
            self.assertIn("raccoon:", output)
            self.assertIn("ERROR:", output)

    def test_prints_failure_summary_and_continues_past_missing_staging_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root, staging_dir = self._setup_repo(tmp)

            dragons_dir = staging_dir / "dragons"
            dragons_dir.mkdir()
            dragons_path = dragons_dir / "dragons-000.jpg"
            dragons_path.write_bytes(b"a dragon")
            dragons_etag = hashlib.md5(b"a dragon").hexdigest()

            # "raccoon" has no staging subdirectory at all -> FileNotFoundError
            # (an OSError), not a ValueError, when process_album tries to scan
            # it. main() must still catch this, report it, and continue on to
            # process "dragons" successfully.
            client = FakeS3Client({"dragons/dragons-000.jpg": dragons_etag})
            buf = io.StringIO()
            with mock.patch(
                "publish_album_updates.build_s3_client", return_value=client
            ):
                with contextlib.redirect_stdout(buf):
                    result = main(
                        argv=[str(staging_dir), "dragons", "raccoon"],
                        repo_root=repo_root,
                    )

            output = buf.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("dragons:", output)
            self.assertIn("unchanged", output)
            self.assertIn("raccoon:", output)
            self.assertIn("ERROR:", output)
            self.assertIn("1 album(s) failed.", output)

    def test_all_success_run_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root, staging_dir = self._setup_repo(tmp)

            dragons_dir = staging_dir / "dragons"
            dragons_dir.mkdir()
            dragons_path = dragons_dir / "dragons-000.jpg"
            dragons_path.write_bytes(b"a dragon")
            dragons_etag = hashlib.md5(b"a dragon").hexdigest()

            client = FakeS3Client({"dragons/dragons-000.jpg": dragons_etag})
            buf = io.StringIO()
            with mock.patch(
                "publish_album_updates.build_s3_client", return_value=client
            ):
                with contextlib.redirect_stdout(buf):
                    result = main(argv=[str(staging_dir), "dragons"], repo_root=repo_root)

            self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
