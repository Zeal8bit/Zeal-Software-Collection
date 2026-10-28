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

from community_image import bundle_lock, conflicts, export_bundle, manifest, write_downloads, write_json

ROOT = Path(__file__).resolve().parents[2]
ZDE_REPO = "https://github.com/zoul0813/zeal-dev-environment.git"
IMAGE = "docker.io/zoul0813/zeal-dev-environment:latest"
# Read the effective catalog through ZDE itself: no duplicate catalog merging rules.
CATALOG_QUERY = """
import json
import sys
sys.path.insert(0, "/home/zeal8bit/zde")
from mods.deps import DepCatalog
c = DepCatalog()
print('COMMUNITY_CATALOG=' + json.dumps([
    dict(id=d.id, parents=d.depends_on, required=d.required,
         stage_disabled=d.stage_disabled,
         path=str(d.path_resolved), installed=d.installed,
         revision=c.lock_deps.get(d.id, {})) for d in c.deps]))
"""


def run(args, *, cwd=ROOT, env=None, log=None):
    print("+", " ".join(map(str, args)), flush=True)
    output = []
    with (log.open("a") if log else open(os.devnull, "w")) as stream:
        stream.write("+ " + " ".join(map(str, args)) + "\n")
        # These commands are noninteractive. Inheriting the terminal lets Compose
        # change its output mode, causing streamed newlines to form a staircase.
        with subprocess.Popen(list(map(str, args)), cwd=cwd, env=env,
                              stdin=subprocess.DEVNULL,
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


def install_and_stage(ids, catalog, zde, staging, report, report_path):
    """Failures are isolated per item, including dependency and staging failures."""
    installed = {}
    for dep_id in ids:
        row = {"id": dep_id, "status": "installing", "stage": "install"}
        report["items"].append(row)
        write_json(report_path, report)
        result = zde("deps", "install", dep_id)
        failed_parents = [p for p in catalog[dep_id]["parents"] if not installed.get(p)]
        if result.returncode or failed_parents:
            row["reason"] = ("Failed dependencies: " + ", ".join(failed_parents)
                             if failed_parents else "Install/build failed; see build.log")
            installed[dep_id] = False
            row["status"] = "failed"
        else:
            installed[dep_id] = True
            row.update(status="installed", stage="stage")
        write_json(report_path, report)

    for row in report["items"]:
        dep_id = row["id"]
        if not installed[dep_id]:
            continue
        if catalog[dep_id]["stage_disabled"]:
            row.update(status="skipped", reason="Staging disabled by ZDE catalog")
            write_json(report_path, report)
            continue
        row["status"] = "staging"
        write_json(report_path, report)
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
            previous_paths = {entry["path"] for entry in previous["entries"]}
            row["files"] = [entry["path"] for entry in visible["entries"]
                            if entry["type"] == "file" and entry["path"] not in previous_paths]
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
            write_json(report_path, report)


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


def build(latest_output):
    report = {"version": 1, "success": False, "status": "building", "items": [],
              "started_at": datetime.now(timezone.utc).isoformat()}
    workspace_root = ROOT / "tmp" / "community-image"
    workspace_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(
        prefix=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-"),
        dir=workspace_root))
    report["workspace"] = str(work)
    output = work / "artifacts"
    output.mkdir()
    print(f"Workspace: {work}", flush=True)
    write_json(output / "build-report.json", report)
    try:
        if not shutil.which("git"):
            raise RuntimeError("Missing prerequisite: git")
        runtime = os.environ.get("CONTAINER_CMD") or next(
            (tool for tool in ("docker", "podman") if shutil.which(tool)), None)
        if runtime not in {"docker", "podman"} or not shutil.which(runtime):
            raise RuntimeError("Missing prerequisite: Docker or Podman")
        checked([runtime, "info"])
        import yaml
        collection_source = (ROOT / "collection.yml").read_bytes()
        collection = yaml.safe_load(collection_source)
        report["collection_sha256"] = hashlib.sha256(collection_source).hexdigest()
        selected = [d["id"] for d in collection["dependencies"] if d.get("zde") is not False]
        report["collection_commit"] = checked(["git", "rev-parse", "HEAD"]).strip()
        checkout, state = work / "zde", work / "state"
        state.mkdir()
        (state / "collection.yml").write_bytes(collection_source)
        checked(["git", "clone", "--branch", "main", "--recurse-submodules", ZDE_REPO, checkout])
        checked([runtime, "pull", IMAGE])
        report["zde_commit"] = checked(["git", "-C", checkout, "rev-parse", "HEAD"]).strip()
        image_id = checked([runtime, "image", "inspect", IMAGE, "--format",
                            "{{index .RepoDigests 0}}"]).strip()
        report["zde_image"] = image_id
        env = dict(os.environ, ZDE_PATH=str(checkout), ZDE_USER_PATH=str(state),
                   ZDE_IMAGE_REF=image_id, ZDE_STRICT_EXIT="1", HOST_CWD=str(ROOT),
                   CONTAINER_CMD=runtime)
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
        required = [dep["id"] for dep in effective.values() if dep["required"]]
        ids = ordered(effective, required + selected + ["Zeal8bit/ZealFS"])
        staging = checkout / "mnt" / "tf"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        install_and_stage(ids, effective, zde, staging, report, output / "build-report.json")
        final_catalog = catalog()
        for row in report["items"]:
            row["source"] = final_catalog[row["id"]]["revision"]
        tools_failed = any(r["id"] == "Zeal8bit/ZealFS" and r["status"] == "failed"
                           for r in report["items"])
        if tools_failed or not final_catalog["Zeal8bit/ZealFS"]["installed"]:
            raise RuntimeError("ZealFS image tools failed to install")
        content, excluded = manifest(staging)
        report["excluded_names"] = excluded
        if (not any(row["status"] == "included" for row in report["items"])
                or not any(entry["type"] == "file" for entry in content["entries"])):
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
        report["tf_contents"] = [{"path": entry["path"], "type": entry["type"]}
                                 for entry in content["entries"]]
        digest = hashlib.sha256((output / "tf.img").read_bytes()).hexdigest()
        (output / "tf.img.sha256").write_text(f"{digest}  tf.img\n")
        write_downloads(output, report)
        report["success"] = True
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        report["status"] = "complete" if report["success"] else "failed"
        write_json(output / "build-report.json", report)
        export_bundle(output, latest_output)


def retry_app(latest_output: Path, app_id: str):
    """Test one app against the retained environment without changing the image bundle."""
    with bundle_lock(latest_output):
        report_path = latest_output / "build-report.json"
        if not report_path.is_file():
            raise ValueError("No previous build found; run the builder without --retry first")
        report = json.loads(report_path.read_text())
    work = Path(report["workspace"]).resolve()
    work.relative_to((ROOT / "tmp/community-image").resolve())
    checkout, state = work / "zde", work / "state"
    if not (checkout / "zde").is_file() or not state.is_dir():
        raise ValueError(f"Retained build workspace is missing: {work}")
    runtime = os.environ.get("CONTAINER_CMD") or next(
        (tool for tool in ("docker", "podman") if shutil.which(tool)), None)
    if runtime not in {"docker", "podman"} or not shutil.which(runtime):
        raise RuntimeError("Missing prerequisite: Docker or Podman")
    env = dict(os.environ, ZDE_PATH=str(checkout), ZDE_USER_PATH=str(state),
               ZDE_IMAGE_REF=report["zde_image"], ZDE_STRICT_EXIT="1",
               HOST_CWD=str(ROOT), CONTAINER_CMD=runtime)
    retries = work / "retries"
    retries.mkdir(exist_ok=True)
    attempt = Path(tempfile.mkdtemp(
        prefix=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-"), dir=retries))
    print(f"Retry workspace: {work}\nRetry log: {attempt / 'build.log'}", flush=True)
    result = {"id": app_id, "success": False, "stage": "install"}
    try:
        # ZDE owns alias resolution, configured refs, and dependency installation.
        for stage in ("install", "build"):
            result["stage"] = stage
            checked([checkout / "zde", "deps", stage, app_id], env=env,
                    log=attempt / "build.log")
        result["success"] = True
        print(f"Successfully installed and built {app_id}. Image bundle unchanged.")
    except Exception as exc:
        result["error"] = str(exc)
        raise
    finally:
        write_json(attempt / "report.json", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/community",
                        help="Latest completed bundle directory within dist/")
    parser.add_argument("--retry", metavar="APP",
                        help="Update and rebuild one app in the last build workspace; no image creation")
    parser.add_argument("--verify-image", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--manifest-output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.verify_image:
            verify_image(args.verify_image, args.manifest_output)
        else:
            output = args.output.resolve()
            output.relative_to(ROOT / "dist")
            if args.retry:
                retry_app(output, args.retry)
            else:
                build(output)
    except Exception as exc:
        print(f"Community image build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
