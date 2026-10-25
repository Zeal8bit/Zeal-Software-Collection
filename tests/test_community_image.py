from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets/scripts"))
from community_image import manifest, validate_manifest, write_json
from build_community_image import install_and_stage, ordered
import build_community_image as builder
import publish_community_image as publisher


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

    def test_bad_baseline_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            value = manifest(Path(temporary))[0]
            self.assertTrue(publisher.changed(value, None))
            self.assertFalse(publisher.changed(value, value))
            bad = copy.deepcopy(value)
            bad["sha256"] = "bad"
            with self.assertRaises(ValueError):
                publisher.changed(value, bad)
            with self.assertRaises(ValueError):
                publisher.changed(value, {})


class BuilderTests(unittest.TestCase):
    def test_prerequisite_failure_removes_stale_image_and_writes_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / "tf.img").write_bytes(b"old image")
            with patch.object(builder.shutil, "which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "Missing prerequisite"):
                    builder.build(output)
            self.assertFalse((output / "tf.img").exists())
            report = json.loads((output / "build-report.json").read_text())
            self.assertFalse(report["success"])
            self.assertIn("Missing prerequisite", report["error"])

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
            install_and_stage(ids, catalog, zde, staging, report)
            self.assertEqual([c[-1] for c in calls if c[1] == "install"], ids)
            self.assertNotIn(("deps", "stage", "tf", "dependent"), calls)
            self.assertEqual((staging / "app").read_text(), "good")
            self.assertFalse((staging / "debris").exists())
            self.assertTrue((staging / "last").exists())
            self.assertEqual([r["status"] for r in report["items"]],
                             ["failed", "failed", "included", "failed", "failed", "included", "skipped"])


class ReleaseTests(unittest.TestCase):
    def assets(self, directory):
        (directory / "tf.img").write_bytes(b"image")
        import hashlib
        (directory / "tf.img.sha256").write_text(hashlib.sha256(b"image").hexdigest() + "  tf.img\n")
        write_json(directory / "content-manifest.json", manifest(directory / "contents")[0])
        write_json(directory / "build-report.json", {
            "success": True, "collection_commit": "a" * 40,
            "items": [{"id": "good", "status": "included"},
                      {"id": "broken", "status": "failed", "stage": "install", "reason": "failed"}]})

    def test_release_filter(self):
        releases = [
            {"draft": False, "tag_name": "v99", "published_at": "2026-12-01"},
            {"draft": True, "tag_name": publisher.PREFIX + "draft", "published_at": None},
            {"draft": False, "tag_name": publisher.PREFIX + "old", "published_at": "2026-01-01"}]
        self.assertEqual(publisher.latest_release(releases)["tag_name"], publisher.PREFIX + "old")

    def test_first_release_upload_then_publish_and_failed_upload_stays_draft(self):
        for fail_upload in (False, True):
            with self.subTest(fail_upload=fail_upload), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                (directory / "contents").mkdir()
                self.assets(directory)
                calls = []
                def gh(*args):
                    calls.append(args)
                    if args[0] == "api":
                        return "[[]]"
                    if args[:2] == ("release", "create"):
                        description = Path(args[-1]).read_text()
                        self.assertIn("broken (install): failed", description)
                    if fail_upload and args[:2] == ("release", "upload"):
                        raise subprocess.CalledProcessError(1, "gh", stderr="upload failed")
                    return ""
                with patch.object(publisher, "gh", gh):
                    if fail_upload:
                        with self.assertRaises(subprocess.CalledProcessError):
                            publisher.publish(directory, "owner/repo", "123")
                    else:
                        self.assertTrue(publisher.publish(directory, "owner/repo", "123").startswith(publisher.PREFIX))
                edits = [c for c in calls if c[:2] == ("release", "edit")]
                self.assertEqual(len(edits), int(not fail_upload))

    def test_unchanged_and_invalid_baseline_and_lookup_errors(self):
        for mode in ("unchanged", "invalid", "lookup-failed", "download-failed"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                (directory / "contents").mkdir()
                self.assets(directory)
                calls = []
                def gh(*args):
                    calls.append(args)
                    if args[0] == "api":
                        if mode == "lookup-failed":
                            raise subprocess.CalledProcessError(1, "gh")
                        return json.dumps([[{"draft": False, "tag_name": publisher.PREFIX + "old",
                                             "published_at": "2026-01-01"}]])
                    if args[:2] == ("release", "download"):
                        if mode == "download-failed":
                            raise subprocess.CalledProcessError(1, "gh")
                        baseline = json.loads((directory / "content-manifest.json").read_text())
                        write_json(Path(args[-1]) / "content-manifest.json", {} if mode == "invalid" else baseline)
                    return ""
                with patch.object(publisher, "gh", gh):
                    if mode == "unchanged":
                        self.assertIsNone(publisher.publish(directory, "owner/repo", "123"))
                    else:
                        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                            publisher.publish(directory, "owner/repo", "123")
                self.assertFalse(any(c[:2] == ("release", "create") for c in calls))


if __name__ == "__main__":
    unittest.main()
