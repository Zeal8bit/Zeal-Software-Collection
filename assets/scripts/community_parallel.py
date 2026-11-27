"""Schedule independent ZDE installations in private workspaces."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import copy
from pathlib import Path
import shutil
import tempfile

from community_cache import fingerprint
from community_image import manifest, write_json
from community_kernels import project_path


def install_projects(ids, catalog, install, report, report_path, *, jobs=1, finish=None):
    rows = {dep_id: {"id": dep_id, "status": "pending", "stage": "install",
                     "dependencies": list(catalog[dep_id]["parents"])} for dep_id in ids}
    report["items"].extend(rows.values())
    pending = dict.fromkeys(ids)
    completed = set()
    running = {}
    # Unblock dependency chains before queueing independent leaf projects.
    parents = {parent for row in rows.values() for parent in row["dependencies"]}
    order = sorted(ids, key=lambda dep_id: dep_id not in parents) if jobs > 1 else ids

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        while pending or running:
            for dep_id in order:
                if len(running) == jobs:
                    break
                if dep_id not in pending:
                    continue
                row = rows[dep_id]
                if not completed.issuperset(row["dependencies"]):
                    continue
                del pending[dep_id]
                failed = [parent for parent in row["dependencies"] if rows[parent]["status"] == "failed"]
                if failed:
                    row.update(status="failed", reason="Failed dependencies: " + ", ".join(failed))
                    completed.add(dep_id)
                    write_json(report_path, report)
                else:
                    row["status"] = "installing"
                    write_json(report_path, report)
                    if jobs == 1:
                        install(dep_id, row)
                        completed.add(dep_id)
                        write_json(report_path, report)
                    else:
                        # Workers update a private row. Only the coordinator writes the report.
                        running[pool.submit(install, dep_id, dict(row))] = dep_id

            if running:
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    dep_id = running.pop(future)
                    finish(dep_id, rows[dep_id], future.result())
                    completed.add(dep_id)
                    write_json(report_path, report)


def read_lock(state):
    import yaml

    path = state / "deps-lock.yml"
    if path.is_file():
        return yaml.safe_load(path.read_text())
    return {"version": 1, "dependencies": {}}


class IsolatedBuilds:
    """Copy dependency outputs into each worker and promote only its own project."""

    def __init__(self, work, checkout, state, source, catalog, ids, run_zde, log):
        self.checkout = checkout
        self.state = state
        self.run_zde = run_zde
        self.log = log
        self.catalog = catalog
        self.cache = None
        paths = [project_path(checkout, catalog, dep_id) for dep_id in ids]
        self.paths_overlap = any(a == b or a in b.parents or b in a.parents
                                 for i, a in enumerate(paths) for b in paths[i + 1:])
        if self.paths_overlap:
            return

        self.workers = work / "workers"
        self.workers.mkdir()
        self.staged = work / "staged-projects"
        self.staged.mkdir()
        self.template = self.workers / "template"
        self.template.mkdir()
        shutil.copytree(checkout, self.template / "zde", symlinks=True)
        shutil.copytree(state, self.template / "state", symlinks=True)
        shutil.copytree(source / "assets/scripts", self.template / "src/assets/scripts",
                        symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
        self.dependencies = {}
        for dep_id in ids:
            parents = catalog[dep_id]["parents"]
            self.dependencies[dep_id] = set(parents).union(
                *(self.dependencies[parent] for parent in parents))

    def installer(self, install):
        def worker(dep_id, row):
            worker = Path(tempfile.mkdtemp(prefix=f"{self.checkout.parent.name}-", dir=self.workers))
            shutil.copytree(self.template, worker, symlinks=True, dirs_exist_ok=True)
            checkout, state = worker / "zde", worker / "state"
            parents = self.dependencies[dep_id]
            # Each of these entries has finished, or is the initial cached copy
            # of this project. The coordinator never writes those paths mid-copy.
            lock = read_lock(self.state)
            entries = lock["dependencies"]
            lock["dependencies"] = {name: entries[name] for name in (*parents, dep_id)
                                    if name in entries}
            write_json(state / "deps-lock.yml", lock)
            for name in (*parents, dep_id):
                source = project_path(self.checkout, self.catalog, name)
                if source.is_dir():
                    target = project_path(checkout, self.catalog, name)
                    shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True)

            cache = None
            if self.cache is not None:
                cache = copy(self.cache)
                cache.home, cache.state = checkout / "home", state
                cache.keys = {parent: self.cache.keys[parent] for parent in parents}
                cache.revisions = {}
                cache.tags = {}

            def zde(*args):
                return self.run_zde(checkout, state, worker / "src", worker / "build.log", *args)

            install(dep_id, row, self.catalog, zde, cache)
            if row["status"] == "installed" and not self.catalog[dep_id]["stage_disabled"]:
                staging = checkout / "mnt/tf"
                if staging.exists():
                    shutil.rmtree(staging)
                staging.mkdir(parents=True)
                if zde("deps", "stage", "tf", dep_id).returncode == 0:
                    staging.rename(self.staged / fingerprint(dep_id))
            return worker, row, cache

        return worker

    def finish(self, dep_id, row, result):
        worker, updated_row, cache = result
        row.update(updated_row)
        with self.log.open("ab") as log, (worker / "build.log").open("rb") as project_log:
            shutil.copyfileobj(project_log, log)

        source = project_path(worker / "zde", self.catalog, dep_id)
        target = project_path(self.checkout, self.catalog, dep_id)
        if target.exists():
            shutil.rmtree(target)
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)

        worker_lock = read_lock(worker / "state")
        entry = worker_lock["dependencies"].get(dep_id)
        lock = read_lock(self.state)
        if entry is None:
            lock["dependencies"].pop(dep_id, None)
        else:
            lock["dependencies"][dep_id] = entry
            lock["updated_at"] = worker_lock["updated_at"]
        write_json(self.state / "deps-lock.yml", lock)
        if cache is not None and dep_id in cache.keys:
            self.cache.keys[dep_id] = cache.keys[dep_id]
            self.cache.revisions[dep_id] = cache.revisions[dep_id]
            self.cache.tags[dep_id] = cache.tags[dep_id]
        shutil.rmtree(worker)

    def stage(self, dep_id, staging, before):
        source = self.staged / fingerprint(dep_id)
        if not source.is_dir():
            return 1
        current = {entry["path"]: entry for entry in before["entries"]}
        incoming = manifest(source, filter_names=False)[0]["entries"]
        collisions = [entry["path"] for entry in incoming
                      if entry["path"] in current and current[entry["path"]] != entry]
        if collisions:
            raise ValueError("Conflicting paths: " + ", ".join(collisions))
        shutil.copytree(source, staging, symlinks=True, dirs_exist_ok=True)
        return 0
