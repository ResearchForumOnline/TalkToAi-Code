import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import skynet_mode


class SkynetModeTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'POSIX executable mode check')
    def test_check_chmod_cannot_select_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'app.py'
            source.write_text('value = 1\n', encoding='utf-8')
            source.chmod(0o644)

            def chmod_during_check(tools, name, args):
                (tools.root / 'app.py').chmod(0o755)
                return 'Exit 0\n1 test passed'

            with patch.object(skynet_mode.ProjectTools, 'execute', chmod_during_check):
                result, output, _ = skynet_mode._evaluate(root, threading.Event())
            self.assertEqual(result['status'], 'blocked')
            self.assertIn('source mode changes: app.py', output)
            self.assertEqual(os.stat(source).st_mode & 0o777, 0o644)

    def test_git_candidate_copies_safe_untracked_source_used_by_project(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as destination:
            root=Path(folder)
            subprocess.run(['git','init','-q',str(root)],check=True,capture_output=True)
            (root/'tracked.py').write_text('from feature import value\n',encoding='utf-8')
            (root/'feature.py').write_text('value = 7\n',encoding='utf-8')
            (root/'.gitignore').write_text('ignored.py\n',encoding='utf-8')
            (root/'ignored.py').write_text('private = True\n',encoding='utf-8')
            subprocess.run(['git','-C',str(root),'add','tracked.py','.gitignore'],check=True,capture_output=True)
            manifest=skynet_mode._copy_candidate(root,Path(destination))
            self.assertIn('feature.py',manifest)
            self.assertEqual((Path(destination)/'feature.py').read_text(encoding='utf-8'),'value = 7\n')
            self.assertNotIn('ignored.py',manifest)

    @staticmethod
    def metric_fixture(root):
        (root / 'app.py').write_text('error = 5\n', encoding='utf-8')
        (root / 'evaluate.py').write_text(
            "import json\nfrom pathlib import Path\n"
            "value = float(Path('app.py').read_text().split('=')[1].strip())\n"
            "metric = 'other' if value < 0 else 'error'\n"
            "print(json.dumps({'metric': metric, 'value': value}))\n", encoding='utf-8')
        (root / skynet_mode.CONTRACT_NAME).write_text(json.dumps({
            'schema': 'talktoai.skynet.evaluator.v1',
            'metric': 'error', 'direction': 'minimize',
            'command': ['python', 'evaluate.py'],
            'timeout_seconds': 5, 'frozen_files': ['evaluate.py'],
        }), encoding='utf-8')

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

    def test_diff_never_reads_original_symlink_outside_selected_project(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as candidate, tempfile.TemporaryDirectory() as outside:
            root, target = Path(folder), Path(candidate)
            secret = Path(outside, 'private.py')
            secret.write_text('DO_NOT_COPY_THIS_PRIVATE_CONTENT\n', encoding='utf-8')
            (root / 'app.py').write_text('answer = 1\n', encoding='utf-8')
            try:
                (root / 'config.py').symlink_to(secret)
            except (OSError, NotImplementedError):
                self.skipTest('Creating symlinks requires OS privileges')
            original_hashes = skynet_mode._copy_candidate(root, target)
            (target / 'config.py').write_text('safe = True\n', encoding='utf-8')
            diff = skynet_mode._diff(root, target, skynet_mode._changes(target, original_hashes))
            self.assertIn('safe = True', diff)
            self.assertNotIn('DO_NOT_COPY_THIS_PRIVATE_CONTENT', diff)

    def test_changed_source_size_is_checked_before_file_read(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'large.json').write_bytes(b'x' * 64)
            with patch.object(skynet_mode, 'MAX_FILE_BYTES', 32), \
                 patch.object(Path, 'read_bytes', side_effect=AssertionError('unexpected unbounded read')):
                with self.assertRaisesRegex(ValueError, '4 MB limit'):
                    skynet_mode._changes(root, {})

    def test_scan_limit_gives_actionable_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('one.py', 'two.py'):
                (root / name).write_text('pass')
            with patch.object(skynet_mode, 'MAX_SCANNED_FILES', 1):
                with self.assertRaisesRegex(ValueError, 'specific app or game folder'):
                    skynet_mode._source_paths(root)

    def test_git_candidate_includes_eligible_untracked_source(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as candidate:
            root = Path(folder)
            subprocess.run(['git', 'init', '-q'], cwd=root, check=True, capture_output=True)
            (root / 'tracked.py').write_text('value = 1\n', encoding='utf-8')
            (root / 'untracked.py').write_text('value = 2\n', encoding='utf-8')
            subprocess.run(['git', 'add', 'tracked.py'], cwd=root, check=True, capture_output=True)
            manifest = skynet_mode._copy_candidate(root, Path(candidate))
            self.assertEqual(list(manifest), ['tracked.py', 'untracked.py'])

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

    def test_later_failed_iterations_keep_the_last_passing_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'app.py').write_text('answer = 1\n', encoding='utf-8')
            proposals = iter((2, 3, 4))

            def fake_agent(url, model, history, project, act, cancel, emit, rounds, performance, tools, **kwargs):
                (Path(project) / 'app.py').write_text(f'answer = {next(proposals)}\n', encoding='utf-8')

            def fake_check(tools, name, args):
                self.assertEqual(name, 'run_checks')
                answer = (tools.root / 'app.py').read_text(encoding='utf-8')
                return 'Exit 0\n1 test passed' if 'answer = 1' in answer or 'answer = 2' in answer else 'Exit 1\n1 test failed'

            with patch.object(skynet_mode, '_run_agent', side_effect=fake_agent), \
                 patch.object(skynet_mode.ProjectTools, 'execute', fake_check):
                report = skynet_mode.run_improvement('local', 'fixture', root, 'Improve answer',
                                                     threading.Event(), lambda *_: None, max_iterations=3)

            self.assertEqual([item['checks']['status'] for item in report['iterations']],
                             ['passed', 'failed', 'failed'])
            self.assertEqual(report['selected_iteration'], 1)
            self.assertEqual((Path(report['candidate_dir']) / 'app.py').read_text(encoding='utf-8'), 'answer = 2\n')
            self.assertEqual((root / 'app.py').read_text(encoding='utf-8'), 'answer = 1\n')

    def test_test_mutation_cannot_select_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'app.py').write_text('answer = 1\n', encoding='utf-8')
            (root / 'test_app.py').write_text('assert answer == 2\n', encoding='utf-8')

            def fake_agent(url, model, history, project, act, cancel, emit, rounds, performance, tools, **kwargs):
                (Path(project) / 'app.py').write_text('answer = 2\n', encoding='utf-8')
                (Path(project) / 'test_app.py').write_text('assert True\n', encoding='utf-8')

            with patch.object(skynet_mode, '_run_agent', side_effect=fake_agent), \
                 patch.object(skynet_mode.ProjectTools, 'execute', return_value='Exit 0\n1 test passed'):
                report = skynet_mode.run_improvement('local', 'fixture', root, 'Improve answer',
                                                     threading.Event(), lambda *_: None, max_iterations=1)
            self.assertIsNone(report['selected_iteration'])
            self.assertEqual(report['iterations'][0]['checks']['status'], 'blocked')
            self.assertIn('test_app.py', report['iterations'][0]['check_output'])

    def test_evaluation_time_source_mutation_blocks_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'app.py').write_text('answer = 1\n', encoding='utf-8')

            def fake_agent(url, model, history, project, act, cancel, emit, rounds, performance, tools, **kwargs):
                (Path(project) / 'app.py').write_text('answer = 2\n', encoding='utf-8')

            def mutating_check(tools, name, args):
                (tools.root / 'app.py').write_text('answer = 999\n', encoding='utf-8')
                return 'Exit 0\n1 test passed'

            with patch.object(skynet_mode, '_run_agent', side_effect=fake_agent), \
                 patch.object(skynet_mode.ProjectTools, 'execute', mutating_check):
                report = skynet_mode.run_improvement('local', 'fixture', root, 'Improve answer',
                                                     threading.Event(), lambda *_: None, max_iterations=1)
            self.assertEqual(report['baseline_checks']['status'], 'blocked')
            self.assertEqual(report['iterations'][0]['checks']['status'], 'blocked')
            self.assertIsNone(report['selected_iteration'])
            self.assertEqual((Path(report['candidate_dir']) / 'app.py').read_text(encoding='utf-8'), 'answer = 2\n')

    def test_managed_jobs_close_before_candidate_evaluation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'app.py').write_text('answer = 1\n', encoding='utf-8')
            closed = threading.Event()
            checks = 0

            def fake_agent(url, model, history, project, act, cancel, emit, rounds, performance, tools, **kwargs):
                jobs = Mock()
                jobs.close.side_effect = closed.set
                tools.jobs = jobs
                (Path(project) / 'app.py').write_text('answer = 2\n', encoding='utf-8')

            def fake_check(tools, name, args):
                nonlocal checks
                checks += 1
                if checks > 1:
                    self.assertTrue(closed.is_set(), 'candidate evaluated while a managed job was still owned')
                return 'Exit 0\n1 test passed'

            with patch.object(skynet_mode, '_run_agent', side_effect=fake_agent), \
                 patch.object(skynet_mode.ProjectTools, 'execute', fake_check):
                report = skynet_mode.run_improvement('local', 'fixture', root, 'Improve answer',
                                                     threading.Event(), lambda *_: None, max_iterations=1)
            self.assertTrue(closed.is_set())
            self.assertEqual(report['iterations'][0]['checks']['status'], 'passed')

    def test_final_diff_uses_frozen_baseline_when_original_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'app.py').write_text('answer = 1\n', encoding='utf-8')

            def fake_agent(url, model, history, project, act, cancel, emit, rounds, performance, tools, **kwargs):
                (Path(project) / 'app.py').write_text('answer = 2\n', encoding='utf-8')
                (root / 'app.py').write_text('outside later change\n', encoding='utf-8')

            with patch.object(skynet_mode, '_run_agent', side_effect=fake_agent), \
                 patch.object(skynet_mode.ProjectTools, 'execute', return_value='Exit 0\n1 test passed'):
                report = skynet_mode.run_improvement('local', 'fixture', root, 'Improve answer',
                                                     threading.Event(), lambda *_: None, max_iterations=1)
            diff = Path(report['diff_path']).read_text(encoding='utf-8')
            self.assertIn('-answer = 1', diff)
            self.assertIn('+answer = 2', diff)
            self.assertNotIn('outside later change', diff)

    def test_frozen_metric_selects_strictly_better_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.metric_fixture(root)
            proposals = iter((4, 6, 3))

            def fake_agent(url, model, history, project, act, cancel, emit, rounds, performance, tools, **kwargs):
                (Path(project) / 'app.py').write_text(f'error = {next(proposals)}\n', encoding='utf-8')

            with patch.object(skynet_mode, '_run_agent', side_effect=fake_agent), \
                 patch.object(skynet_mode.ProjectTools, 'execute', return_value='Exit 0\n1 test passed'):
                report = skynet_mode.run_improvement('local', 'fixture', root, 'Reduce error',
                                                     threading.Event(), lambda *_: None, max_iterations=3)

            self.assertEqual(report['evaluation_mode'], 'metric')
            self.assertEqual(report['baseline_metric']['value'], 5.0)
            self.assertEqual([item['metric']['value'] for item in report['iterations']], [4.0, 6.0, 3.0])
            self.assertEqual(report['selected_iteration'], 3)
            self.assertEqual(report['selected_metric']['value'], 3.0)
            self.assertEqual((Path(report['candidate_dir']) / 'app.py').read_text(encoding='utf-8'), 'error = 3\n')
            self.assertEqual((root / 'app.py').read_text(encoding='utf-8'), 'error = 5\n')

    def test_missing_metric_cannot_replace_prior_passing_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.metric_fixture(root)
            proposals = iter((4, -1))

            def fake_agent(url, model, history, project, act, cancel, emit, rounds, performance, tools, **kwargs):
                (Path(project) / 'app.py').write_text(f'error = {next(proposals)}\n', encoding='utf-8')

            with patch.object(skynet_mode, '_run_agent', side_effect=fake_agent), \
                 patch.object(skynet_mode.ProjectTools, 'execute', return_value='Exit 0\n1 test passed'):
                report = skynet_mode.run_improvement('local', 'fixture', root, 'Reduce error',
                                                     threading.Event(), lambda *_: None, max_iterations=2)
            self.assertEqual(report['selected_iteration'], 1)
            self.assertEqual(report['iterations'][1]['metric']['status'], 'failed')
            self.assertEqual((Path(report['candidate_dir']) / 'app.py').read_text(encoding='utf-8'), 'error = 4\n')

    def test_missing_baseline_metric_cannot_claim_improvement(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.metric_fixture(root)
            calls = 0

            def fake_agent(url, model, history, project, act, cancel, emit, rounds, performance, tools, **kwargs):
                (Path(project) / 'app.py').write_text('error = 1\n', encoding='utf-8')

            def fake_check(tools, name, args):
                nonlocal calls
                calls += 1
                return 'Exit 1\nBaseline check failed' if calls == 1 else 'Exit 0\n1 test passed'

            with patch.object(skynet_mode, '_run_agent', side_effect=fake_agent), \
                 patch.object(skynet_mode.ProjectTools, 'execute', fake_check):
                report = skynet_mode.run_improvement('local', 'fixture', root, 'Reduce error',
                                                     threading.Event(), lambda *_: None, max_iterations=1)
            self.assertIsNone(report['baseline_metric'])
            self.assertEqual(report['iterations'][0]['metric']['value'], 1.0)
            self.assertIsNone(report['selected_iteration'])
            self.assertIn('Baseline metric unavailable', report['selection_basis'])
            self.assertIn('Baseline metric unavailable', report['iterations'][0]['selection_reason'])

    def test_maximize_direction_rejects_lower_scoring_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.metric_fixture(root)
            contract_path = root / skynet_mode.CONTRACT_NAME
            contract = json.loads(contract_path.read_text(encoding='utf-8'))
            contract['direction'] = 'maximize'
            contract_path.write_text(json.dumps(contract), encoding='utf-8')
            proposals = iter((6, 4))

            def fake_agent(url, model, history, project, act, cancel, emit, rounds, performance, tools, **kwargs):
                (Path(project) / 'app.py').write_text(f'error = {next(proposals)}\n', encoding='utf-8')

            with patch.object(skynet_mode, '_run_agent', side_effect=fake_agent), \
                 patch.object(skynet_mode.ProjectTools, 'execute', return_value='Exit 0\n1 test passed'):
                report = skynet_mode.run_improvement('local', 'fixture', root, 'Raise score',
                                                     threading.Event(), lambda *_: None, max_iterations=2)
            self.assertEqual(report['selected_iteration'], 1)
            self.assertEqual(report['selected_metric']['value'], 6.0)

    def test_frozen_evaluator_mutation_blocks_scoring(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.metric_fixture(root)

            def fake_agent(url, model, history, project, act, cancel, emit, rounds, performance, tools, **kwargs):
                (Path(project) / 'app.py').write_text('error = 1\n', encoding='utf-8')
                (Path(project) / 'evaluate.py').write_text("print('{\"metric\":\"error\",\"value\":0}')\n", encoding='utf-8')

            with patch.object(skynet_mode, '_run_agent', side_effect=fake_agent), \
                 patch.object(skynet_mode.ProjectTools, 'execute', return_value='Exit 0\n1 test passed'):
                report = skynet_mode.run_improvement('local', 'fixture', root, 'Reduce error',
                                                     threading.Event(), lambda *_: None, max_iterations=1)
            self.assertIsNone(report['selected_iteration'])
            self.assertEqual(report['iterations'][0]['checks']['status'], 'blocked')
            self.assertIn('evaluate.py', report['iterations'][0]['check_output'])

    def test_evaluator_source_mutation_in_disposable_copy_blocks_score(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.metric_fixture(root)
            script = root / 'evaluate.py'
            script.write_text(script.read_text(encoding='utf-8').replace(
                "metric = 'other' if value < 0 else 'error'",
                "if value < 5: Path('app.py').write_text('error = 0\\n')\nmetric = 'error'"), encoding='utf-8')

            def fake_agent(url, model, history, project, act, cancel, emit, rounds, performance, tools, **kwargs):
                (Path(project) / 'app.py').write_text('error = 1\n', encoding='utf-8')

            with patch.object(skynet_mode, '_run_agent', side_effect=fake_agent), \
                 patch.object(skynet_mode.ProjectTools, 'execute', return_value='Exit 0\n1 test passed'):
                report = skynet_mode.run_improvement('local', 'fixture', root, 'Reduce error',
                                                     threading.Event(), lambda *_: None, max_iterations=1)
            self.assertEqual(report['baseline_metric']['status'], 'measured')
            self.assertEqual(report['iterations'][0]['checks']['status'], 'blocked')
            self.assertIsNone(report['selected_iteration'])

    def test_cancel_during_evaluator_stops_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.metric_fixture(root)
            script = root / 'evaluate.py'
            script.write_text(script.read_text(encoding='utf-8').replace(
                "metric = 'other' if value < 0 else 'error'",
                "import time\nif value < 5: time.sleep(5)\nmetric = 'error'"), encoding='utf-8')
            cancel = threading.Event()
            timer = None

            def fake_agent(url, model, history, project, act, stop, emit, rounds, performance, tools, **kwargs):
                nonlocal timer
                (Path(project) / 'app.py').write_text('error = 1\n', encoding='utf-8')
                timer = threading.Timer(.2, cancel.set)
                timer.start()

            try:
                with patch.object(skynet_mode, '_run_agent', side_effect=fake_agent), \
                     patch.object(skynet_mode.ProjectTools, 'execute', return_value='Exit 0\n1 test passed'):
                    report = skynet_mode.run_improvement('local', 'fixture', root, 'Reduce error',
                                                         cancel, lambda *_: None, max_iterations=1)
            finally:
                if timer:
                    timer.cancel()
            self.assertTrue(report['cancelled'])
            self.assertIsNone(report['selected_iteration'])
            self.assertIn(report['iterations'][0]['metric']['status'], ('cancelled', 'unverified'))

    def test_invalid_contract_command_is_rejected_before_model_call(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.metric_fixture(root)
            path = root / skynet_mode.CONTRACT_NAME
            data = json.loads(path.read_text(encoding='utf-8'))
            data['command'] = ['python', '-c']
            path.write_text(json.dumps(data), encoding='utf-8')
            with patch.object(skynet_mode, '_run_agent') as model:
                with self.assertRaisesRegex(ValueError, 'Evaluator command'):
                    skynet_mode.run_improvement('local', 'fixture', root, 'Reduce error',
                                               threading.Event(), lambda *_: None, max_iterations=1)
            model.assert_not_called()

    def test_packaged_python_resolution_prefers_real_windows_python(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            windows_apps = root / 'WindowsApps'
            installed = root / 'Python312'
            windows_apps.mkdir()
            installed.mkdir()
            alias = windows_apps / 'python.exe'
            real = installed / 'python3.exe'
            alias.write_bytes(b'alias')
            real.write_bytes(b'python')
            found = {'python': str(alias), 'python3': str(real)}
            with patch.object(skynet_mode.sys, 'frozen', True, create=True), \
                 patch.object(skynet_mode.shutil, 'which', side_effect=lambda name: found[name]) as which:
                self.assertEqual(skynet_mode._resolve_metric_python('nt'), str(real))
            self.assertEqual([call.args[0] for call in which.call_args_list], ['python', 'python3'])
            found['python'] = str(real)
            with patch.object(skynet_mode.sys, 'frozen', True, create=True), \
                 patch.object(skynet_mode.shutil, 'which', side_effect=lambda name: found[name]) as which:
                self.assertEqual(skynet_mode._resolve_metric_python('nt'), str(real))
            self.assertEqual([call.args[0] for call in which.call_args_list], ['python'])

    def test_missing_packaged_python_never_starts_metric_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'evaluate.py').write_text('print(1)\n', encoding='utf-8')
            contract = {'command': ['python', 'evaluate.py'], 'timeout_seconds': 1,
                        'metric': 'error', 'direction': 'minimize'}
            with patch.object(skynet_mode, '_resolve_metric_python', return_value=None), \
                 patch.object(skynet_mode.subprocess, 'Popen') as popen:
                result = skynet_mode._run_metric_contract(root, contract, threading.Event())
            self.assertEqual(result['status'], 'unverified')
            popen.assert_not_called()

    def test_metric_runner_uses_resolved_python_without_launcher_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'evaluate.py').write_text('print(1)\n', encoding='utf-8')
            contract = {'command': ['python', 'evaluate.py'], 'timeout_seconds': 1,
                        'metric': 'error', 'direction': 'minimize'}
            fake = Mock()
            fake.poll.return_value = 0
            fake.returncode = 0
            with patch.object(skynet_mode, '_resolve_metric_python', return_value='C:/Python312/python.exe'), \
                 patch.object(skynet_mode.subprocess, 'Popen', return_value=fake) as popen:
                result = skynet_mode._run_metric_contract(root, contract, threading.Event())
            self.assertEqual(result['status'], 'failed')  # Empty mocked stdout is not a metric.
            self.assertEqual(popen.call_args.args[0], ['C:/Python312/python.exe', '-B', 'evaluate.py'])


if __name__ == '__main__':
    unittest.main()
