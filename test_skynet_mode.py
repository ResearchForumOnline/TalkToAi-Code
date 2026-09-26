import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import skynet_mode


class SkynetModeTests(unittest.TestCase):
    def test_exported_godot_project_preserves_playable_inputs(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as candidate:
            root = Path(folder)
            files = {'project.godot': b'[application]\nconfig/name="Fixture"\n',
                     'AGENTS.md': b'Use Godot 4.',
                     'scripts/player.gd': b'extends CharacterBody3D\n',
                     'scenes/main.tscn': b'[gd_scene format=3]\n',
                     'assets/pixel.png': b'\x89PNG\r\n\x1a\n',
                     '.godot/imported/cache.gd': b'generated',
                     '.env.production': b'SECRET=private',
                     'secrets/settings.json': b'{}'}
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            manifest = skynet_mode._copy_candidate(root, Path(candidate))
            self.assertEqual(set(manifest), {'project.godot', 'AGENTS.md', 'scripts/player.gd',
                                            'scenes/main.tscn', 'assets/pixel.png'})
            self.assertEqual((Path(candidate) / 'assets/pixel.png').read_bytes(), files['assets/pixel.png'])

    def test_large_asset_does_not_silently_create_broken_candidate(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as candidate:
            root = Path(folder)
            (root / 'project.godot').write_text('[application]')
            (root / 'huge.png').write_bytes(b'x' * 64)
            with patch.object(skynet_mode, 'MAX_FILE_BYTES', 32):
                with self.assertRaisesRegex(ValueError, 'huge.png'):
                    skynet_mode._copy_candidate(root, Path(candidate))

    def test_binary_changes_are_reported_without_corrupt_text_diff(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as candidate:
            root, target = Path(folder), Path(candidate)
            (root / 'sprite.png').write_bytes(b'\x89PNG\x00before')
            baseline = skynet_mode._copy_candidate(root, target)
            (target / 'sprite.png').write_bytes(b'\x89PNG\x00after')
            changes = skynet_mode._changes(target, baseline)
            diff = skynet_mode._diff(root, target, changes)
            self.assertIn('Binary asset modified: sprite.png', diff)
            self.assertIn(changes[0]['sha256'], diff)
            self.assertNotIn('\x00', diff)

    def test_scan_limit_gives_actionable_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('one.py', 'two.py'):
                (root / name).write_text('pass')
            with patch.object(skynet_mode, 'MAX_SCANNED_FILES', 1):
                with self.assertRaisesRegex(ValueError, 'specific app or game folder'):
                    skynet_mode._source_paths(root)

    def test_git_candidate_uses_tracked_source_only(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as candidate:
            root = Path(folder)
            subprocess.run(['git', 'init', '-q'], cwd=root, check=True, capture_output=True)
            (root / 'tracked.py').write_text('value = 1\n', encoding='utf-8')
            (root / 'untracked.py').write_text('value = 2\n', encoding='utf-8')
            subprocess.run(['git', 'add', 'tracked.py'], cwd=root, check=True, capture_output=True)
            manifest = skynet_mode._copy_candidate(root, Path(candidate))
            self.assertEqual(list(manifest), ['tracked.py'])

    def test_candidate_is_bounded_and_excludes_credentials(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as candidate:
            root = Path(folder)
            (root / 'app.py').write_text('print("before")\n', encoding='utf-8')
            (root / '.env').write_text('PRIVATE=1', encoding='utf-8')
            (root / 'credentials.json').write_text('{}', encoding='utf-8')
            (root / 'build').mkdir()
            (root / 'build' / 'artifact.py').write_text('generated', encoding='utf-8')
            manifest = skynet_mode._copy_candidate(root, Path(candidate))
            self.assertEqual(list(manifest), ['app.py'])
            self.assertFalse((Path(candidate) / '.env').exists())
            self.assertFalse(skynet_mode._eligible(Path('.talktoai-code/checkpoints/record.json')))

    def test_improvement_produces_reviewable_candidate_without_replacing_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'app.py').write_text('answer = 1\n', encoding='utf-8')
            events = []

            def fake_agent(url, model, history, project, act, cancel, emit, rounds, performance, tools, **kwargs):
                self.assertTrue(kwargs['improvement_mode'])
                (Path(project) / 'app.py').write_text('answer = 2\n', encoding='utf-8')

            with patch.object(skynet_mode, '_run_agent', side_effect=fake_agent), \
                 patch.object(skynet_mode.ProjectTools, 'execute', return_value='Exit 0\nRan 1 test\nOK'):
                report = skynet_mode.run_improvement('local', 'fixture', root, 'Improve answer',
                                                     threading.Event(), lambda kind, value: events.append((kind, value)),
                                                     max_iterations=1)
            self.assertEqual((root / 'app.py').read_text(), 'answer = 1\n')
            self.assertEqual((Path(report['candidate_dir']) / 'app.py').read_text(), 'answer = 2\n')
            self.assertEqual(report['iterations'][0]['checks']['status'], 'passed')
            self.assertIn('answer = 2', Path(report['diff_path']).read_text())
            self.assertTrue(json.loads(Path(report['report_path']).read_text())['review_required'])
            self.assertTrue(any(kind == 'skynet_report' for kind, _ in events))

    def test_budget_and_goal_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            for goal, iterations in (('', 1), ('Goal', 0), ('Goal', 4)):
                with self.assertRaises(ValueError):
                    skynet_mode.run_improvement('local', 'fixture', folder, goal,
                                                threading.Event(), lambda *_: None,
                                                max_iterations=iterations)

    def test_missing_project_checks_are_unverified(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'app.py').write_text('answer = 1\n', encoding='utf-8')

            def fake_agent(url, model, history, project, act, cancel, emit, rounds, performance, tools, **kwargs):
                (Path(project) / 'app.py').write_text('answer = 2\n', encoding='utf-8')

            with patch.object(skynet_mode, '_run_agent', side_effect=fake_agent), \
                 patch.object(skynet_mode.ProjectTools, 'execute', side_effect=ValueError('No supported checks detected')):
                report = skynet_mode.run_improvement('local', 'fixture', root, 'Improve answer',
                                                     threading.Event(), lambda *_: None, max_iterations=1)
            self.assertEqual(report['iterations'][0]['checks']['status'], 'unverified')


if __name__ == '__main__':
    unittest.main()
