#!/usr/bin/env python3
"""Build and stage changed collection entries in independent ZDE environments."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import yaml

from build_community_image import CATALOG_QUERY, IMAGE, ROOT, ZDE_REPO, checked, run


def entries(document):
    data = yaml.safe_load(document)
    if not isinstance(data, dict) or not isinstance(data.get("dependencies"), list):
        raise ValueError("Collection must contain a dependencies list")
    result = {}
    for entry in data["dependencies"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            raise ValueError("Every dependency must have an ID")
        if entry["id"] in result:
            raise ValueError(f"Duplicate dependency ID: {entry['id']}")
        result[entry["id"]] = entry
    return result


def changed_entries(base, current):
    before, after = entries(base), entries(current)
    return [entry for key, entry in after.items() if before.get(key) != entry]


def validate(base_ref, jobs):
    base = subprocess.check_output(["git", "show", f"{base_ref}:collection.yml"], cwd=ROOT, text=True)
    source = (ROOT / "collection.yml").read_bytes()
    changed = changed_entries(base, source)
    targets = [entry["id"] for entry in changed
               if entry.get("zde") is not False and entry.get("build") is not False]
    for entry in changed:
        if entry["id"] not in targets:
            print(f"Skipping {entry['id']}: excluded from ZDE or build disabled", flush=True)
    if not targets:
        print("No changed buildable collection entries.")
        return True
    runtime = os.environ.get("CONTAINER_CMD") or next(
        (name for name in ("docker", "podman") if shutil.which(name)), None)
    if runtime not in {"docker", "podman"} or not shutil.which(runtime):
        raise RuntimeError("Missing prerequisite: Docker or Podman")
    with tempfile.TemporaryDirectory(prefix="collection-validation-") as temporary:
        work = Path(temporary)
        template = work / "template"
        checked(["git", "clone", "--branch", "main", "--recurse-submodules", ZDE_REPO, template])
        checked([runtime, "pull", IMAGE])
        image = checked([runtime, "image", "inspect", IMAGE, "--format", "{{index .RepoDigests 0}}"]).strip()

        def test_one(item):
            index, dep_id = item
            project = work / f"project-{index}"
            checkout, state = project / "zde", project / "state"
            project.mkdir()
            shutil.copytree(template, checkout, symlinks=True)
            state.mkdir()
            (state / "collection.yml").write_bytes(source)
            env = dict(os.environ, ZDE_PATH=str(checkout), ZDE_USER_PATH=str(state),
                       ZDE_IMAGE_REF=image, ZDE_STRICT_EXIT="1", HOST_CWD=str(ROOT),
                       CONTAINER_CMD=runtime, COMPOSE_PROJECT_NAME=f"sc-validation-{os.getpid()}-{index}")
            def zde(*args):
                return run([checkout / "zde", "--no-tty", *args], env=env)
            print(f"Validating {dep_id}", flush=True)
            catalog = zde("exec", "python3", "-c", CATALOG_QUERY)
            if catalog.returncode:
                return dep_id, "could not read ZDE catalog"
            line = next((line for line in catalog.stdout.splitlines() if line.startswith("COMMUNITY_CATALOG=")), None)
            if line is None:
                return dep_id, "could not read ZDE catalog"
            effective = {entry["id"]: entry for entry in json.loads(line.partition("=")[2])}
            if dep_id not in effective:
                return dep_id, "entry missing from ZDE catalog"
            for required in (entry["id"] for entry in effective.values() if entry["required"] and entry["id"] != dep_id):
                if zde("deps", "install", required).returncode:
                    return dep_id, f"required baseline dependency {required} failed"
            if zde("deps", "install", dep_id).returncode:
                return dep_id, "installation/build failed"
            if effective[dep_id]["stage_disabled"]:
                return dep_id, None
            if zde("deps", "stage", "tf", dep_id).returncode:
                return dep_id, "TF staging failed"
            return dep_id, None

        with ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(test_one, enumerate(targets)))
        for dep_id, error in results:
            print(f"{dep_id}: {error or 'passed'}", flush=True)
        return not any(error for _, error in results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Git revision to compare with")
    parser.add_argument("--jobs", type=int, default=4, choices=range(1, 9))
    args = parser.parse_args()
    return 0 if validate(args.base, args.jobs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
