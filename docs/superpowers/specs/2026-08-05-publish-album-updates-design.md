# Publish Album Updates — Design

Date: 2026-08-05

## Goal

A reusable script for the recurring "Tiana sent a few updated/new photos for
an existing album" request (first instance: 5 replacement photos for
`kingfisher`, 2 new photos for `dragons`). Replaces one-off manual R2
uploads and `index.md` edits with a single command that's safe to rerun.

This is a smaller, lighter-weight companion to the full album-refresh
pipeline (`build_staging.py` / `upload_to_r2.py` / `generate_albums.py`,
documented in `2026-08-02-album-refresh-design.md`) — that pipeline does a
full destructive rebuild of every album from a fresh Russian-folder source;
this script does incremental touch-ups to albums that already exist on the
live site.

## Non-goals (v1)

- Deleting photos or albums.
- Reordering photos within an album (decimal-insertion filenames are the
  existing mechanism for that, per the `for-tiana` convention).
- Creating brand-new albums (that's `generate_albums.py`'s job).
- Keeping `staging/manifest.json` up to date — it's a snapshot from the
  original migration; this script uses R2 itself as the source of truth
  and doesn't read or write the manifest.
- Auto-committing or pushing to git — matches the existing convention of a
  human gate before anything goes live-visible.

## Trigger convention

Drop files directly into `staging/<slug>/`, using either form:

- **Short plain-digit name** (`0.jpg`, `7.jpg`, `12.jpg`) — shorthand for
  "this is photo index N in this album." The script renames it in place to
  the canonical `<slug>-NNN.ext` form (zero-padded to 3 digits, matching
  the existing convention) before processing.
- **Already-canonical name** (`dragons-008.jpg`) — used as-is.

No other signal is needed — whether a given index is a *replacement* or a
*new append* is derived automatically by comparing against what's actually
in R2 (see Detection algorithm), not declared by the user.

## Detection algorithm

Per album slug, in ascending index order:

1. List remote objects under `<slug>/` directly from R2 (via boto3 —
   `ListObjectsV2`, which the S3-compatible endpoint supports even though
   `wrangler`'s CLI never exposed a list command) → `{filename: etag}`.
2. Normalize local filenames in `staging/<slug>/` to canonical form (see
   Trigger convention above).
3. For each canonical local file, compute its MD5 and compare to R2's
   ETag:
   - **Key not in R2** → new photo. Only valid if its index equals
     `current_max_remote_index + 1` (no gaps allowed); the running "max"
     updates as each new index in the batch is processed, so multiple new
     photos in one run (e.g. dragons 008 then 009) chain correctly. A
     local file whose index skips ahead of `max + 1` is an error — the
     script stops and reports the gap rather than guessing.
   - **Key in R2, MD5 differs from ETag** → changed photo (a replacement).
   - **Key in R2, MD5 matches ETag** → unchanged; skipped. This makes the
     script idempotent — rerunning it after a successful run reports
     nothing to do.

Note: ETag-as-MD5 only holds for non-multipart uploads. All photos in this
project are well under R2/S3's multipart threshold (default 8MB), so this
holds in practice; the script does not need to handle the multipart-ETag
case.

## Actions taken

- **New photo:** upload to R2 (`<slug>/<slug>-NNN.ext`), then append one
  `{{< img "<slug>/<slug>-NNN.ext" "<Title> NNN" >}}` line to
  `content/albums/<slug>/index.md`, matching the exact formatting already
  used there (blank line between entries, `<Title>` from the album's
  front-matter `title:` field, `NNN` zero-padded to match the existing
  alt-text style).
- **Changed photo:** upload to R2 with the same key, overwriting the old
  object in place. No `index.md` change — the shortcode path is unchanged,
  only the underlying object content changes.
- **Unchanged photo:** no action.

## Safety: dry run by default

The script prints its plan (which indices are new appends, which are
replacements, which are no-ops) without touching R2 or `index.md`. It only
performs the actions with an explicit `--apply` flag. It never runs `git
add/commit/push` — reviewing and pushing `content/albums/` changes stays a
manual step, same as the existing album-refresh convention.

## CLI

```
python3 scripts/publish_album_updates.py <staging_dir> [slug ...] [--apply]
```

Run from the `flowersbytiana-papermod` repo root (so `.env` and
`content/albums/` resolve correctly). `<staging_dir>` is an explicit
argument (e.g. `../flowersbytiana-to-upload-2026/staging`) rather than a
hardcoded relative default, since that path changes with each year's batch
directory. If no `slug` arguments are given, the script scans every
subdirectory of `<staging_dir>`.

## Location and credentials

- `flowersbytiana-papermod/scripts/publish_album_updates.py` — committed
  to the Hugo repo (a permanent tool now, unlike the one-off migration
  scripts that live in the disposable yearly `-to-upload-<year>`
  directory).
- Reads `ACCESS_KEY_ID` / `SECRET_ACCESS_KEY` from the repo's `.env`
  (already gitignored) to configure a boto3 S3-compatible client against
  R2's endpoint (`https://<account-id>.r2.cloudflarestorage.com`,
  `region_name="auto"`). This replaces the per-file `npx wrangler r2
  object put` subprocess-loop approach used during the 2026-08 migration,
  which was slow and CPU-heavy for bulk operations (see prior feedback).
  `CLOUDFLARE_API_TOKEN` (also present in `.env`) is not needed by this
  script.
- No new pip dependency beyond `boto3` (already installed); `.env` is
  parsed with a small manual `KEY=VALUE` reader rather than adding
  `python-dotenv` as a dependency for three lines of config.

## Out of scope / deferred

- Deleting photos or albums (would need its own explicit, confirmable
  flow — not an accidental side effect of this script).
- Renumbering to close index gaps.
- Any UI — this stays a CLI script until/unless the homelab upload app
  (see `2026-08-02-homelab-upload-app-design.md`) supersedes it.
