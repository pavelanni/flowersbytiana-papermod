# Album Refresh Design

Date: 2026-08-02

## Goal

Replace all photo albums currently live on flowersbytiana.com with a new set of
17 albums sourced from `../flowersbytiana-to-upload-2026` (Russian-named
folders provided by Tiana), translated to English and uploaded to the existing
Cloudflare R2 bucket (`flowersbytiana`, custom domain `cdn.flowersbytiana.com`).

This is a full replacement, not an incremental update: all 8 currently-live
albums (`iris, lilies, lotus, narcissus, orchid, peonies, plum, roses`) are
removed, including `roses` and `lilies`, which have no corresponding folder in
the new source and will not be recreated.

## Source data quirks handled by this design

- Every source folder may contain an `exclude/` subfolder — rejected photos,
  never uploaded.
- Some folders also contain long camera-style filenames (`IMG20240405...jpg`,
  `IMG_4139.jpeg`, `.HEIC`) sitting outside `exclude/` — these are treated the
  same as excluded and never uploaded, per explicit instruction.
- `коты другие` (cats-other) consists *entirely* of such long-named files, so
  it is skipped entirely — no album is created for it.
- `13 цапли` (herons) has no photos of its own; all of its content lives in
  the nested `другие цапли` subfolder, which becomes the `herons` album as-is.
- Filenames like `1.jpg`, `1.1.jpg`, `1.2.jpg`, `2.jpg` encode an intentional
  order: Tiana inserts new photos between existing ones using decimal
  suffixes. Sort must treat these as floating-point numbers (`1 < 1.1 < 1.2 <
  2`), not as strings.
- Folder names on disk are NFD-normalized Unicode (e.g. `й` as `и` +
  combining breve) while typed/generated Cyrillic text is NFC. Any code that
  maps folder names must go through `unicodedata.normalize('NFC', name)`
  rather than comparing against literal Cyrillic strings typed by hand.

## Album mapping

Weights are spaced by 10 (not 1, 2, 3...) specifically so future albums can be
inserted between existing ones without renumbering everything — Tiana's own
suggestion, applied here.

| Weight | Slug | Source folder | Photo count |
|---|---|---|---|
| 10 | orchids | `01 орхидеи` | 10 |
| 20 | meihua | `02 мейхуа` | 6 |
| 30 | bamboo | `03 бамбук` | 5 |
| 40 | chrysanthemums | `04 хризантемы` | 9 |
| 50 | lotus | `05 лотосы` | 22 |
| 60 | kingfisher | `06 кингфишер` | 9 |
| 70 | iris | `07 ирисы` | 10 |
| 80 | peonies | `08 пионы` | 8 |
| 90 | hydrangea | `09 гортензия` | 5 |
| 100 | narcissus | `10 нарциссы` | 6 |
| 110 | dragons | `11 драконы` | 8 |
| 120 | gongbi-mogufa | `12 гунби-могуфа` | 29 |
| 130 | herons | `13 цапли/другие цапли` | 15 |
| 140 | colored-lotus | `14 лотосы цветные` | 7 |
| 150 | cats | `15 коты` | 9 |
| 160 | fish | `16 рыбы` | 11 |
| 170 | roosters | `21 петухи` | 4 |

Total: 173 photos across 17 albums.

## File naming and R2 layout

- Flat R2 layout: `<slug>/<slug>-NNN.jpg`, e.g. `orchids/orchids-000.jpg`.
  Matches how the existing `{{< img "path" "alt" >}}` shortcode already works
  (`layouts/shortcodes/img.html` just prepends `site.Params.imageCdn`).
- Within an album, files are sorted by the floating-point filename scheme
  described above, then renamed sequentially starting at `000`.

## Process

1. **Stage locally** — build `staging/<slug>/<slug>-NNN.jpg` inside
   `flowersbytiana-to-upload-2026/`, nothing touches R2 or git yet. Reviewable
   by Tiana/Pavel before anything goes live.
2. **Wipe R2** — delete the ~429 currently-known objects, enumerated from
   `../flowersbytiana-to-upload/image-list.txt` (covers `birds/`, `cats/`,
   `fish/`, `flowers/`, `gongbi/` prefixes). The bucket itself and its
   `cdn.flowersbytiana.com` custom domain binding are left intact — only
   objects are deleted, never the bucket (deleting/recreating the bucket
   would drop the custom domain binding and risk site downtime).
   Wrangler has no bucket-list command, so anything uploaded after
   `image-list.txt` was last generated (Nov 2024) and not listed in it would
   be left behind as a harmless, unreferenced orphan object.
3. **Upload** the 173 staged files to R2 under the new flat paths via
   `npx wrangler r2 object put flowersbytiana/<key> --file=<path> --remote`.
4. **Rewrite Hugo content** — delete the 8 old `content/albums/*` directories,
   create 17 new ones. Each `index.md` front matter: `title` (humanized
   slug), `weight`, `tags` (own slug + a coarse category), `cover.image`
   (copied locally from the album's first staged photo). Body: one
   `{{< img "<slug>/<slug>-NNN.jpg" "<Title> NNN" >}}` shortcode per photo, in
   order.
5. **Test locally** with `hugo server`, spot-check a few album pages and
   confirm R2 URLs resolve (e.g. via `curl -I`).
6. **Commit** the repo changes. **Push only with explicit user go-ahead**,
   since pushing deploys the live site.

## Out of scope / deferred

- Tag category assignments (flowers/birds/animals/technique) are a
  best-effort default; easy to hand-adjust in front matter later, not worth
  blocking on.
- No image resizing/optimization pass — source JPEGs are uploaded as-is,
  matching what was done previously.
