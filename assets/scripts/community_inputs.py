#!/usr/bin/env python3
"""Skip CI image builds only when all inputs match a successful published build."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from build_community_image import IMAGE, ROOT, ZDE_REPO, ordered
from community_cache import kernel_dependencies
from community_image import write_json

# Use the same catalog merging and ref selection as ZDE, without a container.
INPUT_QUERY = """
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from mods.catalog import filter_zde_visible_deps, load_deps_yaml, merge_deps_lists
from mods.update import configured_ref, wants_tag_fetch
collection = load_deps_yaml(Path(sys.argv[2]))
selected = {d['id'] for d in filter_zde_visible_deps(collection)}
deps = filter_zde_visible_deps(merge_deps_lists(
    load_deps_yaml(Path(sys.argv[1]) / 'deps.yml', overrides=True),
    collection))
print(json.dumps([dict(id=d['id'], repo=d['repo'], ref=configured_ref(d),
                       fetch_tags=wants_tag_fetch(d), parents=d.get('depends_on', []),
                       required=d.get('required', False), selected=d['id'] in selected)
                  for d in deps]))
"""


def capture(args):
    return subprocess.check_output(list(map(str, args)), text=True, timeout=120).strip()


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def implementation_inputs(root):
    paths = sorted({*root.glob('assets/scripts/community_*.py'),
                    root / 'assets/scripts/build_community_image.py',
                    root / 'assets/scripts/publish_community_image.py',
                    root / '.github/workflows/community-image.yml'})
    return {path.relative_to(root).as_posix(): sha256(path) for path in paths}


def project_input(dep):
    kind, value = dep['ref']
    result = dict(dep)
    if kind == 'commit':
        # A full immutable object ID needs no remote lookup. Unresolvable or
        # abbreviated IDs fall back to a build rather than risking a false skip.
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value):
            raise ValueError(f"Preflight requires a full commit ID: {dep['id']}")
        result['commit'] = value
        if not dep['fetch_tags']:
            return result
        patterns = []
    elif kind == 'branch':
        patterns = [f'refs/heads/{value}']
    elif kind == 'tag':
        patterns = [f'refs/tags/{value}', f'refs/tags/{value}^{{}}']
    else:
        raise ValueError(f"Unknown Git ref type: {kind}")
    if dep['fetch_tags']:
        patterns.append('refs/tags/*')
    output = capture(['git', 'ls-remote', '--exit-code', '--', dep['repo'], *patterns])
    refs = {}
    for line in output.splitlines():
        commit, ref = line.split('\t')
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
            raise ValueError(f"Invalid remote commit: {dep['id']}")
        refs[ref] = commit
    if kind != 'commit':
        ref = f'refs/heads/{value}' if kind == 'branch' else f'refs/tags/{value}'
        result['commit'] = refs.get(ref + '^{}', refs.get(ref))
        if not result['commit']:
            raise ValueError(f"Missing remote ref: {dep['id']} {ref}")
    if dep['fetch_tags']:
        # ZOS fetches tags for version reporting, even when building main.
        result['tags'] = {ref: commit for ref, commit in refs.items() if ref.startswith('refs/tags/')}
    return result


def image_inputs():
    # Resolve the shared multi-platform index, independent of the runner's CPU.
    descriptor = json.loads(capture(['docker', 'buildx', 'imagetools', 'inspect', IMAGE,
                                     '--format', '{{json .Manifest}}']))
    digest = descriptor.get('digest')
    if digest is None:
        # Single-platform manifests do not expose their digest in the JSON view.
        description = capture(['docker', 'buildx', 'imagetools', 'inspect', IMAGE])
        match = re.search(r'^Digest:\s+(sha256:[0-9a-f]{64})\s*$', description, re.MULTILINE)
        digest = match.group(1) if match else None
    members = sorted({item['digest'] for item in descriptor.get('manifests', [])})
    if any(not isinstance(value, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', value)
           for value in [digest, *members]):
        raise ValueError('Unable to resolve the ZDE container image digest')
    return {'image_digest': digest, 'image_members': members}


def collect_inputs(root=ROOT):
    with tempfile.TemporaryDirectory(prefix='community-inputs-') as temporary:
        checkout = Path(temporary) / 'zde'
        capture(['git', 'clone', '--depth', '1', '--branch', 'main', ZDE_REPO, checkout])
        zde_commit = capture(['git', '-C', checkout, 'rev-parse', 'HEAD'])
        deps = json.loads(capture([sys.executable, '-c', INPUT_QUERY,
                                  checkout / 'home/zde', root / 'collection.yml']))
        catalog = kernel_dependencies({dep['id']: dep for dep in deps})
        roots = [dep['id'] for dep in deps if dep['required'] or dep['selected']]
        ids = ordered(catalog, roots + ['Zeal8bit/ZealFS'])
        with ThreadPoolExecutor(max_workers=8) as executor:
            projects = list(executor.map(project_input, (catalog[key] for key in ids)))
    # Registry metadata only: no image pull or container startup.
    image = image_inputs()
    return {'version': 1, 'zde_commit': zde_commit, **image,
            'collection_sha256': sha256(root / 'collection.yml'),
            'implementation': implementation_inputs(root),
            'projects': {dep['id']: dep for dep in projects}}


def check_inputs(baseline_path, current_path):
    current_path.parent.mkdir(parents=True, exist_ok=True)
    current_path.unlink(missing_ok=True)
    try:
        current = collect_inputs()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'Input check inconclusive; building normally: {exc}', flush=True)
        return True
    write_json(current_path, current)
    try:
        baseline = json.loads(baseline_path.read_text())
    except (OSError, ValueError):
        baseline = None
    changed = current != baseline
    print('Build inputs changed or no successful baseline; building.' if changed else
          'All build inputs unchanged; skipping image build and publication.', flush=True)
    return changed


def record_inputs(current_path, report_path, baseline_path):
    """Advance only after publication succeeds and the build matches preflight."""
    if not current_path.is_file():
        print('No complete input snapshot; next run will build again.')
        return False
    current = json.loads(current_path.read_text())
    report = json.loads(report_path.read_text())
    projects = current['projects']
    actual = {row['id']: row.get('source', {}).get('current_commit') for row in report['items']}
    expected = {key: dep['commit'] for key, dep in projects.items()}
    if (report.get('success') is not True or report.get('status') != 'complete'
            or any(row['status'] != 'included' for row in report.get('kernels', []))
            or actual != expected
            or report.get('zde_commit') != current['zde_commit']
            or report.get('zde_image', '').rsplit('@', 1)[-1]
               not in [current['image_digest'], *current['image_members']]
            or report.get('collection_sha256') != current['collection_sha256']):
        print('Build unsuccessful or inputs moved during the build; next run will retry.')
        return False
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(baseline_path, current)
    print('Recorded inputs from the successful build and publication.')
    return True


def output(name, value):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            stream.write(f'{name}={str(value).lower()}\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path,
                        default=ROOT / 'tmp/community-inputs/baseline.json')
    parser.add_argument('--current', type=Path,
                        default=ROOT / 'tmp/community-inputs/current.json')
    parser.add_argument('--record', action='store_true')
    parser.add_argument('--report', type=Path, default=ROOT / 'dist/community/build-report.json')
    args = parser.parse_args()
    if args.record:
        output('recorded', record_inputs(args.current, args.report, args.baseline))
    else:
        output('changed', check_inputs(args.baseline, args.current))


if __name__ == '__main__':
    main()
