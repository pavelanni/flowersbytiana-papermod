# Album Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all live photo albums on flowersbytiana.com with 17 new albums translated from a Russian-named source directory, uploaded flat to the existing Cloudflare R2 bucket, and rendered via the site's existing `{{< img >}}` shortcode.

**Architecture:** Three standalone Python scripts, run in sequence, connected only through a `staging/manifest.json` file and a `staging/<slug>/<slug>-NNN.ext` directory tree: (1) `build_staging.py` reads the messy Russian source folders and produces a clean, ordered, filtered local staging copy; (2) `delete_old_r2_objects.py` + `upload_to_r2.py` empty and repopulate the R2 bucket from that staging copy; (3) `generate_albums.py` regenerates the Hugo `content/albums/` tree from the same staging copy. Each script is independently runnable and its output independently inspectable — no shared runtime state, no library code, so each task is testable on its own.

**Tech Stack:** Python 3 (stdlib only: `os`, `json`, `shutil`, `subprocess`, `unicodedata`, `mimetypes`), `npx wrangler` CLI (already authenticated), Hugo (already installed, used for local preview via `hugo server`).

## Global Constraints

- Full replacement, not incremental: all 8 currently-live albums (`iris, lilies, lotus, narcissus, orchid, peonies, plum, roses`) are removed; `roses` and `lilies` are not recreated (per spec, explicitly confirmed with user).
- Every source folder's `exclude/` subfolder is skipped entirely, everywhere.
- Any file with a long camera-style name (heuristic: stem longer than 6 characters, or containing any character that isn't a digit or `.`) is skipped, even outside `exclude/` — this is how `.HEIC`/`IMG*` files get dropped.
- `коты другие` (cats-other) is skipped entirely — every one of its 6 files is a long camera-style name.
- `13 цапли` (herons) has no files of its own; its `другие цапли` subfolder supplies all 15 herons photos.
- Filenames encode intentional order as floating-point numbers (`1`, `1.1`, `1.2`, `2`...) — sort by `float()` of the filename stem, never lexicographically.
- Folder names on disk are NFD-normalized Unicode; any code comparing against literal Cyrillic strings must normalize both sides with `unicodedata.normalize('NFC', ...)`.
- R2 layout is flat: `<slug>/<slug>-NNN.ext` (e.g. `orchids/orchids-000.jpg`).
- Weights are spaced by 10 (`10, 20, 30...170`) so future albums can be inserted without renumbering.
- The R2 bucket (`flowersbytiana`) and its `cdn.flowersbytiana.com` custom domain binding must never be deleted/recreated — only individual objects are deleted.
- Push to the git remote only happens with explicit user go-ahead — this plan stops at a local commit.
- Exact album table (slug, source folder, weight, expected photo count) is reproduced in Task 1 below; it is the source of truth for every later task.

---

## Task 1: Build the staging script

**Files:**
- Create: `flowersbytiana-to-upload-2026/build_staging.py`

**Interfaces:**
- Produces: `flowersbytiana-to-upload-2026/staging/<slug>/<slug>-NNN.<ext>` files (renamed, ordered, filtered copies) and `flowersbytiana-to-upload-2026/staging/manifest.json`, a JSON array where each element is `{"weight": int, "slug": str, "source_folder": str, "photo_count": int, "files": [str, ...]}` (`files` is the list of staged filenames in final order, e.g. `["orchids-000.jpg", "orchids-001.jpg", ...]`). This manifest is the sole interface Tasks 3, 4, and 5 consume — they never touch the Russian-named source folders directly.

- [ ] **Step 1: Write `build_staging.py`**

```python
#!/usr/bin/env python3
"""Build a local staging copy of renamed, ordered, filtered photos ready
for R2 upload. See docs/superpowers/specs/2026-08-02-album-refresh-design.md
in flowersbytiana-papermod for the full design."""
import json
import os
import shutil
import unicodedata

SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
STAGING_DIR = os.path.join(SOURCE_DIR, "staging")

# (weight, slug, source folder name, optional subfolder to descend into)
ALBUMS = [
    (10, "orchids", "01 орхидеи", None),
    (20, "meihua", "02 мейхуа", None),
    (30, "bamboo", "03 бамбук", None),
    (40, "chrysanthemums", "04 хризантемы", None),
    (50, "lotus", "05 лотосы", None),
    (60, "kingfisher", "06 кингфишер", None),
    (70, "iris", "07 ирисы", None),
    (80, "peonies", "08 пионы", None),
    (90, "hydrangea", "09 гортензия", None),
    (100, "narcissus", "10 нарциссы", None),
    (110, "dragons", "11 драконы", None),
    (120, "gongbi-mogufa", "12 гунби-могуфа", None),
    (130, "herons", "13 цапли", "другие цапли"),
    (140, "colored-lotus", "14 лотосы цветные", None),
    (150, "cats", "15 коты", None),
    (160, "fish", "16 рыбы", None),
    (170, "roosters", "21 петухи", None),
]

# Expected counts per docs/superpowers/specs/2026-08-02-album-refresh-design.md
# table. Mismatches almost always mean a filter or mapping bug, not real
# source data drift, so this is treated as a hard assertion.
EXPECTED_COUNTS = {
    "orchids": 10, "meihua": 6, "bamboo": 5, "chrysanthemums": 9,
    "lotus": 22, "kingfisher": 9, "iris": 10, "peonies": 8,
    "hydrangea": 5, "narcissus": 6, "dragons": 8, "gongbi-mogufa": 29,
    "herons": 15, "colored-lotus": 7, "cats": 9, "fish": 11, "roosters": 4,
}


def nfc(s):
    return unicodedata.normalize("NFC", s)


def is_long_camera_name(filename):
    name = os.path.splitext(filename)[0]
    return len(name) > 6 or not all(c.isdigit() or c == "." for c in name)


def sort_key(filename):
    return float(os.path.splitext(filename)[0])


def find_source_folder(expected_name):
    for entry in os.listdir(SOURCE_DIR):
        full = os.path.join(SOURCE_DIR, entry)
        if os.path.isdir(full) and nfc(entry) == nfc(expected_name):
            return full
    raise FileNotFoundError(f"source folder not found: {expected_name!r}")


def collect_photos(folder_path, subfolder):
    target = folder_path
    if subfolder is not None:
        target = None
        for entry in os.listdir(folder_path):
            full = os.path.join(folder_path, entry)
            if os.path.isdir(full) and nfc(entry) == nfc(subfolder):
                target = full
                break
        if target is None:
            raise FileNotFoundError(f"subfolder not found: {subfolder!r} in {folder_path!r}")

    files = []
    for entry in os.listdir(target):
        full = os.path.join(target, entry)
        if not os.path.isfile(full):
            continue
        if is_long_camera_name(entry):
            continue
        files.append(entry)

    files.sort(key=sort_key)
    return target, files


def build():
    if os.path.exists(STAGING_DIR):
        shutil.rmtree(STAGING_DIR)
    os.makedirs(STAGING_DIR)

    manifest = []
    for weight, slug, folder_name, subfolder in ALBUMS:
        folder_path = find_source_folder(folder_name)
        source_dir, files = collect_photos(folder_path, subfolder)

        album_staging_dir = os.path.join(STAGING_DIR, slug)
        os.makedirs(album_staging_dir)

        staged_files = []
        for i, filename in enumerate(files):
            ext = os.path.splitext(filename)[1].lower()
            staged_name = f"{slug}-{i:03d}{ext}"
            shutil.copy2(
                os.path.join(source_dir, filename),
                os.path.join(album_staging_dir, staged_name),
            )
            staged_files.append(staged_name)

        expected = EXPECTED_COUNTS[slug]
        assert len(staged_files) == expected, (
            f"{slug}: expected {expected} photos, got {len(staged_files)}"
        )

        manifest.append({
            "weight": weight,
            "slug": slug,
            "source_folder": folder_name,
            "photo_count": len(staged_files),
            "files": staged_files,
        })
        print(f"{slug:20s} weight={weight:3d}  {len(staged_files):3d} photos staged")

    with open(os.path.join(STAGING_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    total = sum(a["photo_count"] for a in manifest)
    assert total == 173, f"expected 173 total photos, got {total}"
    print(f"\nTotal staged: {total} photos across {len(manifest)} albums")
    return manifest


if __name__ == "__main__":
    build()
```

- [ ] **Step 2: Run it**

Run: `cd flowersbytiana-to-upload-2026 && python3 build_staging.py`

Expected: 17 lines, one per album, each showing the exact `photo_count` from the `EXPECTED_COUNTS` table in Step 1, ending with `Total staged: 173 photos across 17 albums`. If an `AssertionError` fires, the mapping/filename filter is wrong for that album — inspect the named folder before touching the assertion.

- [ ] **Step 3: Verify staging output on disk**

Run: `find flowersbytiana-to-upload-2026/staging -type f -name "*.json" -o -type f | sort | head -20` and `cat flowersbytiana-to-upload-2026/staging/manifest.json | python3 -m json.tool | head -30`

Expected: `staging/<slug>/<slug>-000.<ext>` style filenames, and the manifest is valid JSON with 17 entries.

- [ ] **Step 4: No commit needed**

`flowersbytiana-to-upload-2026` is not a git repository and this plan doesn't create one — the scripts here are one-off migration tooling, not part of the deployed site. Nothing to commit for this task; the durable output that matters (the Hugo repo) gets committed in Task 7.

---

## Task 2: Manual review checkpoint (human gate, no code)

**Files:** none — this is a pause point, not an implementation step.

- [ ] **Step 1: Stop and wait for explicit user confirmation**

Show the user (or have them browse) `flowersbytiana-to-upload-2026/staging/`, in particular the first photo of each album (`find staging -name "*-000.*"`) and the total per-album counts printed in Task 1. Do not proceed to Task 3 until the user explicitly confirms the staged set looks right — this is the last point before anything destructive touches the live R2 bucket.

---

## Task 3: Delete old R2 objects

**Files:**
- Create: `flowersbytiana-to-upload-2026/delete_old_r2_objects.py`

**Interfaces:**
- Consumes: `../flowersbytiana-to-upload/image-list.txt` (one relative path per line, e.g. `./gongbi/orchids/orchids-000.jpg`).
- Produces: nothing on disk — deletes objects from the live `flowersbytiana` R2 bucket via `npx wrangler r2 object delete`.

- [ ] **Step 1: Write `delete_old_r2_objects.py`**

```python
#!/usr/bin/env python3
"""Delete every object listed in the old image-list.txt manifest from the
flowersbytiana R2 bucket. Leaves the bucket and its custom domain binding
intact -- only objects are removed."""
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE_LIST = os.path.join(SCRIPT_DIR, "..", "flowersbytiana-to-upload", "image-list.txt")
BUCKET = "flowersbytiana"
HUGO_REPO = os.path.join(SCRIPT_DIR, "..", "flowersbytiana-papermod")


def load_keys():
    keys = []
    with open(IMAGE_LIST, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            keys.append(line.lstrip("./"))
    return keys


def main():
    keys = load_keys()
    print(f"Deleting {len(keys)} objects from bucket {BUCKET}...")
    failures = []
    for i, key in enumerate(keys, 1):
        result = subprocess.run(
            ["npx", "wrangler", "r2", "object", "delete", f"{BUCKET}/{key}", "--remote", "-y"],
            cwd=HUGO_REPO,
            capture_output=True,
            text=True,
        )
        status = "OK" if result.returncode == 0 else "FAIL"
        print(f"[{i}/{len(keys)}] {status} {key}")
        if result.returncode != 0:
            failures.append((key, result.stderr.strip()))

    print(f"\nDone. {len(keys) - len(failures)} deleted, {len(failures)} failures.")
    for key, err in failures:
        print(f"  FAILED {key}: {err}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `cd flowersbytiana-to-upload-2026 && python3 delete_old_r2_objects.py`

Expected: 429 `OK` lines (one per line in `image-list.txt`), ending with `429 deleted, 0 failures.` Deleting a key that's already gone also returns success in R2, so re-running this script is safe if it's interrupted partway.

- [ ] **Step 3: Spot-check deletion**

Run: `curl -sI https://cdn.flowersbytiana.com/flowers/orchids/orchids-000.jpg | head -1`

Expected: `HTTP/2 404` (object gone). If it still returns `200`, the delete didn't take — check the failures list from Step 2.

- [ ] **Step 4: Commit**

No repo-tracked files changed by this task (it only mutates remote R2 state); nothing to commit beyond the script itself, which was already committed (or intentionally left untracked) in Task 1.

---

## Task 4: Upload staged files to R2

**Files:**
- Create: `flowersbytiana-to-upload-2026/upload_to_r2.py`

**Interfaces:**
- Consumes: `flowersbytiana-to-upload-2026/staging/manifest.json` and the `staging/<slug>/<slug>-NNN.<ext>` files it references (from Task 1).
- Produces: nothing on disk — uploads objects to the live `flowersbytiana` R2 bucket at key `<slug>/<slug>-NNN.<ext>`.

- [ ] **Step 1: Write `upload_to_r2.py`**

```python
#!/usr/bin/env python3
"""Upload every staged photo to the flowersbytiana R2 bucket, flat under
<slug>/<slug>-NNN.ext, per staging/manifest.json built by build_staging.py."""
import json
import mimetypes
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STAGING_DIR = os.path.join(SCRIPT_DIR, "staging")
BUCKET = "flowersbytiana"
HUGO_REPO = os.path.join(SCRIPT_DIR, "..", "flowersbytiana-papermod")


def main():
    with open(os.path.join(STAGING_DIR, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)

    total = sum(a["photo_count"] for a in manifest)
    uploaded = 0
    failures = []

    for album in manifest:
        slug = album["slug"]
        for filename in album["files"]:
            key = f"{slug}/{filename}"
            local_path = os.path.abspath(os.path.join(STAGING_DIR, slug, filename))
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            result = subprocess.run(
                [
                    "npx", "wrangler", "r2", "object", "put", f"{BUCKET}/{key}",
                    f"--file={local_path}",
                    f"--content-type={content_type}",
                    "--remote", "-y",
                ],
                cwd=HUGO_REPO,
                capture_output=True,
                text=True,
            )
            uploaded += 1
            status = "OK" if result.returncode == 0 else "FAIL"
            print(f"[{uploaded}/{total}] {status} {key}")
            if result.returncode != 0:
                failures.append((key, result.stderr.strip()))

    print(f"\nDone. {uploaded - len(failures)}/{total} uploaded, {len(failures)} failures.")
    for key, err in failures:
        print(f"  FAILED {key}: {err}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `cd flowersbytiana-to-upload-2026 && python3 upload_to_r2.py`

Expected: 173 `OK` lines, ending with `173/173 uploaded, 0 failures.`

- [ ] **Step 3: Spot-check three uploads across different albums**

Run:
```bash
curl -sI https://cdn.flowersbytiana.com/orchids/orchids-000.jpg | head -1
curl -sI https://cdn.flowersbytiana.com/gongbi-mogufa/gongbi-mogufa-028.jpg | head -1
curl -sI https://cdn.flowersbytiana.com/roosters/roosters-003.jpg | head -1
```

Expected: all three return `HTTP/2 200`.

- [ ] **Step 4: Commit**

Nothing repo-tracked changes here either (remote-only); no commit needed beyond the script already committed in Task 1's commit step.

---

## Task 5: Regenerate Hugo album content

**Files:**
- Create: `flowersbytiana-to-upload-2026/generate_albums.py`
- Delete (via script): `flowersbytiana-papermod/content/albums/{iris,lilies,lotus,narcissus,orchid,peonies,plum,roses}/`
- Create (via script): `flowersbytiana-papermod/content/albums/{orchids,meihua,bamboo,chrysanthemums,lotus,kingfisher,iris,peonies,hydrangea,narcissus,dragons,gongbi-mogufa,herons,colored-lotus,cats,fish,roosters}/index.md` and matching `<slug>-cover.<ext>` files.

**Interfaces:**
- Consumes: `flowersbytiana-to-upload-2026/staging/manifest.json` and the staged files (from Task 1). Reads `manifest[i]["weight"]`, `manifest[i]["slug"]`, `manifest[i]["files"]`.
- Produces: `content/albums/<slug>/index.md` (Hugo page bundle front matter + `{{< img "<slug>/<filename>" "<alt>" >}}` body) and `content/albums/<slug>/<slug>-cover.<ext>` (copied from that album's first staged photo).

- [ ] **Step 1: Write `generate_albums.py`**

```python
#!/usr/bin/env python3
"""Regenerate content/albums/ in the Hugo repo from staging/manifest.json.
Deletes the 8 old albums entirely and writes 17 new ones."""
import json
import os
import shutil

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STAGING_DIR = os.path.join(SCRIPT_DIR, "staging")
CONTENT_ALBUMS_DIR = os.path.join(
    SCRIPT_DIR, "..", "flowersbytiana-papermod", "content", "albums"
)

OLD_ALBUMS = ["iris", "lilies", "lotus", "narcissus", "orchid", "peonies", "plum", "roses"]

TITLES = {
    "orchids": "Orchids", "meihua": "Meihua", "bamboo": "Bamboo",
    "chrysanthemums": "Chrysanthemums", "lotus": "Lotus", "kingfisher": "Kingfisher",
    "iris": "Iris", "peonies": "Peonies", "hydrangea": "Hydrangea",
    "narcissus": "Narcissus", "dragons": "Dragons", "gongbi-mogufa": "Gongbi Mogufa",
    "herons": "Herons", "colored-lotus": "Colored Lotus", "cats": "Cats",
    "fish": "Fish", "roosters": "Roosters",
}

CATEGORY_TAGS = {
    "orchids": "flowers", "meihua": "flowers", "bamboo": "flowers",
    "chrysanthemums": "flowers", "lotus": "flowers", "kingfisher": "birds",
    "iris": "flowers", "peonies": "flowers", "hydrangea": "flowers",
    "narcissus": "flowers", "dragons": "animals", "gongbi-mogufa": "technique",
    "herons": "birds", "colored-lotus": "flowers", "cats": "animals",
    "fish": "animals", "roosters": "birds",
}

DATE = "2026-08-02T12:00:00-04:00"


def main():
    with open(os.path.join(STAGING_DIR, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)

    for old_slug in OLD_ALBUMS:
        old_dir = os.path.join(CONTENT_ALBUMS_DIR, old_slug)
        if os.path.isdir(old_dir):
            shutil.rmtree(old_dir)
            print(f"removed old album: {old_slug}")

    for album in manifest:
        slug = album["slug"]
        weight = album["weight"]
        files = album["files"]
        title = TITLES[slug]
        category = CATEGORY_TAGS[slug]

        album_dir = os.path.join(CONTENT_ALBUMS_DIR, slug)
        os.makedirs(album_dir, exist_ok=True)

        cover_filename = files[0]
        cover_ext = os.path.splitext(cover_filename)[1]
        cover_name = f"{slug}-cover{cover_ext}"
        shutil.copy2(
            os.path.join(STAGING_DIR, slug, cover_filename),
            os.path.join(album_dir, cover_name),
        )

        lines = [
            "---",
            f"date: '{DATE}'",
            "draft: false",
            f"title: '{title}'",
            f"weight: {weight}",
            "tags:",
            f"  - {slug}",
            f"  - {category}",
            "cover:",
            f'  image: "{cover_name}"',
            "---",
            "",
        ]
        for i, filename in enumerate(files):
            alt = f"{title} {i:03d}"
            lines.append(f'{{{{< img "{slug}/{filename}" "{alt}" >}}}}')
            lines.append("")

        with open(os.path.join(album_dir, "index.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        print(f"wrote album: {slug} ({len(files)} photos, weight={weight})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `cd flowersbytiana-to-upload-2026 && python3 generate_albums.py`

Expected: 8 `removed old album: ...` lines, then 17 `wrote album: ...` lines.

- [ ] **Step 3: Verify the resulting content tree**

Run: `ls flowersbytiana-papermod/content/albums/`

Expected: exactly 17 directories — `bamboo cats chrysanthemums colored-lotus dragons fish gongbi-mogufa herons hydrangea iris kingfisher lotus meihua narcissus orchids peonies roosters` — and none of `lilies orchid plum roses`.

Run: `cat flowersbytiana-papermod/content/albums/orchids/index.md`

Expected: valid front matter with `weight: 10`, `title: 'Orchids'`, a `cover.image` pointing at an existing `orchids-cover.jpg` in the same directory, and 10 `{{< img "orchids/orchids-NNN.jpg" ... >}}` lines.

---

## Task 6: Local Hugo build check

**Files:** none created — verification only.

- [ ] **Step 1: Build the site locally**

Run: `cd flowersbytiana-papermod && hugo --minify -D 2>&1 | tail -30`

Expected: build completes with no `ERROR` lines and reports 17 album pages under `public/albums/`.

- [ ] **Step 2: Spot-check rendered HTML for image URLs**

Run: `grep -o 'https://cdn.flowersbytiana.com/[^"]*' flowersbytiana-papermod/public/albums/gongbi-mogufa/index.html | head -5`

Expected: 5 URLs of the form `https://cdn.flowersbytiana.com/gongbi-mogufa/gongbi-mogufa-00N.jpg`.

- [ ] **Step 3: Confirm the albums list page shows the new order**

Run: `grep -o '<a[^>]*href="/albums/[a-z-]*/"' flowersbytiana-papermod/public/albums/index.html`

Expected: 17 links, in the weight order from the table (orchids, meihua, bamboo, chrysanthemums, lotus, kingfisher, iris, peonies, hydrangea, narcissus, dragons, gongbi-mogufa, herons, colored-lotus, cats, fish, roosters).

---

## Task 7: Commit the Hugo repo changes

**Files:**
- Modify: `flowersbytiana-papermod/content/albums/` (delete 8, add 17, per Task 5)

- [ ] **Step 1: Review the diff**

Run: `cd flowersbytiana-papermod && git status && git add content/albums && git status`

Expected: 8 old album directories staged as deleted, 17 new album directories staged as added (each with an `index.md` and one `<slug>-cover.<ext>`).

- [ ] **Step 2: Commit**

```bash
cd flowersbytiana-papermod
git commit -m "$(cat <<'EOF'
Replace all albums with new set translated from Tiana's source folders

Full replacement per docs/superpowers/specs/2026-08-02-album-refresh-design.md:
17 new albums (orchids, meihua, bamboo, chrysanthemums, lotus, kingfisher,
iris, peonies, hydrangea, narcissus, dragons, gongbi-mogufa, herons,
colored-lotus, cats, fish, roosters), replacing the previous 8. Images
uploaded to R2 under the new flat <slug>/<slug>-NNN.ext layout.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Confirm commit and stop — do not push**

Run: `git log --oneline -3`

Expected: the new commit on top. **Do not run `git push`** — that deploys the live site and requires the user's explicit go-ahead, separate from this plan.

---

## Self-Review Notes

- **Spec coverage:** every section of `2026-08-02-album-refresh-design.md` maps to a task — source quirks (Task 1), album mapping/weights (Task 1), R2 layout (Tasks 3–4), process staging/review gate (Tasks 1–2), R2 wipe (Task 3), upload (Task 4), Hugo content rewrite (Task 5), local test (Task 6), commit-not-push (Task 7).
- **Placeholder scan:** no TBD/TODO; all scripts are complete, runnable code with the full album table inlined.
- **Type/name consistency:** `manifest.json` schema (`weight`, `slug`, `source_folder`, `photo_count`, `files`) is identical across the producer (Task 1) and both consumers (Tasks 4 and 5); slug strings match exactly between `ALBUMS` (Task 1), `TITLES`/`CATEGORY_TAGS` (Task 5), and the R2 key prefixes (Tasks 3–4).
