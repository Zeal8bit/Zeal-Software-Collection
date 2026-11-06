"""License and source companions for community downloads, outside the TF volume."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

MATRIX = "zoul0813/zeal-matrix"
MATRIX_INPUTS = (MATRIX, "Zeal8bit/Zeal-8-bit-OS", "Zeal8bit/Zeal-VideoBoard-SDK")
DISTRIBUTION_FILES = ("distribution.zip", "distribution.zip.sha256")


def source_archive(path: Path, revision: str, prefix: str) -> bytes:
    """Archive the exact clean Git revision, without untracked build output or .git data."""
    head = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    if head != revision:
        raise ValueError(f"Source revision mismatch: {prefix}")
    if subprocess.check_output(["git", "-C", str(path), "status", "--porcelain",
                                "--untracked-files=no"], text=True).strip():
        raise ValueError(f"Modified tracked source cannot be archived as {revision}: {prefix}")
    tree = subprocess.check_output(["git", "-C", str(path), "ls-tree", "-r", "HEAD"], text=True)
    if any(line.startswith("160000 ") for line in tree.splitlines()):
        raise ValueError(f"Source archive needs submodule sources: {prefix}")
    archive = subprocess.check_output(["git", "-C", str(path), "archive", "--format=tar",
                                       f"--prefix={prefix}/", revision])
    return gzip.compress(archive, mtime=0)


def write_distribution(directory: Path, report: dict, catalog: dict, home: Path) -> None:
    """Keep notices and matching source in the download, never in staging."""
    members = {}
    notices = [
        "# Community image licensing", "",
        "Included projects retain their respective licenses. The files below are copied",
        "from the project revisions recorded in this build.", "",
        "These files accompany the download and are not installed in tf.img.", "",
    ]
    metadata = {"version": 1, "components": []}
    included = {row["id"] for row in report["items"] if row["status"] == "included"}
    selected = set(included)
    selected.update(dep_id for dep_id, dep in catalog.items() if dep.get("required"))
    if MATRIX in included:
        selected.update(MATRIX_INPUTS)
    pending = list(selected)
    while pending:
        for parent in catalog[pending.pop()].get("parents", []):
            if parent not in selected:
                selected.add(parent)
                pending.append(parent)
    for dep_id in sorted(selected):
        dep = catalog[dep_id]
        revision = dep["revision"]["current_commit"]
        path = home / Path(dep["path"]).relative_to("/home/zeal8bit")
        files = subprocess.check_output(["git", "-C", str(path), "ls-tree", "-r",
                                         "--name-only", "-z", revision]).decode().split("\0")
        copies = []
        for name in filter(None, files):
            if not any(part.lower().startswith(("license", "licence", "copying", "notice"))
                       for part in Path(name).parts):
                continue
            destination = f"licenses/{dep_id}/{name}"
            members[destination] = subprocess.check_output(
                ["git", "-C", str(path), "show", f"{revision}:{name}"])
            copies.append(destination)
        metadata.setdefault("licenses", []).append({"id": dep_id, "revision": revision, "files": copies})
        notices.extend([f"## {dep_id}", "", f"Source revision: `{revision}`.",
                        "Project: " + dep["revision"].get("repo", f"https://github.com/{dep_id}"), ""])
        notices.extend(f"- `{name}`" for name in copies)
        if not copies:
            notices.append("No license or notice file was found at this revision.")
        notices.append("")
    if any(row["id"] == MATRIX and row["status"] == "included" for row in report["items"]):
        if not any(name.startswith(f"licenses/{MATRIX}/") for name in members):
            raise ValueError("Matrix checkout is missing its GPL license file")
        notices.extend([
            "## Zeal Matrix — GPL version 2", "",
            "Original author: Dave Plummer, 2024.",
            "Modified for Zeal 8-bit by David Higgins, 2025.",
            "Original source: https://github.com/davepl/pdpsrc/blob/main/bsd/screensavers/matrix.c",
            "Zeal port: https://github.com/zoul0813/zeal-matrix", "",
            "See licenses/zoul0813/zeal-matrix/LICENSE for GPL terms and warranty disclaimer.",
            "Matching source archives, including the OS runtime, SDK interfaces, and build",
            "scripts, are under sources/. See sources/README.md for build instructions.", "",
            "The maintainer confirms explicit rights-holder permission to redistribute",
            "the binaries incorporating the Apache-2.0 Zeal OS runtime.", "",
        ])
        build = ["# Matrix source and build inputs", "",
                 "These archives contain the exact tracked revisions used by this build.",
                 "The Matrix source header preserves attribution and the GPL 2.0 declaration.", "",
                 "Extract all three archives in one directory. With SDCC, CMake, and the",
                 "Zeal OS SDK build prerequisites installed, build the OS SDK first following",
                 "its sdk/sdcc documentation, then build Matrix:", "", "```sh",
                 "export ZOS_PATH=$PWD/Zeal8bit-Zeal-8-bit-OS",
                 "export ZVB_SDK_PATH=$PWD/Zeal8bit-Zeal-VideoBoard-SDK",
                 "cmake -S zoul0813-zeal-matrix -B matrix-build",
                 "cmake --build matrix-build", "```", "",
                 "The build report records the ZDE checkout and container digest used for",
                 "the original build. Toolchain build instructions are preserved in the archives.", ""]
        for dep_id in MATRIX_INPUTS:
            dep = catalog[dep_id]
            revision = dep["revision"]["current_commit"]
            slug = dep_id.replace("/", "-")
            name = f"sources/{slug}-{revision}.tar.gz"
            path = home / Path(dep["path"]).relative_to("/home/zeal8bit")
            members[name] = source_archive(path, revision, slug)
            metadata["components"].append({"id": dep_id, "revision": revision, "source": name,
                                           "sha256": hashlib.sha256(members[name]).hexdigest()})
            build.append(f"- {dep_id}: `{revision}`, archive `{name.removeprefix('sources/')}`")
        members["sources/README.md"] = ("\n".join(build) + "\n").encode()
    members["THIRD_PARTY_NOTICES.md"] = ("\n".join(notices) + "\n").encode()
    members["distribution-manifest.json"] = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode()
    with zipfile.ZipFile(directory / "distribution.zip", "w") as archive:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
    digest = hashlib.sha256((directory / "distribution.zip").read_bytes()).hexdigest()
    (directory / "distribution.zip.sha256").write_text(f"{digest}  distribution.zip\n")
    report["distribution"] = metadata


def distribution_members(directory: Path) -> dict[str, bytes]:
    """Validate the companion and return exactly what each image ZIP must include."""
    data = (directory / "distribution.zip").read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if (directory / "distribution.zip.sha256").read_text() != f"{digest}  distribution.zip\n":
        raise ValueError("Distribution checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if (len(names) != len(set(names)) or any(
                name.startswith("/") or any(part in {"", ".", ".."} for part in name.split("/"))
                for name in names)):
            raise ValueError("Invalid distribution archive paths")
        members = {name: archive.read(name) for name in names}
    if not {"THIRD_PARTY_NOTICES.md", "distribution-manifest.json"} <= members.keys():
        raise ValueError("Missing distribution notices")
    return members
