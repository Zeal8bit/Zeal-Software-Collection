#!/usr/bin/env python3
"""Build a community TF image using the supported ZDE CLI and container."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from community_image import conflicts, manifest, write_json

ROOT = Path(__file__).resolve().parents[2]
ZDE_REPO = "https://github.com/zoul0813/zeal-dev-environment.git"
IMAGE = "zoul0813/zeal-dev-environment:latest"
# Read the effective catalog through ZDE itself: no duplicate catalog merging rules.
CATALOG_QUERY = """
import json
import sys
sys.path.insert(0, "/home/zeal8bit/zde")
from mods.deps import DepCatalog
c = DepCatalog()
print('COMMUNITY_CATALOG=' + json.dumps([
    dict(id=d.id, parents=d.depends_on, stage_disabled=d.stage_disabled,
         path=str(d.path_resolved), installed=d.installed,
         revision=c.lock_deps.get(d.id, {})) for d in c.deps]))
"""


def run(args, *, cwd=ROOT, env=None, log=None):
    print("+", " ".join(map(str, args)), flush=True)
    output = []
    with (log.open("a") if log else open(os.devnull, "w")) as stream:
        stream.write("+ " + " ".join(map(str, args)) + "\n")
        with subprocess.Popen(list(map(str, args)), cwd=cwd, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True) as process:
            for line in process.stdout:
                print(line, end="", flush=True)
                stream.write(line)
                stream.flush()
                output.append(line)
            code = process.wait()
    return subprocess.CompletedProcess(args, code, "".join(output))


def checked(args, **kwargs):
    result = run(args, **kwargs)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(map(str, args))}")
    return result.stdout


def ordered(catalog, selected):
    result, visiting, visited = [], set(), set()
    def visit(dep_id):
        if dep_id in visiting:
            raise ValueError(f"Dependency cycle at {dep_id}")
        if dep_id in visited:
            return
        if dep_id not in catalog:
            raise ValueError(f"Unknown dependency: {dep_id}")
        visiting.add(dep_id)
        for parent in catalog[dep_id]["parents"]:
            visit(parent)
        visiting.remove(dep_id)
        visited.add(dep_id)
        result.append(dep_id)
    for dep_id in selected:
        visit(dep_id)
    return result


def install_and_stage(ids, catalog, zde, staging, report):
    """Failures are isolated per item, including dependency and staging failures."""
    installed = {}
    for dep_id in ids:
        row = {"id": dep_id, "status": "failed", "stage": "install"}
        report["items"].append(row)
        result = zde("deps", "install", dep_id)
        failed_parents = [p for p in catalog[dep_id]["parents"] if not installed.get(p)]
        if result.returncode or failed_parents:
            row["reason"] = ("Failed dependencies: " + ", ".join(failed_parents)
                             if failed_parents else "Install/build failed; see build.log")
            installed[dep_id] = False
            continue
        installed[dep_id] = True
        row.update(status="installed", stage="stage")

    for row in report["items"]:
        dep_id = row["id"]
        if not installed[dep_id]:
            continue
        if catalog[dep_id]["stage_disabled"]:
            row.update(status="skipped", reason="Staging disabled by ZDE catalog")
            continue
        before, _ = manifest(staging, filter_names=False)
        backup = staging.with_name("tf-backup")
        shutil.copytree(staging, backup, symlinks=True)
        try:
            result = zde("deps", "stage", "tf", dep_id)
            if result.returncode:
                raise ValueError("Staging failed or artifacts missing; see build.log")
            after, _ = manifest(staging, filter_names=False)
            collisions = conflicts(before, after)
            if collisions:
                raise ValueError("Conflicting paths: " + ", ".join(collisions))
            visible, excluded = manifest(staging)
            previous, _ = manifest(backup)
            row["excluded_names"] = excluded
            if visible == previous:
                row.update(status="skipped", reason="No additional image-visible files")
            else:
                row.update(status="included")
        except (ValueError, OSError) as exc:
            shutil.rmtree(staging)
            shutil.copytree(backup, staging, symlinks=True)
            row.update(status="failed", reason=str(exc))
        finally:
            shutil.rmtree(backup)


def verify_image(image, output):
    """Run inside ZDE via its supported exec command; always unmount."""
    with tempfile.TemporaryDirectory(prefix="community-verify-") as directory:
        checked(["sudo", "/home/zeal8bit/ZealFS/build/zealfs", "-v2",
                 f"--image={image}", directory])
        try:
            value, _ = manifest(Path(directory), filter_names=False)
            write_json(output, value)
        finally:
            checked(["sudo", "umount", directory])


def build(output):
    output.mkdir(parents=True, exist_ok=True)
    # A failed rerun must never leave an old image looking like a successful build.
    for name in ("tf.img", "tf.img.sha256", "content-manifest.json", "verified.json", "build.log"):
        (output / name).unlink(missing_ok=True)
    report = {"version": 1, "success": False, "items": [],
              "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        for tool in ("git", "docker"):
            if not shutil.which(tool):
                raise RuntimeError(f"Missing prerequisite: {tool}")
        checked(["docker", "info"])
        import yaml
        collection = yaml.safe_load((ROOT / "collection.yml").read_text())
        selected = [d["id"] for d in collection["dependencies"] if d.get("zde") is not False]
        report["collection_commit"] = checked(["git", "rev-parse", "HEAD"]).strip()
        with tempfile.TemporaryDirectory(prefix="community-image-") as directory:
            work = Path(directory)
            checkout, state = work / "zde", work / "state"
            state.mkdir()
            shutil.copy2(ROOT / "collection.yml", state / "collection.yml")
            checked(["git", "clone", "--branch", "main", "--recurse-submodules", ZDE_REPO, checkout])
            checked(["docker", "pull", IMAGE])
            report["zde_commit"] = checked(["git", "-C", checkout, "rev-parse", "HEAD"]).strip()
            image_id = checked(["docker", "image", "inspect", IMAGE, "--format",
                                "{{index .RepoDigests 0}}"]).strip()
            report["zde_image"] = image_id
            env = dict(os.environ, ZDE_PATH=str(checkout), ZDE_USER_PATH=str(state),
                       ZDE_IMAGE_REF=image_id, ZDE_STRICT_EXIT="1", HOST_CWD=str(ROOT))
            def zde(*args):
                return run([checkout / "zde", *args], env=env, log=output / "build.log")
            def catalog():
                result = zde("exec", "python3", "-c", CATALOG_QUERY)
                if result.returncode:
                    raise RuntimeError("Unable to read ZDE catalog")
                payload = next(line.partition("=")[2] for line in result.stdout.splitlines()
                               if line.startswith("COMMUNITY_CATALOG="))
                return {d["id"]: d for d in json.loads(payload)}
            effective = catalog()
            ids = ordered(effective, selected + ["Zeal8bit/ZealFS"])
            staging = checkout / "mnt" / "tf"
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True)
            install_and_stage(ids, effective, zde, staging, report)
            final_catalog = catalog()
            for row in report["items"]:
                row["source"] = final_catalog[row["id"]]["revision"]
            tools_failed = any(r["id"] == "Zeal8bit/ZealFS" and r["status"] == "failed"
                               for r in report["items"])
            if tools_failed or not final_catalog["Zeal8bit/ZealFS"]["installed"]:
                raise RuntimeError("ZealFS image tools failed to install")
            content, excluded = manifest(staging)
            report["excluded_names"] = excluded
            if not any(row["status"] == "included" for row in report["items"]):
                raise RuntimeError("No software was successfully staged")
            if zde("image", "tf", "create", "--yes", "65536").returncode:
                raise RuntimeError("TF image creation failed")
            # /src is the collection checkout in ZDE's standard Compose configuration.
            relative = output.relative_to(ROOT)
            verified = output / "verified.json"
            if zde("exec", "sudo", "python3", "/src/assets/scripts/build_community_image.py",
                   "--verify-image", "/mnt/tf.img", "--manifest-output",
                   str(Path("/src") / relative / "verified.json")).returncode:
                raise RuntimeError("TF image verification failed")
            if json.loads(verified.read_text()) != content:
                raise RuntimeError("TF image contents differ from staging manifest")
            verified.unlink()
            shutil.copyfile(checkout / "mnt" / "tf.img", output / "tf.img")
            write_json(output / "content-manifest.json", content)
            digest = hashlib.sha256((output / "tf.img").read_bytes()).hexdigest()
            (output / "tf.img.sha256").write_text(f"{digest}  tf.img\n")
            report["success"] = True
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        write_json(output / "build-report.json", report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/community",
                        help="Output directory within the collection checkout")
    parser.add_argument("--verify-image", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--manifest-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.verify_image:
            verify_image(args.verify_image, args.manifest_output)
        else:
            output = args.output.resolve()
            output.relative_to(ROOT / "dist")
            build(output)
    except Exception as exc:
        print(f"Community image build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
