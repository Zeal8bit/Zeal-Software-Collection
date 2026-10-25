"""Content identity shared by the local builder and release publisher."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def manifest(root: Path, *, filter_names: bool = True) -> tuple[dict, list[str]]:
    entries = []
    excluded = []

    def visit(directory: Path, relative: Path, ancestors: frozenset[Path]) -> None:
        resolved = directory.resolve(strict=True)
        if resolved in ancestors:
            raise ValueError(f"Symlink cycle at {relative}")
        for child in sorted(directory.iterdir(), key=lambda p: p.name):
            rel = relative / child.name
            if filter_names and len(child.name.encode("utf-8")) > 16:
                excluded.append(rel.as_posix())
                continue
            if child.is_dir():
                entries.append({"path": rel.as_posix(), "type": "directory"})
                visit(child, rel, ancestors | {resolved})
            elif child.is_file():
                with child.open("rb") as source:
                    digest = hashlib.sha256()
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                entries.append({"path": rel.as_posix(), "type": "file",
                                "size": child.stat().st_size, "sha256": digest.hexdigest()})
            else:
                raise ValueError(f"Unsupported or broken staged entry: {rel}")

    visit(root, Path(), frozenset())
    entries.sort(key=lambda e: e["path"])
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return {"version": 1, "entries": entries,
            "sha256": hashlib.sha256(encoded).hexdigest()}, excluded


def validate_manifest(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("entries"), list):
        raise ValueError("Unsupported content manifest")
    entries = value["entries"]
    paths = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid manifest entry")
        path = entry.get("path", "")
        if (not isinstance(path, str) or not path or path.startswith("/")
                or any(p in {"", ".", ".."} for p in path.split("/")) or path in paths):
            raise ValueError("Invalid or duplicate manifest path")
        paths.add(path)
        if entry.get("type") == "file":
            sha = entry.get("sha256", "")
            if (type(entry.get("size")) is not int or entry["size"] < 0
                    or not isinstance(sha, str) or len(sha) != 64
                    or any(c not in "0123456789abcdef" for c in sha)):
                raise ValueError("Invalid file manifest entry")
        elif entry.get("type") != "directory":
            raise ValueError("Invalid manifest entry type")
    if entries != sorted(entries, key=lambda e: e["path"]):
        raise ValueError("Unsorted content manifest")
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    if value.get("sha256") != hashlib.sha256(encoded).hexdigest():
        raise ValueError("Content manifest checksum mismatch")
    return value


def conflicts(before: dict, after: dict) -> list[str]:
    current = {e["path"]: e for e in after["entries"]}
    return [e["path"] for e in before["entries"] if current.get(e["path"]) != e]
