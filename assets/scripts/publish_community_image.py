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
import zipfile

from community_image import DOWNLOAD_FILES, IMAGE_FILES, IMAGE_SIZES_MIB, bundle_lock, notes, validate_manifest

from community_licenses import DISTRIBUTION_FILES, distribution_members, source_archive_url

PREFIX = "community-image-"
ASSETS = (*DOWNLOAD_FILES, *DISTRIBUTION_FILES, "content-manifest.json", "build-report.json")


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


def publish(directory, repository, run_id, *, draft=False):
    with bundle_lock(directory):
        return _publish(directory, repository, run_id, draft=draft)


def _publish(directory, repository, run_id, *, draft=False):
    for asset in ASSETS:
        if not (directory / asset).is_file():
            raise ValueError(f"Missing release asset: {asset}")
    report = json.loads((directory / "build-report.json").read_text())
    if report.get("success") is not True:
        raise ValueError("Refusing to publish an unsuccessful build")
    if not re.fullmatch(r"[0-9a-f]{40}", report["collection_commit"]):
        raise ValueError("Invalid collection commit")
    extras = distribution_members(directory)
    metadata = json.loads(extras["distribution-manifest.json"])
    if metadata.get("version") != 2:
        raise ValueError("Unsupported distribution manifest")
    components = {component["id"]: component for component in metadata["components"]}
    revisions = {row["id"]: row.get("source", {}).get("current_commit") for row in report["items"]}
    expected = {row["id"] for row in report["items"]
                if row["status"] == "included" and row.get("source", {}).get("current_commit")}
    if not expected <= components.keys():
        raise ValueError("Missing source download links")
    for dep_id, component in components.items():
        if (not revisions.get(dep_id) or component["revision"] != revisions[dep_id]
                or component["source_url"] != source_archive_url(component["repo"], revisions[dep_id])
                or any(name not in extras for name in component["files"])):
            raise ValueError(f"Distribution materials do not match the build: {dep_id}")
    for size, name in zip(IMAGE_SIZES_MIB, IMAGE_FILES):
        checksum = (directory / (name + ".sha256")).read_text()
        with zipfile.ZipFile(directory / (name + ".zip")) as archive:
            if sorted(archive.namelist()) != sorted([name, name + ".sha256", *extras]):
                raise ValueError(f"Unexpected ZIP contents: {name}")
            if any(archive.read(member) != data for member, data in extras.items()):
                raise ValueError(f"Distribution materials differ in {name}")
            if archive.getinfo(name).file_size != size * 1024 * 1024:
                raise ValueError(f"Unexpected TF image size: {name}")
            with archive.open(name) as source:
                digest = hashlib.sha256()
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            if (checksum != f"{digest.hexdigest()}  {name}\n"
                    or archive.read(name + ".sha256").decode() != checksum):
                raise ValueError(f"TF image checksum mismatch: {name}")
    current = json.loads((directory / "content-manifest.json").read_text())
    validate_manifest(current)
    baseline_release = None
    if not draft:
        # Branch previews do not depend on a published baseline or content changes.
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
        available = {asset["name"] for asset in baseline_release.get("assets", [])} if baseline_release else set()
        if not draft and not changed(current, baseline) and set(ASSETS) <= available:
            gh("release", "download", baseline_release["tag_name"], "--repo", repository,
               "--pattern", "distribution.zip.sha256", "--dir", temporary)
            previous = (temporary / "distribution.zip.sha256").read_text()
            if not re.fullmatch(r"[0-9a-f]{64}  distribution\.zip\n", previous):
                raise ValueError("Invalid baseline distribution checksum")
            if previous == (directory / "distribution.zip.sha256").read_text():
                print("Image contents and distribution materials unchanged; no release created.")
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
        if draft:
            print(f"Created draft release {tag}")
        else:
            gh("release", "edit", tag, "--repo", repository, "--draft=false", "--latest")
            print(f"Published {tag}")
        return tag


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("dist/community"))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), required=False)
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument("--draft", action="store_true",
                        help="Always create a draft with all assets; do not publish or mark latest")
    args = parser.parse_args()
    if not args.repo or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("--repo OWNER/REPO is required")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.run_id):
        parser.error("Invalid run ID")
    try:
        publish(args.directory, args.repo, args.run_id, draft=args.draft)
    except (ValueError, KeyError, OSError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
        print(f"Community release failed: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stderr, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
