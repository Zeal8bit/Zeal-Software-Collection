from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'assets/scripts'))
import community_inputs as inputs


class RemoteRefTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / 'remote'
        self.git('init', '-b', 'main', self.repo, outside=True)
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test')
        self.first = self.commit('first')

    def git(self, *args, outside=False):
        command = ['git'] if outside else ['git', '-C', str(self.repo)]
        return subprocess.check_output([*command, *map(str, args)], text=True,
                                       stderr=subprocess.PIPE).strip()

    def commit(self, value):
        (self.repo / 'source').write_text(value)
        self.git('add', 'source')
        self.git('commit', '-m', value)
        return self.git('rev-parse', 'HEAD')

    def resolve(self, kind='branch', value='main', fetch_tags=False):
        return inputs.project_input(dict(id='owner/project', repo=str(self.repo),
                                         ref=[kind, value], fetch_tags=fetch_tags))

    def test_branch_changes_and_pinned_commits(self):
        self.assertEqual(self.resolve()['commit'], self.first)
        second = self.commit('second')
        self.assertEqual(self.resolve()['commit'], second)
        self.assertEqual(self.resolve('commit', self.first)['commit'], self.first)

    def test_annotated_lightweight_and_moving_tags(self):
        self.git('tag', '-a', 'release', '-m', 'release')
        self.git('tag', 'lightweight')
        self.assertEqual(self.resolve('tag', 'release')['commit'], self.first)
        self.assertEqual(self.resolve('tag', 'lightweight')['commit'], self.first)
        second = self.commit('second')
        self.git('tag', '-f', '-a', 'release', '-m', 'moved')
        self.assertEqual(self.resolve('tag', 'release')['commit'], second)

    def test_version_tags_change_inputs_without_new_commits(self):
        before = self.resolve(fetch_tags=True)
        self.git('tag', 'v1')
        after = self.resolve(fetch_tags=True)
        self.assertEqual(before['commit'], after['commit'])
        self.assertNotEqual(before, after)
        self.assertIn('refs/tags/v1', after['tags'])

    def test_missing_ref_and_short_commit_are_inconclusive(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.resolve(value='missing')
        with self.assertRaises(ValueError):
            self.resolve('commit', self.first[:7])


class InputGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.baseline = self.root / 'baseline.json'
        self.current = self.root / 'current.json'
        self.report_path = self.root / 'build-report.json'
        self.snapshot = {'version': 1, 'zde_commit': 'zde-one',
                         'image_digest': 'sha256:image-one', 'image_members': [],
                         'collection_sha256': 'collection-one',
                         'implementation': {'builder.py': 'builder-one'},
                         'projects': {'owner/app': {'commit': 'app-one'},
                                      'owner/library': {'commit': 'library-one'}}}
        self.report = {'success': True, 'status': 'complete', 'zde_commit': 'zde-one',
                       'zde_image': 'image@sha256:image-one', 'collection_sha256': 'collection-one',
                       'items': [{'id': key, 'status': 'included',
                                  'source': {'current_commit': dep['commit']}}
                                 for key, dep in self.snapshot['projects'].items()],
                       'kernels': [{'status': 'included'}]}

    def write(self, path, data):
        path.write_text(json.dumps(data))

    def check(self, snapshot=None):
        with patch.object(inputs, 'collect_inputs', return_value=snapshot or self.snapshot):
            return inputs.check_inputs(self.baseline, self.current)

    def record(self):
        self.write(self.current, self.snapshot)
        self.write(self.report_path, self.report)
        return inputs.record_inputs(self.current, self.report_path, self.baseline)

    def test_first_build_then_unchanged_skips_without_advancing_baseline(self):
        self.assertTrue(self.check())
        self.assertFalse(self.baseline.exists())
        self.assertTrue(self.record())
        baseline = self.baseline.read_bytes()
        self.assertFalse(self.check())
        self.assertEqual(self.baseline.read_bytes(), baseline)

    def test_each_changed_input_requires_building(self):
        self.write(self.baseline, self.snapshot)
        for field in ['projects', 'zde_commit', 'image_digest', 'collection_sha256',
                      'implementation', 'version']:
            with self.subTest(field=field):
                changed = copy.deepcopy(self.snapshot)
                changed[field] = 'changed'
                self.assertTrue(self.check(changed))
        removed = copy.deepcopy(self.snapshot)
        removed['projects'].pop('owner/library')
        self.assertTrue(self.check(removed))

    def test_missing_or_corrupt_baseline_builds(self):
        for value in ['not json', 'null', '{}']:
            self.baseline.write_text(value)
            self.assertTrue(self.check())

    def test_remote_errors_build_and_remove_stale_candidate(self):
        self.write(self.baseline, self.snapshot)
        self.write(self.current, self.snapshot)
        for error in [OSError('offline'), ValueError('missing ref'),
                      subprocess.TimeoutExpired('git', 120)]:
            with self.subTest(error=error), patch.object(inputs, 'collect_inputs', side_effect=error):
                self.assertTrue(inputs.check_inputs(self.baseline, self.current))
                self.assertFalse(self.current.exists())
                self.assertFalse(inputs.record_inputs(self.current, self.report_path, self.baseline))
                self.assertEqual(json.loads(self.baseline.read_text()), self.snapshot)

    def test_failed_or_racing_build_does_not_advance_baseline(self):
        self.baseline.write_text('previous successful baseline')
        changes = [
            lambda r: r.update(success=False),
            lambda r: r.update(status='failed'),
            lambda r: r['kernels'][0].update(status='failed'),
            lambda r: r['items'][0]['source'].update(current_commit='moved'),
            lambda r: r['items'].pop(),
            lambda r: r.update(zde_commit='moved'),
            lambda r: r.update(zde_image='image@sha256:moved'),
            lambda r: r.update(collection_sha256='changed'),
        ]
        original = copy.deepcopy(self.report)
        for change in changes:
            self.report = copy.deepcopy(original)
            change(self.report)
            self.assertFalse(self.record())
            self.assertEqual(self.baseline.read_text(), 'previous successful baseline')

    def test_successful_image_with_known_failed_app_can_skip_unchanged_inputs(self):
        # Community images deliberately tolerate individual app build failures.
        # Their resolved source revisions still participate in the input check.
        self.report['items'][0]['status'] = 'failed'
        self.assertTrue(self.record())
        self.assertFalse(self.check())
        self.report['items'][0].pop('source')
        self.assertFalse(self.record())

    def test_amd64_and_arm64_builds_share_the_same_input_baseline(self):
        self.snapshot['image_members'] = ['sha256:amd64', 'sha256:arm64']
        for digest in ['sha256:amd64', 'sha256:arm64', 'sha256:image-one']:
            with self.subTest(digest=digest):
                self.report['zde_image'] = f'image@{digest}'
                self.assertTrue(self.record())
                self.assertFalse(self.check())

    def test_builder_hashes_change_but_documentation_does_not(self):
        (self.root / 'assets/scripts').mkdir(parents=True)
        (self.root / '.github/workflows').mkdir(parents=True)
        for name in ['build_community_image.py', 'community_image.py', 'community_inputs.py',
                     'publish_community_image.py']:
            (self.root / 'assets/scripts' / name).write_text('original')
        (self.root / '.github/workflows/community-image.yml').write_text('workflow')
        before = inputs.implementation_inputs(self.root)
        (self.root / 'README.md').write_text('documentation')
        self.assertEqual(inputs.implementation_inputs(self.root), before)
        (self.root / 'assets/scripts/community_image.py').write_text('changed')
        self.assertNotEqual(inputs.implementation_inputs(self.root), before)

    def test_collects_selected_and_required_dependencies_without_pulling_image(self):
        deps = [dict(id=key, repo='repo', ref=['branch', 'main'], fetch_tags=False,
                     selected=selected, required=required, parents=parents)
                for key, selected, required, parents in [
                    ('Zeal8bit/Zeal-8-bit-OS', False, True, []),
                    ('owner/app', True, False, ['owner/library']),
                    ('owner/library', False, False, []),
                    ('Zeal8bit/ZealFS', False, False, []),
                    ('owner/unused', False, False, [])]]
        commands = []
        def capture(command):
            commands.append(list(map(str, command)))
            if command[0] == sys.executable:
                return json.dumps(deps)
            if command[0] == 'docker':
                return json.dumps({'digest': 'sha256:' + 'a' * 64, 'manifests': []})
            return 'a' * 40
        (self.root / 'collection.yml').write_text('collection')
        with patch.object(inputs, 'capture', side_effect=capture), \
                patch.object(inputs, 'implementation_inputs', return_value={}), \
                patch.object(inputs, 'project_input', side_effect=lambda dep: dict(dep, commit='a' * 40)):
            result = inputs.collect_inputs(self.root)
        self.assertEqual(set(result['projects']), {dep['id'] for dep in deps[:-1]})
        docker_commands = [c for c in commands if c[0] == 'docker']
        self.assertEqual(len(docker_commands), 1)
        self.assertEqual(docker_commands[0][1:4], ['buildx', 'imagetools', 'inspect'])

    def test_registry_index_and_single_platform_manifests(self):
        index = {'digest': 'sha256:' + 'a' * 64,
                 'manifests': [{'digest': 'sha256:' + 'b' * 64},
                               {'digest': 'sha256:' + 'c' * 64}]}
        with patch.object(inputs, 'capture', return_value=json.dumps(index)):
            self.assertEqual(inputs.image_inputs(),
                             {'image_digest': index['digest'],
                              'image_members': ['sha256:' + 'b' * 64, 'sha256:' + 'c' * 64]})
        description = 'Name: image\nDigest: ' + index['digest'] + '\n'
        with patch.object(inputs, 'capture', side_effect=['{}', description]):
            self.assertEqual(inputs.image_inputs(),
                             {'image_digest': index['digest'], 'image_members': []})
        with patch.object(inputs, 'capture', side_effect=['{}', 'Digest: invalid']):
            with self.assertRaises(ValueError):
                inputs.image_inputs()


if __name__ == '__main__':
    unittest.main()
