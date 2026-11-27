from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Barrier
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets/scripts"))
from build_community_image import install_project, stage_projects
from community_image import write_json
from community_parallel import IsolatedBuilds, install_projects, read_lock


class ParallelBuildTests(unittest.TestCase):
    def test_isolated_workers_promote_only_their_project_and_merge_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            checkout, state = work / "zde", work / "state"
            (checkout / "home").mkdir(parents=True)
            state.mkdir()
            (work / "assets/scripts").mkdir(parents=True)
            names = ["sdk", "first", "second", "failed", "blocked"]
            catalog = {
                name: {"path": f"/home/zeal8bit/{name}", "parents": parents,
                       "stage_disabled": name == "sdk"}
                for name, parents in zip(names, [[], ["sdk"], ["sdk"], ["sdk"], ["failed"]])
            }
            barrier = Barrier(2)

            def zde(worker_checkout, worker_state, source, log, _, action, *args):
                name = args[-1]
                log.write_text(name)
                home = worker_checkout / "home"
                if action == "install":
                    self.assertNotEqual(worker_state, state)
                    self.assertNotEqual(source, work)
                    self.assertEqual(set(read_lock(worker_state)["dependencies"]),
                                     set(catalog[name]["parents"]))
                    if name in {"first", "second"}:
                        self.assertEqual({p.name for p in home.iterdir()}, {"sdk"})
                        barrier.wait(timeout=5)
                        (home / "sdk/binary").write_text(name)
                    self.assertNotEqual(name, "blocked")
                    (home / name).mkdir()
                    (home / name / "binary").write_text(name)
                    lock = read_lock(worker_state)
                    lock["dependencies"][name] = {"current_commit": name}
                    lock["updated_at"] = name
                    write_json(worker_state / "deps-lock.yml", lock)
                else:
                    stage = worker_checkout / "mnt/tf/apps"
                    stage.mkdir()
                    app = stage / "shared"
                    if name == "first":
                        app.mkdir()
                        (app / "first").write_text(name)
                    else:
                        app.write_text(name)
                return subprocess.CompletedProcess(args, int(name == "failed"))

            isolated = IsolatedBuilds(work, checkout, state, work, catalog, names, zde, work / "build.log")
            report, report_path = {"items": []}, work / "report.json"
            install_projects(names, catalog, isolated.installer(install_project), report, report_path,
                             jobs=2, finish=isolated.finish)

            self.assertEqual((checkout / "home/sdk/binary").read_text(), "sdk")
            self.assertEqual(set(read_lock(state)["dependencies"]), set(names) - {"blocked"})
            staging = work / "tf"
            staging.mkdir()
            stage_projects(catalog, lambda name, before: isolated.stage(name, staging, before),
                           staging, report, report_path)
            self.assertEqual([(row["id"], row["status"]) for row in report["items"]],
                             list(zip(names, ["skipped", "included", "failed", "failed", "failed"])))
            self.assertEqual(report["items"][2]["reason"], "Conflicting paths: apps/shared")
            self.assertEqual((staging / "apps/shared/first").read_text(), "first")
