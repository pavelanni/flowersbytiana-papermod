# Homelab Upload App Design

Date: 2026-08-02

## Goal

A small self-hosted web app, running on Pavel's homelab (Docker/Podman +
docker-compose), that lets Tiana create albums and upload photos to
flowersbytiana.com herself — no shell, no git, no Cloudflare dashboard.
It **coexists** with the existing folder-based workflow
(`for-tiana/` + `HOW-TO-*.md`, published by Pavel/Claude running a script)
rather than replacing it; both paths write to the same Hugo repo and R2
bucket using the same conventions, so they never conflict.

## Non-goals (v1)

- Reordering photos *within* an album beyond appending (mid-sequence
  insertion stays a manual filename-rename thing in the folder-based path,
  per Tiana's own call that this isn't urgent).
- HEIC/other-format conversion — she exports as `.jpg` before uploading,
  per the existing cheat sheet convention.
- A general "renumber all albums" maintenance tool for when weight gaps
  run out (see Known Limitation below) — deferred until it's actually hit.
- Public/internet access, accounts, or per-user permissions — single shared
  password, LAN only.

## Architecture

A single Go binary in a container, password-gated, no database. The
source of truth for album order and photo lists is the Hugo repo's
`content/albums/*/index.md` front matter — identical to what the
folder-based publish script already produces and reads. On every publish
action the app:

1. `git pull` on a persistent local clone (Docker volume) of
   `flowersbytiana-papermod`, so it always starts from whatever the
   folder-based path most recently published — the two paths never
   diverge silently.
2. Decodes each uploaded photo, auto-rotates using its EXIF orientation
   tag, resizes (cap ~2400px on the long side, preserving aspect ratio),
   re-encodes as JPEG quality ~85, and uploads to R2 via a persistent
   AWS SDK v2 S3-compatible client pointed at R2's endpoint (one long-lived
   client for the whole run — this is the direct fix for the
   wrangler-per-file subprocess slowness from the 2026-08 migration).
3. Writes/edits the relevant `content/albums/<slug>/index.md` (front
   matter + `{{< img >}}` body lines) and, for new albums, the
   `<slug>-cover.<ext>` file.
4. `git add / commit / push` over SSH, using a deploy key scoped to just
   this one repo, mounted read-only into the container.
5. Reports success/failure back to the browser. No approval step —
   publish is immediate, matching Pavel's explicit call that the whole
   point is removing himself from the loop. Cloudflare Pages picks up the
   push and rebuilds automatically (its native Git integration, no GitHub
   Actions involved).

## Tech stack

- **Go** — single static binary, minimal container image.
- **Templ** — type-safe HTML templating, compiles `.templ` files to Go
  code via `templ generate` (a build stage before `go build` in the
  Dockerfile).
- **Pico CSS** — classless CSS framework; plain semantic HTML from Templ
  needs no class bookkeeping to look reasonable.
- **HTMX** — all interactivity (upload progress, delete-without-reload,
  live weight-position preview) is server-rendered Templ fragments
  swapped into the DOM via HTMX attributes. No hand-written JavaScript.
- **AWS SDK for Go v2** — R2 is S3-compatible; configured with R2's custom
  endpoint and an R2 API token (Access Key ID/Secret from the Cloudflare
  dashboard — separate credential from the SSH deploy key).
- **`os/exec` + system `git`** — git operations shell out to the real
  `git` binary rather than a Go git library, for the same reason the
  migration scripts did: better-tested auth/edge-case handling.

## Components

- `internal/repo` — git pull/add/commit/push wrapper.
- `internal/albums` — parses and writes Hugo front matter; owns the
  weight-spacing math:
  - **Append:** next multiple of 10 after the current highest weight.
  - **Insert:** midpoint integer between two chosen neighbor weights
    (e.g. between 50 and 60 → 55; between 50 and 55 → no integer
    available, see Known Limitation).
- `internal/r2` — thin wrapper around the S3-compatible client: put/delete
  object.
- `internal/imaging` — EXIF-aware auto-rotate, resize, re-encode.
- `templates/` (`.templ` files) — login, album list, upload form,
  new-album form, delete/confirm views.

## Core flows

### Add photos to an existing album

1. Album list (ordered by weight) → pick one → "Add photos".
2. Select one or more files (`.jpg`/`.jpeg`/`.png` accepted; anything
   else rejected with a clear message pointing at the cheat sheet).
3. Server processes each file (rotate/resize/re-encode), assigns the next
   sequential key in that album (`<slug>/<slug>-NNN.ext`, continuing after
   the highest existing number), uploads to R2.
4. Appends one `{{< img >}}` line per photo to `index.md`.
5. Commit + push. Confirmation page shown (with a note that the live site
   updates within a couple of minutes).

### Create a new album

1. Enter English name — server enforces lowercase, digits/hyphens only,
   no spaces (same rule as the cheat sheet); rejects anything else with
   the specific problem named.
2. Choose position: "insert before/after `[existing album]`" (dropdown of
   current albums) or "at the end". The user never types a raw weight
   number — the app computes it.
3. Upload initial photos (at least 1 required) using the same
   process/upload path as above.
4. Upload a cover image (required) — same resize pipeline, saved as
   `content/albums/<slug>/<slug>-cover.<ext>`.
5. Commit new `content/albums/<slug>/` directory (front matter with
   computed `weight`, `title`, a default `tags` entry, `cover.image`, and
   the photo shortcodes) + push.

### Delete a photo

1. Album view shows live CDN thumbnails (`<img src="https://cdn.flowersbytiana.com/...">`,
   no local thumbnail storage needed) with a delete control on each.
2. Confirm → removes that photo's `{{< img >}}` line from `index.md` and
   deletes the R2 object → commit + push.
3. No renumbering of remaining files — the app writes out whatever
   `{{< img >}}` lines exist, in whatever order; a gap in the numeric
   filenames is harmless.

### Delete an album

1. Confirm by typing the album name back (mistake-resistant, mirrors
   GitHub's own dangerous-action pattern).
2. Deletes `content/albums/<slug>/` (including its cover), deletes every
   R2 object under that `<slug>/` prefix, commits + pushes.

## Known limitation: weight-gap exhaustion

If enough albums get inserted between the same two neighbors, the integer
gap eventually runs out (nothing free between weight 50 and 51). V1 simply
rejects that insert with an explicit error naming the two neighbors and
suggesting different ones. A full renumber-everything operation is a
reasonable future addition; not built now since it's an edge case neither
Pavel nor Tiana has hit yet, matching the same judgment call already made
for the folder-based path's cheat sheet.

## Access control

Single shared password (env var, no user database) gates the whole app —
cheap insurance against another device on the home network, not a real
threat model. LAN-only exposure via docker-compose port binding; no
internet-facing ingress.

## Safety net before first real use

The app must be config-driven for its git remote and R2 bucket/credentials
(never hardcoded), so it can be pointed at a scratch git repo + a test R2
bucket first. The first real exercise of the full publish flow (pull →
process → upload → write front matter → commit → push) should happen
against that scratch target, not production — this app has no
review/approval step, so the first end-to-end test shouldn't be the first
time it touches the live site.

## Out of scope / deferred

- In-album photo reordering beyond append.
- HEIC/other format conversion.
- Weight-gap renumbering tool.
- Any multi-user auth beyond one shared password.
