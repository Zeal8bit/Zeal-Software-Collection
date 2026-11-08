from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets/scripts"))
from community_kernels import OS_ID, build_kernels, kernel_specs
from community_image import notes

SHELL = "owner/shell"
UTILS = "owner/utils"


class KernelTests(unittest.TestCase):
    def setUp(self):
        self.entries = [
            {"id": OS_ID, "build": False,
             "kernels": [{"config": "zeal8bit", "output": "zeal8bit.rom"}]},
            {"id": SHELL, "depends_on": [UTILS],
             "kernels": [{"config": "zshell", "output": "zshell.rom", "romdisk": [SHELL, UTILS]}]},
            {"id": UTILS},
        ]
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.checkout = self.root / "zde"
        self.staging = self.checkout / "mnt/tf"
        self.staging.mkdir(parents=True)
        self.build = self.checkout / "home/OS/build"
        self.build.mkdir(parents=True)
        (self.build / "os_with_romdisk.img").write_bytes(b"stale image")
        self.catalog = {
            dep_id: {"installed": True, "revision": {"current_commit": dep_id},
                     "path": "/home/zeal8bit/" + path}
            for dep_id, path in [(OS_ID, "OS"), (SHELL, "shell"), (UTILS, "utils")]}
        self.report = {"collection_commit": "abc", "items": [
            {"id": dep_id, "status": "skipped", "stage": "stage", "reason": "not staged"}
            for dep_id in self.catalog]}
        self.calls = []
        self.failure = None

    def zde(self, *args):
        self.calls.append(args)
        if args[0] == "kernel":
            self.assertFalse(self.build.exists(), "Each variant must remove stale build outputs")
            if self.failure == "missing":
                return subprocess.CompletedProcess(args, 0)
            if self.failure == "build":
                return subprocess.CompletedProcess(args, 1)
            self.build.mkdir(parents=True)
            files = sorted(p.name for p in (self.checkout / "mnt/romdisk").iterdir() if p.is_file())
            (self.build / "os_with_romdisk.img").write_text(json.dumps([args[1], files]))
        else:
            self.assertEqual(args[:3], ("deps", "stage", "romdisk"))
            if self.failure == "stage":
                return subprocess.CompletedProcess(args, 1)
            if self.failure == "empty_utils" and args[3] == UTILS:
                return subprocess.CompletedProcess(args, 0)
            name = "zshell" if args[3] == SHELL else "ls"
            if self.failure == "collision":
                name = "zshell"
            (self.checkout / "mnt/romdisk" / name).write_text(args[3])
        return subprocess.CompletedProcess(args, 0)

    def run_kernels(self, specs=None):
        build_kernels(kernel_specs(self.entries) if specs is None else specs,
                      self.catalog, self.zde, self.checkout, self.staging,
                      self.report, self.root / "report.json")

    def test_complete_images_preserved_and_romdisks_isolated(self):
        specs = list(reversed(kernel_specs(self.entries)))
        self.run_kernels(specs)
        self.assertEqual(json.loads((self.staging / "sys/zshell.rom").read_text()),
                         ["zshell", ["ls", "zshell"]])
        self.assertEqual(json.loads((self.staging / "sys/zeal8bit.rom").read_text()), ["zeal8bit", []])
        self.assertEqual(self.calls, [("deps", "stage", "romdisk", SHELL),
                                     ("deps", "stage", "romdisk", UTILS),
                                     ("kernel", "zshell"), ("kernel", "zeal8bit")])
        for row in self.report["kernels"]:
            self.assertEqual(row["status"], "included")
            self.assertEqual(len(row["sha256"]), 64)
            self.assertEqual(row["size"], (self.staging / row["path"]).stat().st_size)
        shell = self.report["kernels"][0]
        self.assertEqual(set(shell["sources"]), {OS_ID, SHELL, UTILS})
        self.assertEqual([e["path"] for e in shell["romdisk_manifest"]["entries"]], ["ls", "zshell"])
        self.assertEqual(self.report["items"][0]["files"], ["sys/zeal8bit.rom"])
        self.assertIn("t:/sys/zshell.rom", notes(self.report))
        self.assertIn(UTILS, notes(self.report))

    def test_bad_declarations_rejected(self):
        for change, expected in [
            ({"output": "../bad.img"}, "filename"),
            ({"output": "x" * 17}, "filename"),
            ({"output": "zeal8bit.rom"}, "Duplicate"),
            ({"config": "menuconfig"}, "noninteractive"),
            ({"romdisk": ["missing"]}, "Unavailable"),
            ({"romdisk": "utils"}, "project IDs"),
            ({"artifacts": ["bin/"]}, "declaration"),
        ]:
            with self.subTest(change=change):
                entries = copy.deepcopy(self.entries)
                entries[1]["kernels"][0].update(change)
                with self.assertRaisesRegex(ValueError, expected):
                    kernel_specs(entries)
        self.entries[2]["zde"] = False
        with self.assertRaisesRegex(ValueError, "Unavailable"):
            kernel_specs(self.entries)

    def test_stage_collision_and_build_failures_recorded(self):
        for failure, reason in [("stage", "staging failed"), ("collision", "Invalid ROM disk"),
                                ("empty_utils", "produced no files"),
                                ("build", "build failed"), ("missing", "Missing or empty")]:
            with self.subTest(failure=failure):
                self.failure = failure
                self.report.pop("kernels", None)
                with self.assertRaisesRegex(RuntimeError, reason):
                    self.run_kernels(kernel_specs(self.entries)[1:])
                saved = json.loads((self.root / "report.json").read_text())
                self.assertEqual(saved["kernels"][0]["status"], "failed")
                self.assertFalse((self.staging / "sys/zshell.rom").exists())

    def test_failed_contributor_prevents_build(self):
        self.catalog[UTILS]["installed"] = False
        with self.assertRaisesRegex(RuntimeError, "failed to install"):
            self.run_kernels(kernel_specs(self.entries)[1:])
        self.assertEqual(self.calls, [])

    def test_existing_tf_destination_is_not_overwritten(self):
        (self.staging / "sys").mkdir()
        image = self.staging / "sys/zeal8bit.rom"
        image.write_bytes(b"existing")
        with self.assertRaisesRegex(RuntimeError, "Conflicting kernel destination"):
            self.run_kernels(kernel_specs(self.entries)[:1])
        self.assertEqual(image.read_bytes(), b"existing")


if __name__ == "__main__":
    unittest.main()
