#!/usr/bin/env python3
"""Publish a verified community build only when its image-visible contents changed.

Requires GitHub CLI (gh) and GH_TOKEN. The local builder never needs either.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from community_image import bundle_lock, notes, validate_manifest

PREFIX = "community-image-"
ASSETS = ("tf.img", "tf.img.sha256", "tf.img.zip", "content-manifest.json", "build-report.json")


def gh(*args):
    result = subprocess.run(["gh", *map(str, args)], check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.stdout


def latest_release(releases):
    candidates = [r for r in releases if not r["draft"] and r["tag_name"].startswith(PREFIX)]
    return max(candidates, key=lambda r: r["published_at"], default=None)


def changed(current, baseline):
    validate_manifest(current)
    if baseline is None:
        return True
    validate_manifest(baseline)
    return current["sha256"] != baseline["sha256"]


def publish(directory, repository, run_id):
    with bundle_lock(directory):
        return _publish(directory, repository, run_id)


def _publish(directory, repository, run_id):
    for asset in ASSETS:
        if not (directory / asset).is_file():
            raise ValueError(f"Missing release asset: {asset}")
    report = json.loads((directory / "build-report.json").read_text())
    if report.get("success") is not True:
        raise ValueError("Refusing to publish an unsuccessful build")
    if not re.fullmatch(r"[0-9a-f]{40}", report["collection_commit"]):
        raise ValueError("Invalid collection commit")
    digest = hashlib.sha256((directory / "tf.img").read_bytes()).hexdigest()
    if (directory / "tf.img.sha256").read_text() != f"{digest}  tf.img\n":
        raise ValueError("TF image checksum mismatch")
    current = json.loads((directory / "content-manifest.json").read_text())
    validate_manifest(current)
    # --slurp preserves all paginated arrays; API errors remain fatal.
    pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repository}/releases?per_page=100"))
    baseline_release = latest_release([r for page in pages for r in page])
    baseline = None
    with tempfile.TemporaryDirectory(prefix="community-release-") as temporary:
        temporary = Path(temporary)
        if baseline_release:
            gh("release", "download", baseline_release["tag_name"], "--repo", repository,
               "--pattern", "content-manifest.json", "--dir", temporary)
            baseline = json.loads((temporary / "content-manifest.json").read_text())
        if not changed(current, baseline):
            print("Image contents unchanged; no release created.")
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        tag = f"{PREFIX}{stamp}-{run_id}"
        description = temporary / "release-notes.md"
        description.write_text(notes(report))
        gh("release", "create", tag, "--repo", repository, "--draft", "--target",
           report["collection_commit"], "--title", f"Community image {stamp} UTC",
           "--notes-file", description)
        gh("release", "upload", tag, "--repo", repository,
           *[directory / asset for asset in ASSETS])
        gh("release", "edit", tag, "--repo", repository, "--draft=false", "--latest")
        print(f"Published {tag}")
        return tag


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("dist/community"))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), required=False)
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    args = parser.parse_args()
    if not args.repo or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("--repo OWNER/REPO is required")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.run_id):
        parser.error("Invalid run ID")
    try:
        publish(args.directory, args.repo, args.run_id)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Community release failed: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stderr, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
