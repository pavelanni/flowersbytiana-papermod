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
