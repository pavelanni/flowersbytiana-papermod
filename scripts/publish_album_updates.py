#!/usr/bin/env python3
"""Publish incremental photo updates from staging/<slug>/ to R2 and index.md."""
import argparse
import hashlib
import re
import sys

import boto3
import yaml
from dataclasses import dataclass
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


def main(argv=None, repo_root=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("staging_dir", type=Path)
    parser.add_argument("slugs", nargs="*")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    if repo_root is None:
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
