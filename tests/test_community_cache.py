from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets/scripts"))
from community_cache import BuildCache, KERNEL, fingerprint, kernel_dependencies
from build_community_image import install_and_stage, ordered


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.context = {"version": 1, "image": "image@sha256:one", "zde_commit": "one"}
        self.catalog = kernel_dependencies({
            name: {"parents": parents, "path": "/home/zeal8bit/" + name,
                   "definition": {"build": {"commands": ["make"]}},
                   "stage_disabled": False, "installed": False}
            for name, parents in [(KERNEL, []), ("lib", []), ("app", ["lib"]),
                                  ("indirect", ["app"]), ("unrelated", [])]})
        self.versions = {name: "one" for name in self.catalog}
        self.order = ordered(self.catalog, list(self.catalog))
        self.built = []
        self.round = 0

    def run_build(self, *, context=None, broken=None, fail_rebuild=None):
        self.round += 1
        work = self.root / str(self.round)
        home, state = work / "zde/home", work / "state"
        home.mkdir(parents=True)
        state.mkdir()
        (state / "deps-lock.yml").write_text("version: 1\ndependencies: {}\n")
        catalog = copy.deepcopy(self.catalog)
        cache = BuildCache(self.root / "cache", context or self.context, catalog, home, state)
        cache.restore()
        for name in catalog:
            catalog[name]["installed"] = cache.project(name).exists()
        cache.revision = lambda name: (cache.project(name) / "revision").read_text()
        staging = work / "tf"
        staging.mkdir()
        report = {"items": []}
        def zde(*args):
            name = args[-1]
            path = cache.project(name)
            if args[1] == "install":
                (state / "deps-lock.yml").write_text("version: 1\ndependencies: {}\n")
                if name == broken or (name == fail_rebuild and not path.exists()):
                    return subprocess.CompletedProcess(args, 1)
                fresh = not path.exists()
                path.mkdir(parents=True, exist_ok=True)
                (path / ".git").mkdir(exist_ok=True)
                (path / "revision").write_text(self.versions[name])
                if fresh:
                    self.built.append(name)
                    parents = {parent: (cache.project(parent) / "binary").read_text()
                               for parent in catalog[name]["parents"]}
                    (path / "binary").write_text(json.dumps([self.versions[name], parents]))
            else:
                shutil.copyfile(path / "binary", staging / fingerprint(name))
            return subprocess.CompletedProcess(args, 0)
        install_and_stage(self.order, catalog, zde, staging, report, work / "report.json", cache=cache)
        cache.verify_sources()
        cache.save(report)
        return cache, report, staging

    def test_unchanged_reused_library_change_rebuilds_dependents_and_kernel_rebuilds_all(self):
        self.run_build()
        self.assertEqual(self.built, self.order)
        self.built.clear()
        _, report, staging = self.run_build()
        self.assertEqual(self.built, [])
        self.assertTrue(all(row["cache"] == "hit" for row in report["items"]))
        before = (staging / fingerprint("indirect")).read_text()
        self.versions["lib"] = "two"
        self.built.clear()
        _, _, staging = self.run_build()
        self.assertEqual(self.built, ["lib", "app", "indirect"])
        self.assertNotEqual((staging / fingerprint("indirect")).read_text(), before)
        self.versions[KERNEL] = "two"
        self.built.clear()
        self.run_build()
        self.assertEqual(self.built, self.order)

    def test_recipe_and_environment_changes_invalidate_successful_builds(self):
        self.run_build()
        self.catalog["app"]["definition"]["build"]["commands"] = ["make NEW=1"]
        self.built.clear()
        self.run_build()
        self.assertEqual(self.built, ["app", "indirect"])
        self.built.clear()
        self.run_build(context=dict(self.context, image="image@sha256:two"))
        self.assertEqual(self.built, self.order)

    def test_failed_project_and_its_dependents_are_retried(self):
        _, report, _ = self.run_build(broken="lib")
        self.assertEqual([row["id"] for row in report["items"] if row["status"] == "failed"],
                         ["lib", "app", "indirect"])
        self.built.clear()
        self.run_build()
        self.assertEqual(self.built, ["lib", "app", "indirect"])

    def test_corrupt_cache_falls_back_to_clean_builds(self):
        self.run_build()
        project = self.root / "cache/snapshot/projects" / fingerprint("lib")
        (project / "binary").write_text("corrupted")
        self.built.clear()
        self.run_build()
        self.assertEqual(self.built, self.order)

    def test_invalid_cache_metadata_and_lock_state_fall_back_to_clean_builds(self):
        for invalid in ("projects", "lock"):
            with self.subTest(invalid=invalid):
                self.run_build()
                snapshot = self.root / "cache/snapshot"
                if invalid == "projects":
                    metadata = snapshot / "cache.json"
                    value = json.loads(metadata.read_text())
                    value["projects"] = None
                    metadata.write_text(json.dumps(value))
                else:
                    (snapshot / "deps-lock.yml").write_text("corrupt installation state")
                self.built.clear()
                self.run_build()
                self.assertEqual(self.built, self.order)

    def test_failed_clean_rebuild_does_not_poison_other_projects(self):
        self.run_build()
        self.versions["lib"] = "two"
        _, report, _ = self.run_build(fail_rebuild="lib")
        self.assertEqual([row["id"] for row in report["items"] if row["status"] == "failed"],
                         ["lib", "app", "indirect"])
        self.built.clear()
        self.run_build()
        self.assertEqual(self.built, ["lib", "app", "indirect"])

    def test_source_changes_during_a_run_cannot_produce_a_mixed_image(self):
        cache, _, _ = self.run_build()
        (cache.project(KERNEL) / "revision").write_text("changed during build")
        with self.assertRaisesRegex(RuntimeError, "Source changed during the build"):
            cache.verify_sources()

    def test_private_restore_survives_shared_snapshot_replacement(self):
        first, _, _ = self.run_build()
        second, _, _ = self.run_build()
        original = (second.project("app") / "binary").read_text()
        (first.directory / "snapshot/projects" / fingerprint("app") / "binary").write_text("another run")
        second.reuse("app")
        self.assertEqual((second.project("app") / "binary").read_text(), original)
