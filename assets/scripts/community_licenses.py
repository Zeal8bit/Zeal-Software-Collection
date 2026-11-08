"""License notices and pinned source links for community downloads, outside the TF volume."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import re
from urllib.parse import urlsplit
import zipfile

DISTRIBUTION_FILES = ("distribution.zip", "distribution.zip.sha256")


def source_archive_url(repository: str, revision: str) -> str:
    """Link directly to downloadable GitHub source for the exact build commit."""
    url = urlsplit(repository)
    path = url.path.rstrip("/").removesuffix(".git")
    if (url.scheme != "https" or url.netloc != "github.com" or url.query or url.fragment
            or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path)
            or not re.fullmatch(r"[0-9a-f]{40}", revision)):
        raise ValueError("Invalid GitHub repository or source revision")
    return f"https://github.com{path}/archive/{revision}.tar.gz"


def write_distribution(directory: Path, report: dict, catalog: dict, home: Path) -> None:
    """Copy project licenses and link to source without bundling source files."""
    members = {}
    notices = [
        "# Community image licensing", "",
        "Included projects retain their respective licenses. The files below are copied",
        "from the project revisions recorded in this build.", "",
        "These files accompany the download and are not installed in tf.img.", "",
    ]
    metadata = {"version": 2, "components": []}
    included = {row["id"] for row in report["items"] if row["status"] == "included"}
    selected = set(included)
    for kernel in report.get("kernels", []):
        if kernel["status"] == "included":
            selected.update(kernel["sources"])
    selected.update(dep_id for dep_id, dep in catalog.items() if dep.get("required"))
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
        repository = dep["revision"].get("repo", "https://github.com/" + "/".join(dep_id.split("/")[:2]))
        source_url = source_archive_url(repository, revision)
        metadata["components"].append({"id": dep_id, "revision": revision, "repo": repository,
                                       "source_url": source_url, "files": copies})
        notices.extend([f"## {dep_id}", "", f"Source revision: `{revision}`.",
                        f"[Download matching source]({source_url})", ""])
        notices.extend(f"- `{name}`" for name in copies)
        if not copies:
            notices.append("No license or notice file was found at this revision.")
        notices.append("")
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
