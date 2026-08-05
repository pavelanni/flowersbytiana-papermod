# Publish Album Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/publish_album_updates.py`, a reusable, idempotent, dry-run-by-default script that detects new/changed photos dropped into `staging/<slug>/`, uploads them to R2 via boto3, and appends `index.md` lines for genuinely new photos — then use it to publish the pending kingfisher replacements and dragons additions.

**Architecture:** A single script with small, independently testable pure(ish) functions: local filename normalization, an MD5-vs-ETag diff against R2 (listed live via boto3, not a stale manifest), and an `index.md` appender. `boto3`'s S3-compatible client is injected as a parameter everywhere it's used, so tests substitute a hand-rolled fake client instead of hitting real R2 or adding a mocking dependency.

**Tech Stack:** Python 3 (stdlib `unittest` for tests — no pytest/moto), `boto3` (already installed), `PyYAML` (already installed, for front-matter parsing).

## Global Constraints

- No new pip dependency beyond `boto3` (per spec) — tests use stdlib `unittest` + a hand-rolled fake S3 client, not `pytest`/`moto`.
- Script lives at `flowersbytiana-papermod/scripts/publish_album_updates.py`, committed to the Hugo repo.
- Reads `ACCESS_KEY_ID` / `SECRET_ACCESS_KEY` from `flowersbytiana-papermod/.env` (already gitignored). `CLOUDFLARE_API_TOKEN` in that same file is not used.
- R2 account ID is `7b640a8e821a63b945fa89a9c3d09363`; bucket is `flowersbytiana`; endpoint is `https://7b640a8e821a63b945fa89a9c3d09363.r2.cloudflarestorage.com`; `region_name="auto"`.
- Dry run is the default; only `--apply` performs uploads or `index.md` writes. The script never runs `git add/commit/push`.
- ETag-as-MD5 comparison is valid because all photos in this project are well under R2's multipart upload threshold — no multipart-ETag handling needed.
- Short local filenames (`0.jpg`) are shorthand for canonical `<slug>-NNN.ext` (zero-padded to 3 digits) and must be renamed in place before any diffing.
- New photos are only accepted at `current_max_remote_index + 1` (no gaps); violating this is a hard error naming the slug and filename, not a silent guess.
- `staging/manifest.json` is never read or written by this script.

---

### Task 1: Local file normalization and hashing helpers

**Files:**
- Create: `flowersbytiana-papermod/scripts/publish_album_updates.py`
- Create: `flowersbytiana-papermod/scripts/test_publish_album_updates.py`

**Interfaces:**
- Produces: `md5_of_file(path: Path) -> str`
- Produces: `canonical_index(filename: str, slug: str) -> int | None`
- Produces: `normalize_local_filenames(staging_slug_dir: Path, slug: str) -> None`
- Produces: `local_canonical_files(staging_slug_dir: Path, slug: str) -> dict[str, Path]`

- [ ] **Step 1: Write the failing tests**

Create `flowersbytiana-papermod/scripts/test_publish_album_updates.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd flowersbytiana-papermod/scripts && python3 -m unittest test_publish_album_updates -v`
Expected: FAIL / ImportError — `publish_album_updates` module doesn't exist yet.

- [ ] **Step 3: Write the implementation**

Create `flowersbytiana-papermod/scripts/publish_album_updates.py`:

```python
#!/usr/bin/env python3
"""Publish incremental photo updates from staging/<slug>/ to R2 and index.md."""
import hashlib
import re
from pathlib import Path

SHORT_NAME_RE = re.compile(r"^(\d+)(\.[A-Za-z0-9]+)$")


def md5_of_file(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def canonical_index(filename: str, slug: str) -> int | None:
    m = re.match(r"^%s-(\d{3})(\.[A-Za-z0-9]+)$" % re.escape(slug), filename)
    if not m:
        return None
    return int(m.group(1))


def normalize_local_filenames(staging_slug_dir: Path, slug: str) -> None:
    for path in list(staging_slug_dir.iterdir()):
        if not path.is_file():
            continue
        m = SHORT_NAME_RE.match(path.name)
        if not m:
            continue
        index = int(m.group(1))
        ext = m.group(2)
        canonical_path = staging_slug_dir / ("%s-%03d%s" % (slug, index, ext))
        path.rename(canonical_path)


def local_canonical_files(staging_slug_dir: Path, slug: str) -> dict[str, Path]:
    result = {}
    for path in staging_slug_dir.iterdir():
        if not path.is_file():
            continue
        if canonical_index(path.name, slug) is not None:
            result[path.name] = path
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd flowersbytiana-papermod/scripts && python3 -m unittest test_publish_album_updates -v`
Expected: PASS, all tests in `TestMd5OfFile`, `TestCanonicalIndex`, `TestNormalizeLocalFilenames`, `TestLocalCanonicalFiles`.

- [ ] **Step 5: Commit**

```bash
cd flowersbytiana-papermod
git add scripts/publish_album_updates.py scripts/test_publish_album_updates.py
git commit -m "Add local file normalization helpers for publish_album_updates.py"
```

---

### Task 2: Remote listing and the core diff/plan algorithm

**Files:**
- Modify: `flowersbytiana-papermod/scripts/publish_album_updates.py`
- Modify: `flowersbytiana-papermod/scripts/test_publish_album_updates.py`

**Interfaces:**
- Consumes: `md5_of_file(path: Path) -> str`, `canonical_index(filename: str, slug: str) -> int | None` (Task 1)
- Produces: `Action` dataclass with fields `kind: str` (`"new"|"changed"|"unchanged"`), `filename: str`, `index: int`, `local_path: Path`
- Produces: `list_remote_objects(s3_client, bucket: str, prefix: str) -> dict[str, str]` (filename -> etag, prefix stripped)
- Produces: `compute_plan(slug: str, local_files: dict[str, Path], remote_etags: dict[str, str]) -> list[Action]` (raises `ValueError` on a gap)

- [ ] **Step 1: Write the failing tests**

Add to `flowersbytiana-papermod/scripts/test_publish_album_updates.py` (add this import at the top alongside the existing one):

```python
from publish_album_updates import Action, compute_plan, list_remote_objects
```

Append these test classes:

```python
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
```

Also add `import tempfile` and `from publish_album_updates import md5_of_file` at the top if not already present from Task 1 (they are — `md5_of_file` was imported in Task 1's import block; `tempfile` too).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd flowersbytiana-papermod/scripts && python3 -m unittest test_publish_album_updates -v`
Expected: FAIL / ImportError — `Action`, `compute_plan`, `list_remote_objects` don't exist yet.

- [ ] **Step 3: Write the implementation**

Add to the top of `flowersbytiana-papermod/scripts/publish_album_updates.py`, alongside the existing imports:

```python
from dataclasses import dataclass
```

Append to `flowersbytiana-papermod/scripts/publish_album_updates.py`:

```python
@dataclass
class Action:
    kind: str  # "new" | "changed" | "unchanged"
    filename: str
    index: int
    local_path: Path


def list_remote_objects(s3_client, bucket: str, prefix: str) -> dict[str, str]:
    result = {}
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            filename = obj["Key"][len(prefix):]
            result[filename] = obj["ETag"].strip('"')
    return result


def compute_plan(
    slug: str, local_files: dict[str, Path], remote_etags: dict[str, str]
) -> list[Action]:
    indexed = sorted(
        (canonical_index(filename, slug), filename, path)
        for filename, path in local_files.items()
    )

    remote_max = max(
        (canonical_index(f, slug) for f in remote_etags), default=-1
    )

    actions = []
    for index, filename, path in indexed:
        remote_etag = remote_etags.get(filename)
        if remote_etag is None:
            if index != remote_max + 1:
                raise ValueError(
                    "%s: %s would be a new photo but index %d is not the "
                    "next available index (%d); check for a gap in "
                    "staging/%s/" % (slug, filename, index, remote_max + 1, slug)
                )
            actions.append(Action("new", filename, index, path))
            remote_max = index
        else:
            local_md5 = md5_of_file(path)
            kind = "unchanged" if local_md5 == remote_etag else "changed"
            actions.append(Action(kind, filename, index, path))
    return actions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd flowersbytiana-papermod/scripts && python3 -m unittest test_publish_album_updates -v`
Expected: PASS, all tests including `TestListRemoteObjects` and `TestComputePlan`.

- [ ] **Step 5: Commit**

```bash
cd flowersbytiana-papermod
git add scripts/publish_album_updates.py scripts/test_publish_album_updates.py
git commit -m "Add remote listing and core diff/plan algorithm to publish_album_updates.py"
```

---

### Task 3: Front-matter title lookup and index.md appender

**Files:**
- Modify: `flowersbytiana-papermod/scripts/publish_album_updates.py`
- Modify: `flowersbytiana-papermod/scripts/test_publish_album_updates.py`

**Interfaces:**
- Consumes: `Action` dataclass (Task 2)
- Produces: `album_title(index_md_path: Path) -> str`
- Produces: `append_img_lines(index_md_path: Path, slug: str, title: str, new_actions: list[Action]) -> None`

- [ ] **Step 1: Write the failing tests**

Add to the top of `flowersbytiana-papermod/scripts/test_publish_album_updates.py`:

```python
from publish_album_updates import album_title, append_img_lines
```

Append this test class:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd flowersbytiana-papermod/scripts && python3 -m unittest test_publish_album_updates -v`
Expected: FAIL / ImportError — `album_title`, `append_img_lines` don't exist yet.

- [ ] **Step 3: Write the implementation**

Add to the top of `flowersbytiana-papermod/scripts/publish_album_updates.py`, alongside the existing imports:

```python
import yaml
```

Append to `flowersbytiana-papermod/scripts/publish_album_updates.py`:

```python
def album_title(index_md_path: Path) -> str:
    text = index_md_path.read_text()
    _, front_matter_text, _ = text.split("---", 2)
    front_matter = yaml.safe_load(front_matter_text)
    return front_matter["title"]


def append_img_lines(
    index_md_path: Path, slug: str, title: str, new_actions: list[Action]
) -> None:
    if not new_actions:
        return
    text = index_md_path.read_text()
    lines = [
        '{{< img "%s/%s" "%s %03d" >}}' % (slug, action.filename, title, action.index)
        for action in new_actions
    ]
    addition = "\n\n" + "\n\n".join(lines) + "\n"
    index_md_path.write_text(text.rstrip("\n") + addition)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd flowersbytiana-papermod/scripts && python3 -m unittest test_publish_album_updates -v`
Expected: PASS, all tests including `TestAlbumTitle` and `TestAppendImgLines`.

- [ ] **Step 5: Commit**

```bash
cd flowersbytiana-papermod
git add scripts/publish_album_updates.py scripts/test_publish_album_updates.py
git commit -m "Add front-matter title lookup and index.md appender to publish_album_updates.py"
```

---

### Task 4: CLI wiring, dry-run/apply orchestration, and .env loading

**Files:**
- Modify: `flowersbytiana-papermod/scripts/publish_album_updates.py`
- Modify: `flowersbytiana-papermod/scripts/test_publish_album_updates.py`

**Interfaces:**
- Consumes: everything from Tasks 1-3 (`normalize_local_filenames`, `local_canonical_files`, `list_remote_objects`, `compute_plan`, `album_title`, `append_img_lines`, `Action`)
- Produces: `load_env(env_path: Path) -> dict[str, str]`
- Produces: `build_s3_client(env: dict[str, str], endpoint_url: str) -> Any` (thin boto3 wrapper, not unit-tested against real R2)
- Produces: `process_album(s3_client, bucket: str, staging_dir: Path, content_dir: Path, slug: str, apply: bool) -> list[Action]`
- Produces: `main() -> int` (CLI entry point)

- [ ] **Step 1: Write the failing tests**

Add to the top of `flowersbytiana-papermod/scripts/test_publish_album_updates.py`:

```python
from publish_album_updates import load_env, process_album
```

Append these test classes:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd flowersbytiana-papermod/scripts && python3 -m unittest test_publish_album_updates -v`
Expected: FAIL / ImportError — `load_env`, `process_album` don't exist yet.

- [ ] **Step 3: Write the implementation**

Add to the top of `flowersbytiana-papermod/scripts/publish_album_updates.py`, alongside the existing imports:

```python
import argparse
import sys

import boto3
```

Append to `flowersbytiana-papermod/scripts/publish_album_updates.py`:

```python
R2_ACCOUNT_ID = "7b640a8e821a63b945fa89a9c3d09363"
R2_ENDPOINT = "https://%s.r2.cloudflarestorage.com" % R2_ACCOUNT_ID
R2_BUCKET = "flowersbytiana"


def load_env(env_path: Path) -> dict[str, str]:
    env = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def build_s3_client(env: dict[str, str], endpoint_url: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=env["ACCESS_KEY_ID"],
        aws_secret_access_key=env["SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def process_album(
    s3_client,
    bucket: str,
    staging_dir: Path,
    content_dir: Path,
    slug: str,
    apply: bool,
) -> list[Action]:
    staging_slug_dir = staging_dir / slug
    normalize_local_filenames(staging_slug_dir, slug)
    local_files = local_canonical_files(staging_slug_dir, slug)
    remote_etags = list_remote_objects(s3_client, bucket, "%s/" % slug)
    actions = compute_plan(slug, local_files, remote_etags)

    for action in actions:
        label = {"new": "NEW", "changed": "CHANGED", "unchanged": "unchanged"}[
            action.kind
        ]
        print("  [%s] %s/%s" % (label, slug, action.filename))

    if not apply:
        return actions

    for action in actions:
        if action.kind in ("new", "changed"):
            key = "%s/%s" % (slug, action.filename)
            s3_client.upload_file(str(action.local_path), bucket, key)

    new_actions = [a for a in actions if a.kind == "new"]
    if new_actions:
        index_md_path = content_dir / slug / "index.md"
        title = album_title(index_md_path)
        append_img_lines(index_md_path, slug, title, new_actions)

    return actions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("staging_dir", type=Path)
    parser.add_argument("slugs", nargs="*")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    env = load_env(repo_root / ".env")
    s3_client = build_s3_client(env, R2_ENDPOINT)

    slugs = args.slugs or sorted(
        p.name for p in args.staging_dir.iterdir() if p.is_dir()
    )
    content_dir = repo_root / "content" / "albums"

    had_error = False
    for slug in slugs:
        print("%s:" % slug)
        try:
            process_album(
                s3_client, R2_BUCKET, args.staging_dir, content_dir, slug, args.apply
            )
        except ValueError as e:
            print("  ERROR: %s" % e)
            had_error = True

    if not args.apply:
        print("\nDry run only -- rerun with --apply to upload and update index.md.")

    return 1 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd flowersbytiana-papermod/scripts && python3 -m unittest test_publish_album_updates -v`
Expected: PASS, full suite including `TestLoadEnv` and `TestProcessAlbum`.

- [ ] **Step 5: Commit**

```bash
cd flowersbytiana-papermod
git add scripts/publish_album_updates.py scripts/test_publish_album_updates.py
git commit -m "Add CLI, dry-run/apply orchestration, and .env loading to publish_album_updates.py"
```

---

### Task 5: Publish the pending kingfisher and dragons updates

**Files:**
- None created/modified by this task's own steps beyond what the script itself writes (`content/albums/dragons/index.md`) and uploads (R2 objects). This task exercises the tool built in Tasks 1-4 against the real pending files described in the spec: 5 replacement photos in `staging/kingfisher/` (`0.jpg`, `1.jpg`, `2.jpg`, `3.jpg`, `7.jpg`) and 2 new photos in `staging/dragons/` (`dragons-008.jpg`, `dragons-009.jpg`, already canonically named).

**Interfaces:**
- Consumes: `main()` via the CLI (Task 4).

- [ ] **Step 1: Dry run against both albums**

Run: `cd flowersbytiana-papermod && python3 scripts/publish_album_updates.py ../flowersbytiana-to-upload-2026/staging kingfisher dragons`

Expected output: under `kingfisher:`, five lines reading `[CHANGED] kingfisher/kingfisher-000.jpg` (and `-001`, `-002`, `-003`, `-007`) and four `[unchanged]` lines for `-004`, `-005`, `-006`, `-008`; under `dragons:`, eight `[unchanged]` lines for `-000` through `-007` and two `[NEW]` lines for `-008` and `-009`. Ends with the "Dry run only" notice. Exit code 0.

If the output doesn't match this (e.g. an `ERROR:` line, or different files marked changed/new), stop and re-examine `staging/kingfisher/` and `staging/dragons/` before proceeding — do not apply against unexpected state.

- [ ] **Step 2: Apply**

Run: `cd flowersbytiana-papermod && python3 scripts/publish_album_updates.py ../flowersbytiana-to-upload-2026/staging kingfisher dragons --apply`

Expected: same classification output as the dry run, no "Dry run only" notice, exit code 0.

- [ ] **Step 3: Verify R2 content**

Run:
```bash
cd flowersbytiana-papermod
set -a; source .env; set +a
python3 -c "
import boto3, os
s3 = boto3.client('s3',
    endpoint_url='https://7b640a8e821a63b945fa89a9c3d09363.r2.cloudflarestorage.com',
    aws_access_key_id=os.environ['ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['SECRET_ACCESS_KEY'],
    region_name='auto',
)
for prefix in ('kingfisher/', 'dragons/'):
    print(prefix)
    for obj in s3.list_objects_v2(Bucket='flowersbytiana', Prefix=prefix)['Contents']:
        print(' ', obj['Key'], obj['Size'])
"
```
Expected: `kingfisher/` still has exactly 9 objects (`-000` through `-008`), with `-000`, `-001`, `-002`, `-003`, `-007` now showing the file sizes matching the new local files (compare against `ls -la ../flowersbytiana-to-upload-2026/staging/kingfisher/`). `dragons/` now has exactly 10 objects (`-000` through `-009`).

- [ ] **Step 4: Verify staging/ was normalized**

Run: `ls staging/kingfisher/ staging/dragons/` (from `../flowersbytiana-to-upload-2026`)
Expected: `staging/kingfisher/` contains only `kingfisher-000.jpg` through `kingfisher-008.jpg` (9 files, no more bare `0.jpg`/`1.jpg`/etc. — they were renamed in place by `normalize_local_filenames`). `staging/dragons/` contains `dragons-000.jpg` through `dragons-009.jpg` (10 files).

- [ ] **Step 5: Review the index.md change**

Run: `cd flowersbytiana-papermod && git diff content/albums/dragons/index.md`
Expected: two new lines added at the end, `{{< img "dragons/dragons-008.jpg" "Dragons 008" >}}` and `{{< img "dragons/dragons-009.jpg" "Dragons 009" >}}`, matching the blank-line-separated format of the existing entries. `content/albums/kingfisher/index.md` should show no diff (replacements don't change it).

- [ ] **Step 6: Report to Pavel, do not push**

Show him the `git diff --stat` and the dragons `index.md` diff. Per the established convention (human gate before anything goes live), wait for his explicit go-ahead before `git add content/albums/dragons/index.md && git commit` and before `git push`.
