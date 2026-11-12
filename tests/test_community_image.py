from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets/scripts"))
from community_image import IMAGE_FILES, IMAGE_SIZES_MIB, bundle_lock, export_bundle, manifest, release_changes, validate_manifest, write_downloads, write_json
from community_licenses import write_distribution, distribution_members
from build_community_image import install_and_stage, ordered
import build_community_image as builder
import publish_community_image as publisher


class ChangelogTests(unittest.TestCase):
    def test_changelog_lists_only_projects_with_their_own_commit_changes(self):
        def project(name, revision="a", dependencies=(), status="included"):
            return {"id": "owner/" + name, "status": status,
                    "stage": "install", "reason": "failed",
                    "source": {"current_commit": revision},
                    "dependencies": ["owner/" + dep for dep in dependencies],
                    "files": ["bin/" + name]}
        old = {"items": [project("sdk", status="skipped"),
                         project("library", dependencies=["sdk"], status="skipped"),
                         project("game", dependencies=["library"]), project("updated"),
                         project("rebuilt"), project("removed"), project("failed"),
                         project("recovered", status="failed"), project("unchanged")]}
        new = copy.deepcopy(old)
        rows = {row["id"]: row for row in new["items"]}
        rows["owner/sdk"]["source"]["current_commit"] = "b"
        rows["owner/updated"]["source"]["current_commit"] = "b"
        rows["owner/failed"]["status"] = "failed"
        rows["owner/recovered"]["status"] = "included"
        new["items"] = [row for row in new["items"] if row["id"] != "owner/removed"]
        new["items"].append(project("added"))
        rows["owner/rebuilt"]["files"] = ["bin/different"]
        changes = release_changes(new, old)
        self.assertEqual(changes, [
            "- **Added:** [owner/added](https://github.com/owner/added)",
            "- **Updated:** [owner/sdk](https://github.com/owner/sdk) "
            "([a → b](https://github.com/owner/sdk/compare/a...b)), "
            "[owner/updated](https://github.com/owner/updated) "
            "([a → b](https://github.com/owner/updated/compare/a...b))",
            "- **Removed:** [owner/removed](https://github.com/owner/removed)"])
        for row in old["items"]:
            row.pop("dependencies")
        self.assertEqual(release_changes(new, old), changes)
        description = publisher.notes(dict(new, collection_commit="abc"), changes)
        self.assertLess(description.index("What’s changed"), description.index("Which file"))

    def test_missing_baseline_and_unchanged_projects(self):
        report = {"items": []}
        self.assertEqual(release_changes(report, None),
                         ["Previous release comparison unavailable."])
        self.assertEqual(release_changes(report, report),
                         ["No project commit changes."])

    def test_changed_dependency_graph_and_file_inventory_do_not_imply_new_commits(self):
        before = {"items": [{"id": "owner/game", "status": "included",
                             "dependencies": [], "files": ["bin/game"]}]}
        after = copy.deepcopy(before)
        after["items"][0]["dependencies"] = ["owner/sdk"]
        after["items"][0]["files"] = []
        self.assertEqual(release_changes(after, before), ["No project commit changes."])

    def test_zde_commit_changes_are_listed(self):
        before = {"items": [], "zde_commit": "a" * 40}
        after = {"items": [], "zde_commit": "b" * 40}
        changes = release_changes(after, before)
        self.assertEqual(len(changes), 1)
        self.assertIn("**Updated:** [zoul0813/zeal-dev-environment]", changes[0])
        self.assertIn(f"/compare/{'a' * 40}...{'b' * 40}", changes[0])


class ContentTests(unittest.TestCase):
    def test_identity_tracks_only_visible_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "empty").mkdir()
            (root / "app").write_bytes(b"app")
            (root / "link").symlink_to(root / "app")
            (root / ("é" * 8)).write_text("16-byte name")
            (root / ("é" * 9)).mkdir()
            (root / ("é" * 9) / "hidden").write_text("hidden")
            first, excluded = manifest(root)
            self.assertEqual(excluded, ["é" * 9])
            self.assertEqual(len(first["entries"]), 4)
            validate_manifest(first)
            os.utime(root / "app", (1, 1))
            self.assertEqual(manifest(root)[0], first)
            (root / "app").write_bytes(b"new")
            self.assertNotEqual(manifest(root)[0], first)
            (root / "app").write_bytes(b"app")
            self.assertEqual(manifest(root)[0], first)
            (root / "new").write_text("new file")
            self.assertNotEqual(manifest(root)[0], first)
            (root / "new").unlink()
            self.assertEqual(manifest(root)[0], first)
            (root / "empty").rmdir()
            self.assertNotEqual(manifest(root)[0], first)

    def test_cycles_and_broken_links_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "cycle").symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                manifest(root)
            (root / "cycle").unlink()
            (root / "broken").symlink_to(root / "missing")
            with self.assertRaises(ValueError):
                manifest(root)

    def test_bad_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            value = manifest(Path(temporary))[0]
            validate_manifest(value)
            bad = copy.deepcopy(value)
            bad["sha256"] = "bad"
            with self.assertRaises(ValueError):
                validate_manifest(bad)
            with self.assertRaises(ValueError):
                validate_manifest({})


class BundleTests(unittest.TestCase):
    def test_download_archive_and_local_release_description(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "contents").mkdir()
            ReleaseTests().assets(directory)
            for name in IMAGE_FILES:
                with zipfile.ZipFile(directory / (name + ".zip")) as archive:
                    self.assertEqual(set(archive.namelist()), {name, name + ".sha256", *distribution_members(directory)})
                    self.assertIsNone(archive.testzip())
                    for member in archive.namelist():
                        self.assertEqual(archive.read(member), (directory / member).read_bytes() if member in {name, name + ".sha256"} else distribution_members(directory)[member])
                        self.assertEqual(archive.getinfo(member).compress_type, zipfile.ZIP_DEFLATED)
            report = json.loads((directory / "build-report.json").read_text())
            description = (directory / "README.md").read_text()
            self.assertEqual(description, publisher.notes(report))
            self.assertIn("broken (install): failed", description)
            latest = directory / "latest"
            export_bundle(directory, latest)
            for name in IMAGE_FILES:
                self.assertEqual((latest / (name + ".zip")).read_bytes(),
                                 (directory / (name + ".zip")).read_bytes())
            self.assertEqual((latest / "README.md").read_text(), description)

    def test_export_waits_for_readers_and_keeps_runs_separate(self):
        import threading
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = [root / "run-a", root / "run-b"]
            latest = root / "latest"
            for i, source in enumerate(sources):
                source.mkdir()
                (source / "tf.img").write_text(str(i))
                write_json(source / "build-report.json", {"run": i})
            export_bundle(sources[0], latest)
            started, finished = threading.Event(), threading.Event()
            def writer():
                started.set()
                export_bundle(sources[1], latest)
                finished.set()
            with bundle_lock(latest):
                thread = threading.Thread(target=writer)
                thread.start()
                self.assertTrue(started.wait(1))
                self.assertFalse(finished.wait(0.05))
                self.assertEqual((latest / "tf.img").read_text(), "0")
                self.assertEqual(json.loads((latest / "build-report.json").read_text())["run"], 0)
            thread.join(5)
            self.assertTrue(finished.is_set())
            self.assertEqual((latest / "tf.img").read_text(), "1")
            self.assertEqual(json.loads((latest / "build-report.json").read_text())["run"], 1)
            self.assertEqual((sources[0] / "tf.img").read_text(), "0")


class BuilderTests(unittest.TestCase):
    def test_missing_yaml_fails_before_workspace_or_container_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / "dist/community"
            output.mkdir(parents=True)
            (output / "build-report.json").write_text("previous report")
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(builder, "ROOT", root), patch.dict(sys.modules, {"yaml": None}), patch.object(
                    sys, "argv", ["builder", "--output", str(output)]), patch.object(
                    sys, "stdout", stdout), patch.object(sys, "stderr", stderr), patch.object(
                    builder, "checked") as checked:
                self.assertEqual(builder.main(), 1)
            checked.assert_not_called()
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("Missing Python prerequisite: PyYAML", stderr.getvalue())
            self.assertIn("python3 -m venv .venv", stderr.getvalue())
            self.assertIn(".venv/bin/python assets/scripts/build_community_image.py", stderr.getvalue())
            self.assertFalse((root / "tmp").exists())
            self.assertEqual((output / "build-report.json").read_text(), "previous report")

    def test_all_capacities_are_verified_and_a_mismatch_stops_the_bundle(self):
        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                checkout = root / "zde"
                (checkout / "mnt").mkdir(parents=True)
                output = root / "dist"
                output.mkdir()
                content = {"entries": []}
                sizes = []
                def zde(*args):
                    if args[0] == "image":
                        sizes.append(int(args[-1]) // 1024)
                        with (checkout / "mnt/tf.img").open("wb") as image:
                            image.truncate(sizes[-1] * 1024 * 1024)
                    else:
                        write_json(output / "verified.json", {} if mismatch else content)
                    return subprocess.CompletedProcess(args, 0)
                with patch.object(builder, "ROOT", root):
                    if mismatch:
                        with self.assertRaisesRegex(RuntimeError, "contents differ.*2 MiB"):
                            builder.create_images(zde, checkout, output, content)
                        self.assertEqual(sizes, [2])
                        self.assertFalse(list(output.glob("*.img")))
                    else:
                        builder.create_images(zde, checkout, output, content)
                        self.assertEqual(sizes, [2, 16, 64, 128])
                        for size, name in zip(IMAGE_SIZES_MIB, IMAGE_FILES):
                            self.assertEqual((output / name).stat().st_size, size * 1024 * 1024)
                            self.assertTrue((output / (name + ".sha256")).is_file())

    def test_streamed_commands_cannot_change_the_callers_terminal(self):
        import pty
        import termios
        master, terminal = pty.openpty()
        try:
            original = termios.tcgetattr(terminal)
            # Model Compose switching an inherited input terminal to raw output.
            child = ("import os,termios; "
                     "t=termios.tcgetattr(0) if os.isatty(0) else None; "
                     "exec('if t is not None:\\n t[1] &= ~termios.OPOST; "
                     "termios.tcsetattr(0, termios.TCSANOW, t)'); "
                     "print('first'); print('second')")
            wrapper = ("import sys; import build_community_image as b; "
                       "r=b.run([sys.executable, '-c', sys.argv[1]]); "
                       "assert r.returncode == 0; "
                       "assert r.stdout == 'first\\nsecond\\n'")
            env = dict(os.environ, PYTHONPATH=str(Path(builder.__file__).parent))
            completed = subprocess.run([sys.executable, "-c", wrapper, child],
                                       stdin=terminal, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=env, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            self.assertEqual(termios.tcgetattr(terminal), original)
        finally:
            os.close(terminal)
            os.close(master)

    def test_retry_uses_last_workspace_and_preserves_bundle(self):
        for failing_stage in (None, "install", "build"):
            with self.subTest(failing_stage=failing_stage), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                output = root / "dist/community"
                work = (root / "tmp/community-image/previous-run").resolve()
                (work / "zde").mkdir(parents=True)
                (work / "zde/zde").touch()
                (work / "state").mkdir()
                output.mkdir(parents=True)
                original = {"workspace": str(work), "zde_image": "image@sha256:pinned"}
                write_json(output / "build-report.json", original)
                (output / "tf.img").write_bytes(b"original image")
                calls = []
                def checked(args, **kwargs):
                    calls.append((args, kwargs))
                    if args[2] == failing_stage:
                        raise RuntimeError("test build failure")
                    return ""
                with patch.object(builder, "ROOT", root), patch.object(
                        builder.shutil, "which", return_value="/bin/podman"), patch.dict(
                        os.environ, {"CONTAINER_CMD": "podman"}), patch.object(
                        builder, "checked", side_effect=checked):
                    if failing_stage:
                        with self.assertRaisesRegex(RuntimeError, "test build failure"):
                            builder.retry_app(output, "conway")
                    else:
                        builder.retry_app(output, "conway")
                self.assertEqual([c[0][2] for c in calls],
                                 ["install"] if failing_stage == "install" else ["install", "build"])
                for args, kwargs in calls:
                    self.assertEqual(args[0], work / "zde/zde")
                    self.assertEqual(args[-1], "conway")
                    self.assertEqual(kwargs["env"]["ZDE_USER_PATH"], str(work / "state"))
                    self.assertEqual(kwargs["env"]["ZDE_IMAGE_REF"], original["zde_image"])
                    self.assertEqual(kwargs["env"]["ZDE_STRICT_EXIT"], "1")
                result = json.loads(next((work / "retries").glob("*/report.json")).read_text())
                self.assertEqual(result["success"], failing_stage is None)
                self.assertEqual((output / "tf.img").read_bytes(), b"original image")
                self.assertEqual(json.loads((output / "build-report.json").read_text()), original)

    def test_retry_without_previous_build_explains_prerequisite(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "No previous build"):
                builder.retry_app(Path(temporary), "conway")

    def test_prerequisite_failure_removes_stale_image_and_writes_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / "tf.img").write_bytes(b"old image")
            write_json(output / "build-report.json", {"success": True})
            def missing_tool(tool):
                # A previous successful report must not survive into a new run.
                # The latest completed bundle stays intact while the new run is active.
                self.assertTrue(json.loads((output / "build-report.json").read_text())["success"])
                workspace = next((output / "tmp/community-image").iterdir())
                active = json.loads((workspace / "artifacts/build-report.json").read_text())
                self.assertFalse(active["success"])
                return None
            with patch.object(builder, "ROOT", output), patch.object(
                    builder.shutil, "which", side_effect=missing_tool):
                with self.assertRaisesRegex(RuntimeError, "Missing prerequisite"):
                    builder.build(output)
            self.assertFalse((output / "tf.img").exists())
            report = json.loads((output / "build-report.json").read_text())
            self.assertFalse(report["success"])
            self.assertIn("Missing prerequisite", report["error"])

    def test_each_invocation_retains_a_unique_project_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "collection.yml").write_text("version: 1\ndependencies: []\n")
            output = root / "dist/community"
            workspaces = []
            fake_yaml = SimpleNamespace(safe_load=lambda text: {"dependencies": []})
            with patch.object(builder, "ROOT", root), patch.dict(
                    os.environ, {"CONTAINER_CMD": "docker"}), patch.object(
                    builder.shutil, "which", return_value="/bin/tool"), patch.dict(
                    sys.modules, {"yaml": fake_yaml}):
                for _ in range(3):
                    with patch.object(builder, "checked", side_effect=[
                            "", "a" * 40, RuntimeError("clone failed")]):
                        with self.assertRaisesRegex(RuntimeError, "clone failed"):
                            builder.build(output)
                    report = json.loads((output / "build-report.json").read_text())
                    workspace = Path(report["workspace"])
                    self.assertEqual(workspace.parent, root / "tmp/community-image")
                    self.assertTrue((workspace / "state/collection.yml").is_file())
                    self.assertEqual(json.loads((workspace / "artifacts/build-report.json").read_text()), report)
                    self.assertFalse(report["success"])
                    workspaces.append(workspace)
            self.assertEqual(len(set(workspaces)), 3)
            self.assertTrue(all(workspace.exists() for workspace in workspaces))

    def test_verification_always_unmounts(self):
        calls = []
        def checked(args):
            calls.append(args)
            return ""
        with patch.object(builder, "checked", checked), patch.object(
                builder, "manifest", side_effect=ValueError("bad image")):
            with self.assertRaisesRegex(ValueError, "bad image"):
                builder.verify_image(Path("/mnt/tf.img"), Path("unused"))
        self.assertEqual(calls[-1][:2], ["sudo", "umount"])

    def test_configure_staging_requires_rename_setting(self):
        calls = []
        def zde(*args):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0)
        builder.configure_staging(zde)
        self.assertEqual(calls, [("config", "set", "deps.rename-bins", "on")])
        with self.assertRaisesRegex(RuntimeError, "configure ZDE"):
            builder.configure_staging(lambda *args: subprocess.CompletedProcess(args, 1))

    def test_order_and_cycles(self):
        catalog = {"app": {"parents": ["lib"]}, "lib": {"parents": []}}
        self.assertEqual(ordered(catalog, ["app", "lib"]), ["lib", "app"])
        catalog["lib"]["parents"] = ["app"]
        with self.assertRaises(ValueError):
            ordered(catalog, ["app"])

    def test_independent_installs_rollback_collision_and_dependencies(self):
        ids = ["broken", "dependent", "good", "partial", "conflict", "last", "library"]
        catalog = {name: {"parents": [], "stage_disabled": name == "library"} for name in ids}
        catalog["dependent"]["parents"] = ["broken"]
        report = {"items": []}
        calls = []
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary) / "tf"
            staging.mkdir()
            def zde(*args):
                calls.append(args)
                name = args[-1]
                active = json.loads((staging.parent / "report.json").read_text())
                row = next(r for r in active["items"] if r["id"] == name)
                self.assertEqual(row["status"], "installing" if args[1] == "install" else "staging")
                rc = 0
                if args[1] == "install":
                    rc = int(name == "broken")
                elif name == "good":
                    (staging / "app").write_text("good")
                elif name == "partial":
                    (staging / "debris").write_text("partial")
                    rc = 1
                elif name == "conflict":
                    (staging / "app").write_text("conflicting")
                elif name == "last":
                    (staging / "last").write_text("last")
                return subprocess.CompletedProcess(args, rc, "")
            install_and_stage(ids, catalog, zde, staging, report, staging.parent / "report.json")
            self.assertEqual([c[-1] for c in calls if c[1] == "install"], [name for name in ids if name != "dependent"])
            self.assertNotIn(("deps", "stage", "tf", "dependent"), calls)
            self.assertEqual((staging / "app").read_text(), "good")
            self.assertFalse((staging / "debris").exists())
            self.assertTrue((staging / "last").exists())
            self.assertEqual([r["status"] for r in report["items"]],
                             ["failed", "failed", "included", "failed", "failed", "included", "skipped"])
            self.assertEqual(report["items"][2]["files"], ["app"])
            self.assertEqual(report["items"][5]["files"], ["last"])
            self.assertNotIn("files", report["items"][4])


class ReleaseTests(unittest.TestCase):
    def assets(self, directory):
        import hashlib
        for size, name in zip(IMAGE_SIZES_MIB, IMAGE_FILES):
            with (directory / name).open("wb") as image:
                image.truncate(size * 1024 * 1024)
            digest = hashlib.sha256((directory / name).read_bytes()).hexdigest()
            (directory / (name + ".sha256")).write_text(digest + f"  {name}\n")
        write_json(directory / "content-manifest.json", manifest(directory / "contents")[0])
        write_json(directory / "build-report.json", {
            "success": True, "collection_commit": "a" * 40,
            "items": [{"id": "good", "status": "included"},
                      {"id": "broken", "status": "failed", "stage": "install", "reason": "failed"}]})
        report = json.loads((directory / "build-report.json").read_text())
        write_distribution(directory, {"items": []}, {}, directory)
        write_downloads(directory, report)

    def test_corrupt_capacity_is_rejected_before_any_github_calls(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "contents").mkdir()
            self.assets(directory)
            (directory / "tf-128mib.img.sha256").write_text("incorrect checksum\n")
            with patch.object(publisher, "gh") as gh:
                with self.assertRaisesRegex(ValueError, "checksum mismatch: tf-128mib.img"):
                    publisher.publish(directory, "owner/repo", "123")
                gh.assert_not_called()

    def test_notes_show_project_files_and_verified_tree(self):
        report = {"collection_commit": "abc", "items": [
            {"id": "coreutils", "status": "included", "files": ["bin/cat", "bin/ls"]},
            {"id": "game", "status": "included", "files": ["games/game"]}],
            "tf_contents": [{"path": "bin", "type": "directory"},
                            {"path": "bin/cat", "type": "file"},
                            {"path": "bin/ls", "type": "file"},
                            {"path": "games", "type": "directory"},
                            {"path": "games/game", "type": "file"}]}
        description = publisher.notes(report)
        self.assertIn("Which file should I download?", description)
        for size in (2, 16, 64, 128):
            self.assertIn(f"`tf-{size}mib.img.zip`", description)
        self.assertIn("GitHub's automatic **Source code**", description)
        self.assertIn("| coreutils | `t:/bin/cat`<br>`t:/bin/ls` |", description)
        self.assertIn("t:/\n├── bin\n│   ├── cat\n│   └── ls\n└── games\n    └── game", description)

    def test_release_filter(self):
        releases = [
            {"draft": False, "tag_name": "v99", "published_at": "2026-12-01"},
            {"draft": True, "tag_name": publisher.PREFIX + "draft", "published_at": None},
            {"draft": False, "tag_name": publisher.PREFIX + "old", "published_at": "2026-01-01"}]
        self.assertEqual(publisher.latest_release(releases)["tag_name"], publisher.PREFIX + "old")

    def test_first_release_upload_then_publish_and_failed_upload_stays_draft(self):
        for draft, fail_upload in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(draft=draft, fail_upload=fail_upload), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                (directory / "contents").mkdir()
                self.assets(directory)
                calls = []
                def gh(*args):
                    calls.append(args)
                    if args[0] == "api":
                        self.assertFalse(draft, "Draft previews must not depend on a published baseline")
                        return "[[]]"
                    if args[:2] == ("release", "create"):
                        description = Path(args[-1]).read_text()
                        self.assertIn("broken (install): failed", description)
                    if args[:2] == ("release", "upload"):
                        for name in publisher.ASSETS:
                            self.assertIn(directory / name, args)
                        self.assertNotIn(directory / "distribution.zip", args)
                        self.assertNotIn(directory / "distribution.zip.sha256", args)
                        if fail_upload:
                            raise subprocess.CalledProcessError(1, "gh", stderr="upload failed")
                    return ""
                with patch.object(publisher, "gh", gh):
                    if fail_upload:
                        with self.assertRaises(subprocess.CalledProcessError):
                            publisher.publish(directory, "owner/repo", "123", draft=draft)
                    else:
                        self.assertTrue(publisher.publish(directory, "owner/repo", "123", draft=draft).startswith(publisher.PREFIX))
                edits = [c for c in calls if c[:2] == ("release", "edit")]
                self.assertEqual(len(edits), int(not fail_upload and not draft))
                if draft:
                    self.assertFalse(any("--latest" in call or "--draft=false" in call for call in calls))

    def test_publication_uses_only_previous_commits_and_publishes_identical_outputs(self):
        for mode in ("identical-output", "missing-report", "project-updated",
                     "lookup-failed", "download-failed", "invalid-report"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                (directory / "contents").mkdir()
                self.assets(directory)
                if mode == "project-updated":
                    report = json.loads((directory / "build-report.json").read_text())
                    report["items"].append({"id": "owner/sdk", "status": "skipped",
                                            "reason": "Staging disabled",
                                            "source": {"current_commit": "b" * 40}})
                    write_json(directory / "build-report.json", report)
                calls = []
                def gh(*args):
                    calls.append(args)
                    if args[0] == "api":
                        if mode == "lookup-failed":
                            raise subprocess.CalledProcessError(1, "gh")
                        return json.dumps([[{"draft": False, "tag_name": publisher.PREFIX + "old",
                                             "published_at": "2026-01-01",
                                             "assets": [] if mode == "missing-report" else
                                             [{"name": name} for name in publisher.ASSETS]}]])
                    if args[:2] == ("release", "download"):
                        if mode == "download-failed":
                            raise subprocess.CalledProcessError(1, "gh")
                        self.assertIn("build-report.json", args)
                        self.assertNotIn("content-manifest.json", args)
                        previous = json.loads((directory / "build-report.json").read_text())
                        if mode == "project-updated":
                            previous["items"][-1]["source"]["current_commit"] = "a" * 40
                        write_json(Path(args[-1]) / "build-report.json",
                                   {} if mode == "invalid-report" else previous)
                    if args[:2] == ("release", "create"):
                        description = Path(args[-1]).read_text()
                        self.assertIn("What’s changed", description)
                        if mode == "project-updated":
                            self.assertIn("- **Updated:** [owner/sdk]", description)
                            self.assertIn(f"/compare/{'a' * 40}...{'b' * 40}", description)
                        elif mode == "missing-report":
                            self.assertIn("Previous release comparison unavailable.", description)
                        else:
                            self.assertIn("No project commit changes.", description)
                    return ""
                with patch.object(publisher, "gh", gh):
                    if mode in {"identical-output", "missing-report", "project-updated"}:
                        self.assertIsNotNone(publisher.publish(directory, "owner/repo", "123"))
                    else:
                        with self.assertRaises((ValueError, KeyError, subprocess.CalledProcessError)):
                            publisher.publish(directory, "owner/repo", "123")
                self.assertEqual(any(c[:2] == ("release", "create") for c in calls),
                                 mode in {"identical-output", "missing-report", "project-updated"})


if __name__ == "__main__":
    unittest.main()
