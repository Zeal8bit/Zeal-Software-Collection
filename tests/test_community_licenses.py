from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets/scripts"))
from community_licenses import MATRIX_INPUTS, distribution_members, source_archive, write_distribution
from community_image import IMAGE_FILES, manifest, write_downloads, write_json
import publish_community_image as publisher
import test_community_image as fixtures


class DistributionTests(unittest.TestCase):
    def sources(self, home):
        catalog = {}
        for dep_id in MATRIX_INPUTS:
            path = home / dep_id
            path.mkdir(parents=True)
            (path / "source.c").write_text("/* Original source and attribution */\n")
            (path / "LICENSE").write_text("Project license terms\n")
            (path / "CMakeLists.txt").write_text("# Build instructions\n")
            subprocess.run(["git", "init", "-q", str(path)], check=True)
            subprocess.run(["git", "-C", str(path), "add", "."], check=True)
            subprocess.run(["git", "-C", str(path), "-c", "user.name=Test",
                            "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True)
            revision = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
            (path / "untracked-build.bin").write_bytes(b"generated")
            catalog[dep_id] = {"path": f"/home/zeal8bit/{dep_id}",
                               "revision": {"current_commit": revision}}
        return catalog

    def test_matrix_permission_source_and_notices_allow_normal_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            home = directory / "home"
            catalog = self.sources(home)
            (directory / "contents").mkdir()
            fixtures.ReleaseTests().assets(directory)
            original_image_content = manifest(directory / "contents")[0]
            report = json.loads((directory / "build-report.json").read_text())
            report["items"] = [{"id": dep_id, "status": "included", "source": dep["revision"]}
                               for dep_id, dep in catalog.items()]
            write_distribution(directory, report, catalog, home)
            first = (directory / "distribution.zip").read_bytes()
            write_distribution(directory, report, catalog, home)
            self.assertEqual((directory / "distribution.zip").read_bytes(), first)
            members = distribution_members(directory)
            self.assertIn(b"GPL version 2", members["THIRD_PARTY_NOTICES.md"])
            self.assertIn(b"explicit rights-holder permission", members["THIRD_PARTY_NOTICES.md"])
            self.assertNotIn("LICENSE", members)
            for dep_id in MATRIX_INPUTS:
                self.assertEqual(members[f"licenses/{dep_id}/LICENSE"], b"Project license terms\n")
            self.assertNotIn("publication_blockers", report["distribution"])
            for component in report["distribution"]["components"]:
                data = members[component["source"]]
                self.assertEqual(hashlib.sha256(data).hexdigest(), component["sha256"])
                with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                    names = archive.getnames()
                    self.assertTrue(any(name.endswith("source.c") for name in names))
                    self.assertTrue(any(name.endswith("CMakeLists.txt") for name in names))
                    self.assertFalse(any(".git/" in name or "untracked-build.bin" in name for name in names))
            write_json(directory / "build-report.json", report)
            write_downloads(directory, report)
            self.assertEqual(manifest(directory / "contents")[0], original_image_content)
            for name in IMAGE_FILES:
                with zipfile.ZipFile(directory / (name + ".zip")) as archive:
                    for member, data in members.items():
                        self.assertEqual(archive.read(member), data)
            def gh(*args):
                return "[[]]" if args[0] == "api" else ""
            with patch.object(publisher, "gh", side_effect=gh) as calls:
                self.assertIsNotNone(publisher.publish(directory, "owner/repo", "permission-test"))
                self.assertTrue(any(call.args[:2] == ("release", "edit") for call in calls.call_args_list))

    def test_modified_or_wrong_revision_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            catalog = self.sources(home)
            dep_id = MATRIX_INPUTS[0]
            path = home / dep_id
            revision = catalog[dep_id]["revision"]["current_commit"]
            with self.assertRaisesRegex(ValueError, "revision mismatch"):
                source_archive(path, "0" * 40, "matrix")
            (path / "source.c").write_text("changed after build\n")
            with self.assertRaisesRegex(ValueError, "Modified tracked source"):
                source_archive(path, revision, "matrix")
