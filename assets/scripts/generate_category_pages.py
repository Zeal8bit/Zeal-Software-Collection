#!/usr/bin/env python3

from __future__ import annotations

import re
import sys
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    yaml = None


def find_repo_root(start: Path) -> Path:
    current = start
    while current != current.parent:
        if (current / "collection.yml").exists() and (current / "hugo.toml").exists():
            return current
        current = current.parent
    return start


ROOT = find_repo_root(Path(__file__).resolve().parent)
COLLECTION_PATH = ROOT / "collection.yml"
CONTENT_ROOT = ROOT / "content"


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower())
    return re.sub(r"^-+|-+$", "", slug)


def titleize_category(category: str) -> str:
    return " ".join(part.capitalize() for part in str(category).split("-"))


def write_front_matter(path: Path, front_matter: dict, body: str = "") -> None:
    if yaml is not None:
        yaml_text = yaml.safe_dump(front_matter, sort_keys=False, allow_unicode=True).strip()
    else:
        yaml_text = _ruby_yaml_dump(front_matter)
    content = f"---\n{yaml_text}\n---\n"
    if body.strip():
        content += f"\n{body.rstrip()}\n"
    path.write_text(content, encoding="utf-8")


def _ruby_yaml_load(path: Path) -> dict:
    command = [
        "ruby",
        "-ryaml",
        "-rjson",
        "-e",
        "print JSON.dump(YAML.load_file(ARGV[0]))",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    loaded = json.loads(result.stdout)
    return loaded if isinstance(loaded, dict) else {}


def _ruby_yaml_dump(data: dict) -> str:
    payload = json.dumps(data)
    command = [
        "ruby",
        "-ryaml",
        "-rjson",
        "-e",
        "obj=JSON.parse(ARGV[0]); print YAML.dump(obj).sub(/\\A---\\s*\\n/, '').strip",
        payload,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return result.stdout


def main() -> int:
    if not COLLECTION_PATH.exists():
        print(f"collection.yml not found at {COLLECTION_PATH}", file=sys.stderr)
        return 1

    if yaml is not None:
        data = yaml.safe_load(COLLECTION_PATH.read_text(encoding="utf-8")) or {}
    else:
        data = _ruby_yaml_load(COLLECTION_PATH)
    entries = data.get("dependencies", []) or []
    entry_lastmod = load_entry_lastmod_by_id(COLLECTION_PATH, [entry.get("id") for entry in entries])

    entries_by_category: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        metadata = entry.get("metadata", {}) or {}
        categories = metadata.get("category", []) or []
        for category in categories:
            entries_by_category[category].append(entry)

    CONTENT_ROOT.mkdir(parents=True, exist_ok=True)

    for category in sorted(entries_by_category.keys()):
        category_dir = CONTENT_ROOT / category
        category_dir.mkdir(parents=True, exist_ok=True)
        category_name = titleize_category(category)
        category_entry_ids = [entry.get("id") for entry in entries_by_category[category]]
        category_lastmods = [
            entry_lastmod[entry_id]
            for entry_id in category_entry_ids
            if isinstance(entry_id, str) and entry_id in entry_lastmod
        ]
        category_front_matter = {
            "title": f"{category_name} Category",
            "description": f"Projects in the {category_name} category.",
            "category_key": category,
            "layout": "category-section",
        }
        if category_lastmods:
            category_front_matter["lastmod"] = max(category_lastmods)

        write_front_matter(
            category_dir / "_index.md",
            category_front_matter,
            f"SEO landing page for {category_name} projects.",
        )

        slugs_seen: dict[str, int] = defaultdict(int)

        for entry in entries_by_category[category]:
            metadata = entry.get("metadata", {}) or {}
            entry_id = entry.get("id")
            name = metadata.get("name") or entry.get("id") or "Project"
            base_slug = slugify(entry.get("id") or name)
            slugs_seen[base_slug] += 1
            slug = (
                f"{base_slug}-{slugs_seen[base_slug]}"
                if slugs_seen[base_slug] > 1
                else base_slug
            )

            screenshots: list[str] = []
            single_screenshot = metadata.get("screenshot")
            if isinstance(single_screenshot, str) and single_screenshot:
                screenshots.append(single_screenshot)
            multi_screenshots = metadata.get("screenshots")
            if isinstance(multi_screenshots, list):
                screenshots.extend([item for item in multi_screenshots if isinstance(item, str) and item])
            elif isinstance(multi_screenshots, str) and multi_screenshots:
                screenshots.append(multi_screenshots)

            author = metadata.get("author", {}) or {}
            front_matter = {
                "title": name,
                "description": metadata.get("description"),
                "project_id": entry_id,
                "repo": entry.get("repo"),
                "author_name": author.get("name"),
                "author_link": author.get("link"),
                "project_aliases": entry.get("aliases", []) or [],
                "depends_on": entry.get("depends_on", []) or [],
                "screenshots": screenshots,
                "category_key": category,
                "layout": "category-project",
            }
            if entry_id in entry_lastmod:
                front_matter["lastmod"] = entry_lastmod[entry_id]

            body_lines = []
            description = metadata.get("description")
            if description:
                body_lines.append(str(description))
            body_lines.append("")
            repo = entry.get("repo")
            if repo:
                body_lines.append(f"Repository: [{repo}]({repo})")
            body = "\n".join(body_lines)

            write_front_matter(category_dir / f"{slug}.md", front_matter, body)

    print(f"Generated category pages under {CONTENT_ROOT}")
    return 0


def load_entry_lastmod_by_id(collection_path: Path, entry_ids: list[str | None]) -> dict[str, str]:
    wanted_ids = {entry_id for entry_id in entry_ids if isinstance(entry_id, str) and entry_id}
    if not wanted_ids:
        return {}

    id_ranges = get_entry_line_ranges(collection_path)
    lastmod_by_id: dict[str, str] = {}
    for entry_id in wanted_ids:
        line_range = id_ranges.get(entry_id)
        if not line_range:
            continue
        timestamp = git_blame_latest_timestamp(collection_path, line_range[0], line_range[1])
        if timestamp is None:
            continue
        lastmod_by_id[entry_id] = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    return lastmod_by_id


def get_entry_line_ranges(collection_path: Path) -> dict[str, tuple[int, int]]:
    lines = collection_path.read_text(encoding="utf-8").splitlines()
    starts: list[tuple[str, int]] = []
    pattern = re.compile(r"^\s*-\s+id:\s*(.+?)\s*$")
    for index, line in enumerate(lines, start=1):
        match = pattern.match(line)
        if not match:
            continue
        starts.append((match.group(1), index))

    ranges: dict[str, tuple[int, int]] = {}
    for i, (entry_id, start_line) in enumerate(starts):
        end_line = starts[i + 1][1] - 1 if i + 1 < len(starts) else len(lines)
        ranges[entry_id] = (start_line, end_line)
    return ranges


def git_blame_latest_timestamp(path: Path, start_line: int, end_line: int) -> int | None:
    command = [
        "git",
        "blame",
        "--line-porcelain",
        f"-L{start_line},{end_line}",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, cwd=ROOT)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    timestamps: list[int] = []
    for line in result.stdout.splitlines():
        if line.startswith("author-time "):
            try:
                timestamps.append(int(line.split(" ", 1)[1]))
            except (ValueError, IndexError):
                continue

    return max(timestamps) if timestamps else None


if __name__ == "__main__":
    raise SystemExit(main())
