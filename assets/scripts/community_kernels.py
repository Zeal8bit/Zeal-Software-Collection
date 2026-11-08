"""Package project-declared ROM images through supported ZDE commands."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil

from community_image import conflicts, manifest, write_json

OS_ID = "Zeal8bit/Zeal-8-bit-OS"


def kernel_specs(entries):
    """Validate collection declarations before starting container work."""
    projects = {entry["id"]: entry for entry in entries}
    outputs, result = set(), []
    for entry in entries:
        variants = entry.get("kernels", [])
        if not isinstance(variants, list):
            raise ValueError(f"kernels must be a list: {entry['id']}")
        for variant in variants:
            if not isinstance(variant, dict) or set(variant) - {"config", "output", "romdisk"}:
                raise ValueError(f"Invalid kernel declaration: {entry['id']}")
            config, output = variant.get("config"), variant.get("output")
            if (not isinstance(config, str) or not config.strip() or config != config.strip()
                    or config.startswith("-") or config in {"user", "menuconfig", "default"}):
                raise ValueError(f"Invalid noninteractive kernel config: {config!r}")
            if (not isinstance(output, str) or not output or output in {".", ".."}
                    or "/" in output or "\\" in output or "\0" in output
                    or len(output.encode("utf-8")) > 16):
                raise ValueError(f"Invalid kernel output filename: {output!r}")
            if output in outputs:
                raise ValueError(f"Duplicate kernel output: {output}")
            outputs.add(output)
            romdisk = variant.get("romdisk", [])
            if not isinstance(romdisk, list) or any(not isinstance(p, str) for p in romdisk):
                raise ValueError(f"Kernel romdisk must list project IDs: {entry['id']}")
            for project in [entry["id"], OS_ID, *romdisk]:
                if project not in projects or projects[project].get("zde") is False:
                    raise ValueError(f"Unavailable kernel project: {project}")
            result.append(dict(owner=entry["id"], config=config, output=output,
                               romdisk=list(dict.fromkeys(romdisk))))
    return result


def project_path(checkout, catalog, dep_id):
    relative = Path(catalog[dep_id]["path"]).relative_to("/home/zeal8bit")
    home = (checkout / "home").resolve()
    path = home / relative
    path.resolve().relative_to(home)
    return path


def build_kernels(specs, catalog, zde, checkout, staging, report, report_path):
    """Build one isolated ROM disk at a time; never cache variant build outputs."""
    rows = {row["id"]: row for row in report["items"]}
    for spec in specs:
        row = dict(spec, status="building", stage="kernel")
        report.setdefault("kernels", []).append(row)
        write_json(report_path, report)
        try:
            contributors = list(dict.fromkeys([OS_ID, spec["owner"], *spec["romdisk"]]))
            for dep_id in contributors:
                if (dep_id not in catalog or not catalog[dep_id]["installed"]
                        or rows.get(dep_id, {}).get("status") == "failed"):
                    raise ValueError(f"Kernel project failed to install or stage: {dep_id}")
            row["sources"] = {dep_id: catalog[dep_id]["revision"] for dep_id in contributors}
            romdisk = checkout / "mnt/romdisk"
            if romdisk.exists():
                shutil.rmtree(romdisk)
            romdisk.mkdir(parents=True)
            for dep_id in spec["romdisk"]:
                before, _ = manifest(romdisk, filter_names=False)
                if zde("deps", "stage", "romdisk", dep_id).returncode:
                    raise ValueError(f"ROM disk staging failed: {dep_id}; see build.log")
                after, excluded = manifest(romdisk)
                collisions = conflicts(before, after)
                if collisions or excluded:
                    raise ValueError(f"Invalid ROM disk files: {collisions or excluded}")
                if not any(e["type"] == "file" and e not in before["entries"]
                           for e in after["entries"]):
                    raise ValueError(f"ROM disk staging produced no files: {dep_id}")
            row["romdisk_manifest"] = manifest(romdisk, filter_names=False)[0]
            build = project_path(checkout, catalog, OS_ID) / "build"
            if build.exists():
                shutil.rmtree(build)
            if zde("kernel", spec["config"]).returncode:
                raise ValueError(f"Kernel build failed: {spec['config']}; see build.log")
            image = build / "os_with_romdisk.img"
            if not image.is_file() or image.is_symlink() or image.stat().st_size == 0:
                raise ValueError(f"Missing or empty complete ROM image: {image}")
            destination = staging / "sys" / spec["output"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                raise ValueError(f"Conflicting kernel destination: {destination}")
            shutil.copyfile(image, destination)
            row.update(status="included", path="sys/" + spec["output"],
                       size=destination.stat().st_size,
                       sha256=hashlib.sha256(destination.read_bytes()).hexdigest())
            owner = rows[spec["owner"]]
            owner.update(status="included", stage="kernel")
            owner.pop("reason", None)
            owner.setdefault("files", []).append(row["path"])
        except (ValueError, OSError) as exc:
            row.update(status="failed", reason=str(exc))
            raise RuntimeError(f"Kernel {spec['config']}: {exc}") from exc
        finally:
            write_json(report_path, report)
