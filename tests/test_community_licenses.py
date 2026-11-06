from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets/scripts"))
from community_licenses import distribution_members, source_archive_url, write_distribution
from community_image import IMAGE_FILES, manifest, write_downloads, write_json
import publish_community_image as publisher
import test_community_image as fixtures


PROJECTS = ("zoul0813/zeal-matrix", "zoul0813/zeal-mint", "Zeal8bit/Zeal-8-bit-OS",
            "Zeal8bit/Zeal-VideoBoard-SDK", "TurBoss/Zeal-8bit_conway", "Candid-Moe/Zorth")
UNLICENSED = {"TurBoss/Zeal-8bit_conway", "Candid-Moe/Zorth"}


class DistributionTests(unittest.TestCase):
    def sources(self, home):
        catalog = {}
        for dep_id in PROJECTS:
            path = home / dep_id
            path.mkdir(parents=True)
            (path / "source.c").write_text("/* Original source and attribution */\n")
            if dep_id not in UNLICENSED:
                (path / "LICENSE").write_text("Project license terms\n")
                (path / "third-party").mkdir()
                (path / "third-party/NOTICE").write_text("Nested notice\n")
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

    def test_pinned_links_and_copied_licenses_allow_normal_publication(self):
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
            self.assertNotIn("LICENSE", members)
            self.assertFalse(any(name.startswith("sources/") or name.endswith(".tar.gz") for name in members))
            for dep_id in PROJECTS:
                if dep_id not in UNLICENSED:
                    self.assertEqual(members[f"licenses/{dep_id}/LICENSE"], b"Project license terms\n")
                    self.assertEqual(members[f"licenses/{dep_id}/third-party/NOTICE"], b"Nested notice\n")
            for component in report["distribution"]["components"]:
                revision = catalog[component["id"]]["revision"]["current_commit"]
                self.assertEqual(component["source_url"],
                                 f"https://github.com/{component['id']}/archive/{revision}.tar.gz")
                self.assertIn(component["source_url"].encode(), members["THIRD_PARTY_NOTICES.md"])
                self.assertEqual(component["files"] == [], component["id"] in UNLICENSED)
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

    def test_source_urls_require_an_exact_commit_and_valid_github_repository(self):
        revision = "a" * 40
        self.assertEqual(source_archive_url("https://github.com/zoul0813/zeal-mint.git", revision),
                         f"https://github.com/zoul0813/zeal-mint/archive/{revision}.tar.gz")
        for repository, ref in [("https://github.com/zoul0813/zeal-mint", "main"),
                                ("https://example.com/zoul0813/zeal-mint", revision),
                                ("https://github.com/zoul0813/zeal-mint/../other", revision)]:
            with self.assertRaises(ValueError):
                source_archive_url(repository, ref)
