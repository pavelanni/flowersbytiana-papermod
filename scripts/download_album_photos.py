#!/usr/bin/env python3
"""Download all album photos from R2 to a local directory, preserving the
album (slug) structure that publish_album_updates.py uploads with:
<dest_dir>/<slug>/<filename>.
"""
import argparse
import sys
from pathlib import Path

import boto3

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


def list_all_objects(s3_client, bucket: str) -> list[dict]:
    objects = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        objects.extend(page.get("Contents", []))
    return objects


def download_objects(
    s3_client, bucket: str, objects: list[dict], dest_dir: Path, overwrite: bool
) -> tuple[int, int]:
    downloaded = 0
    skipped = 0
    for obj in objects:
        key = obj["Key"]
        if key.endswith("/"):
            continue
        local_path = dest_dir / key
        if not overwrite and local_path.exists() and local_path.stat().st_size == obj["Size"]:
            print("  [skip] %s (already downloaded)" % key)
            skipped += 1
            continue
        local_path.parent.mkdir(parents=True, exist_ok=True)
        print("  [get]  %s" % key)
        s3_client.download_file(bucket, key, str(local_path))
        downloaded += 1
    return downloaded, skipped


def main(argv=None, repo_root=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dest_dir", type=Path, help="local directory to download albums into"
    )
    parser.add_argument(
        "slugs",
        nargs="*",
        help="only download these album slugs (default: all albums in the bucket)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="re-download files even if a same-size local copy already exists",
    )
    args = parser.parse_args(argv)

    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent
    env = load_env(repo_root / ".env")
    s3_client = build_s3_client(env, R2_ENDPOINT)

    objects = list_all_objects(s3_client, R2_BUCKET)
    if args.slugs:
        prefixes = tuple("%s/" % slug for slug in args.slugs)
        objects = [o for o in objects if o["Key"].startswith(prefixes)]

    args.dest_dir.mkdir(parents=True, exist_ok=True)
    downloaded, skipped = download_objects(
        s3_client, R2_BUCKET, objects, args.dest_dir, args.overwrite
    )

    print(
        "\nDone: %d downloaded, %d skipped (%d total)."
        % (downloaded, skipped, downloaded + skipped)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
