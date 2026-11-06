"""Reuse verified ZDE installations while keeping build inputs explicit."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from community_image import bundle_lock, manifest, validate_manifest, write_json

KERNEL = "Zeal8bit/Zeal-8-bit-OS"


def kernel_dependencies(catalog):
    """Every project uses the OS SDK, even without a collection dependency entry."""
    if KERNEL not in catalog:
        raise ValueError("ZOS kernel is missing from the ZDE catalog")
    return {dep_id: dict(dep, parents=list(dict.fromkeys(
                [*dep["parents"], *([KERNEL] if dep_id != KERNEL else [])])))
            for dep_id, dep in catalog.items()}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class BuildCache:
    def __init__(self, directory, context, catalog, home, state):
        self.directory = directory
        self.context = context
        self.catalog = catalog
        self.home = home
        self.state = state
        self.restored = {}
        self.keys = {}
        self.revisions = {}

    def project(self, dep_id):
        relative = Path(self.catalog[dep_id]["path"]).relative_to("/home/zeal8bit")
        path = self.home / relative
        path.resolve().relative_to(self.home.resolve())
        if not relative.parts or ".." in relative.parts:
            raise ValueError(f"Unsafe cached dependency path: {dep_id}")
        return path

    def revision(self, dep_id):
        return subprocess.check_output(["git", "-C", str(self.project(dep_id)),
                                        "rev-parse", "HEAD"], text=True).strip()

    def key(self, dep_id):
        dep = self.catalog[dep_id]
        revision = self.revision(dep_id)
        self.revisions[dep_id] = revision
        # The effective recipe includes ZDE overrides; metadata does not affect compilation.
        definition = {key: value for key, value in dep["definition"].items()
                      if key not in {"metadata", "aliases"}}
        value = fingerprint({"context": self.context, "id": dep_id, "revision": revision,
                             "definition": definition,
                             "dependencies": {parent: self.keys[parent] for parent in dep["parents"]}})
        self.keys[dep_id] = value
        return value

    def restore(self):
        """Restore only complete, matching snapshots and ZDE's own installation state."""
        snapshot = self.directory / "snapshot"
        copied = []
        try:
            with bundle_lock(self.directory):
                if not (snapshot / "cache.json").is_file():
                    return
                data = json.loads((snapshot / "cache.json").read_text())
                if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
                    raise ValueError("Invalid cache manifest")
                if data["context"] != self.context:
                    print("Build cache: environment changed; starting fresh.", flush=True)
                    return
                entries = {}
                for dep_id, entry in data["projects"].items():
                    if dep_id not in self.catalog:
                        continue
                    if (not isinstance(entry, dict) or not isinstance(entry.get("key"), str)
                            or len(entry["key"]) != 64):
                        raise ValueError(f"Invalid cached project: {dep_id}")
                    if entry.get("path") != self.catalog[dep_id]["path"]:
                        continue
                    validate_manifest(entry.get("manifest"))
                    entries[dep_id] = entry
                lock = snapshot / "deps-lock.yml"
                if hashlib.sha256(lock.read_bytes()).hexdigest() != data["lock_sha256"]:
                    raise ValueError("Cached ZDE installation state is corrupt")
                paths = [self.project(dep_id) for dep_id in entries]
                if any(a == b or a in b.parents or b in a.parents
                       for index, a in enumerate(paths) for b in paths[index + 1:]):
                    raise ValueError("Overlapping dependency paths cannot be cached")
                for dep_id, entry in entries.items():
                    source = snapshot / "projects" / fingerprint(dep_id)
                    if not (source / ".git").is_dir() or manifest(source, filter_names=False)[0] != entry["manifest"]:
                        raise ValueError(f"Incomplete or corrupt cached project: {dep_id}")
                for dep_id in entries:
                    target = self.project(dep_id)
                    if target.exists():
                        shutil.rmtree(target)
                    copied.append(target)
                    shutil.copytree(snapshot / "projects" / fingerprint(dep_id), target, symlinks=True)
                shutil.copyfile(snapshot / "deps-lock.yml", self.state / "deps-lock.yml")
                self.restored = entries
                self.preserve_restored()
                print(f"Build cache: restored {len(entries)} project installations.", flush=True)
        except (ValueError, KeyError, OSError) as exc:
            for path in copied:
                shutil.rmtree(path, ignore_errors=True)
            (self.state / "deps-lock.yml").unlink(missing_ok=True)
            self.restored = {}
            shutil.rmtree(self.home.parent / "cache-restored", ignore_errors=True)
            print(f"Build cache ignored: {exc}", flush=True)

    def reuse(self, dep_id):
        """Git sync can reset tracked generated libraries; preserve the built outputs."""
        # A concurrent run may have replaced the shared snapshot after our restore.
        # Keep each run's private copy instead of reading that shared snapshot again.
        source = self.home.parent / "cache-restored" / fingerprint(dep_id)
        target = self.project(dep_id)
        shutil.rmtree(target)
        shutil.copytree(source, target, symlinks=True)

    def preserve_restored(self):
        for dep_id in self.restored:
            target = self.home.parent / "cache-restored" / fingerprint(dep_id)
            shutil.copytree(self.project(dep_id), target, symlinks=True)

    def discard(self, dep_id):
        shutil.rmtree(self.project(dep_id))

    def verify_sources(self):
        for dep_id, revision in self.revisions.items():
            if self.revision(dep_id) != revision:
                raise RuntimeError(f"Source changed during the build: {dep_id}; run a fresh build")

    def save(self, report):
        """Cache successful installs only; publish the complete snapshot under a lock."""
        temporary = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=self.directory))
            entries = {}
            for row in report["items"]:
                dep_id = row["id"]
                if row["status"] == "failed" or dep_id not in self.keys:
                    continue
                source = self.project(dep_id)
                # External worktree Git directories are not self-contained installations.
                if not (source / ".git").is_dir():
                    continue
                target = temporary / "projects" / fingerprint(dep_id)
                shutil.copytree(source, target, symlinks=True)
                entries[dep_id] = {"key": self.keys[dep_id], "path": self.catalog[dep_id]["path"],
                                   "manifest": manifest(target, filter_names=False)[0]}
            shutil.copyfile(self.state / "deps-lock.yml", temporary / "deps-lock.yml")
            write_json(temporary / "cache.json", {"context": self.context, "projects": entries,
                       "lock_sha256": hashlib.sha256((temporary / "deps-lock.yml").read_bytes()).hexdigest()})
            with bundle_lock(self.directory, exclusive=True):
                snapshot = self.directory / "snapshot"
                if snapshot.exists():
                    shutil.rmtree(snapshot)
                os.replace(temporary, snapshot)
            print(f"Build cache: saved {len(entries)} successful project installations.", flush=True)
        except (ValueError, OSError) as exc:
            print(f"Build cache not saved: {exc}", flush=True)
        finally:
            if temporary is not None:
                shutil.rmtree(temporary, ignore_errors=True)
