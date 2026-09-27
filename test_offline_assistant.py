import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from offline_assistant import OFFLINE_COMMANDS, ScanLimits, main, recognize_request, run_offline


class OfflineAssistantTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def run_skill(self, command, **kwargs):
        result = run_offline(self.root, command=command, **kwargs)
        self.assertEqual(result['metrics']['model_calls'], 0)
        self.assertEqual(result['metrics']['network_calls'], 0)
        json.dumps(result)
        return result

    def test_recognizer_exact_intents_and_compound_requests(self):
        expected = {'Check this project locally': 'quick_check', 'What is in this project?': 'project_report', 'Please show TODOs.': 'todo_report',
                    'Check Python syntax': 'python_syntax', 'Show Git changes': 'git_changes',
                    'Find files matching *.gd': 'find_files', 'Find symbol Player': 'find_symbols'}
        for request, command in expected.items():
            with self.subTest(request=request):
                self.assertEqual(recognize_request(request)['command'], command)
        for request in ('Make me a game', 'Check Python syntax and fix errors',
                        'Do not show TODOs', 'Show Git changes then push',
                        'Find symbol player and rewrite it', 'Find files ../../secret.txt',
                        '/local git_changes && git push', '/local unknown', 'Explain what “show TODOs” does'):
            with self.subTest(request=request):
                self.assertIsNone(recognize_request(request))

    def test_glob_question_mark_and_slash_are_preserved(self):
        self.assertEqual(recognize_request('Find files config.?'), {'command': 'find_files', 'query': 'config.?'})
        self.assertEqual(recognize_request('/local find_files *.py'), {'command': 'find_files', 'query': '*.py'})
        self.assertEqual(recognize_request('/local project_report'), {'command': 'project_report', 'query': ''})

    def test_report_finds_nested_projects_and_only_script_names(self):
        self.write('web/package.json', json.dumps({'scripts': {'start': 'echo PRIVATE-MARKER', 'test': 'pytest'}}))
        self.write('game/project.godot', 'config_version=5\n')
        self.write('game/player.gd', 'func jump():\n    pass\n')
        self.write('app.py', 'print("never execute")\n')
        self.write('tests/test_app.py', 'assert True\n')
        self.write('build.gradle', '// build metadata')
        result = self.run_skill('project_report')
        rendered = '\n'.join(result['details'])
        self.assertIn('GDScript (1 files)', rendered)
        self.assertIn('game/project.godot', rendered)
        self.assertIn('build.gradle', rendered)
        self.assertIn('start, test', rendered)
        self.assertNotIn('PRIVATE-MARKER', rendered)
        self.assertEqual(result['status'], 'completed')

    def test_unsafe_and_generated_files_never_read_or_listed(self):
        self.write('main.py', 'def greet():\n    pass\n')
        private = ['.env', 'private/notes.py', 'passwords.txt', 'api_key.py', 'auth.json',
                   'credentials/token.py', '.ssh/id_rsa', 'build-cache/stale.py',
                   'node_modules/package.js', 'config.json', 'server.key']
        for name in private:
            self.write(name, 'PRIVATE-MARKER')
        result = self.run_skill('find_files', query='*')
        self.assertEqual(result['details'], ['main.py'])
        self.assertEqual(result['metrics']['files_read'], 0)

    def test_python_symbols_use_real_ast_and_never_execute(self):
        marker = self.root / 'executed.txt'
        self.write('app.py', f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\n'
                   'class Player:\n    def player_move(self):\n        pass\n# def player_fake(): pass\n')
        result = self.run_skill('find_symbols', query='player')
        self.assertEqual(len(result['details']), 2)
        self.assertIn('Player', result['details'][0])
        self.assertNotIn('player_fake', str(result))
        self.assertFalse(marker.exists())

    def test_cross_language_symbol_locations(self):
        self.write('player.gd', 'class_name Player\nfunc player_move():\n    pass\n')
        self.write('view.ts', 'export function PlayerView() { return null; }\n')
        result = self.run_skill('find_symbols', query='player')
        self.assertEqual(len(result['details']), 3)
        self.assertIn('player.gd:2', str(result['details']))

    def test_invalid_python_symbol_scan_reports_partial_scope(self):
        self.write('broken.py', 'def player(\n')
        result = self.run_skill('find_symbols', query='player')
        self.assertEqual(result['status'], 'partial')
        self.assertIn('could not be parsed', str(result['details']))

    def test_todo_notes_exclude_sensitive_lines_and_identifiers(self):
        self.write('app.py', '# TODO: add a settings menu\n# FIXME: password=PRIVATE-MARKER\nTODO_COUNT = 3\n')
        result = self.run_skill('todo_report')
        self.assertEqual(len(result['details']), 1)
        self.assertIn('settings menu', result['details'][0])
        self.assertNotIn('PRIVATE-MARKER', str(result))

    def test_syntax_compile_finds_top_level_return_without_execution(self):
        marker = self.root / 'executed.txt'
        self.write('valid.py', f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
        self.write('broken.py', 'return 1\n')
        result = self.run_skill('python_syntax')
        self.assertEqual(result['findings'], 1)
        self.assertIn('outside function', str(result['details']))
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / '__pycache__').exists())

    def test_missing_project_query_and_unsupported_are_friendly(self):
        self.assertEqual(run_offline(None, command='project_report')['status'], 'needs_project')
        self.assertEqual(run_offline(self.root / 'missing', command='todo_report')['status'], 'needs_project')
        self.assertEqual(self.run_skill('find_files')['status'], 'needs_input')
        self.assertEqual(run_offline(self.root, request='Build a game')['status'], 'unsupported')
        self.assertEqual(len(run_offline(self.root, request='help')['actions']), len(OFFLINE_COMMANDS))

    def test_pre_cancel_does_not_scan(self):
        cancelled = threading.Event(); cancelled.set()
        with mock.patch('offline_assistant.os.scandir', side_effect=AssertionError('must not scan')):
            self.assertEqual(self.run_skill('project_report', cancel=cancelled)['status'], 'cancelled')

    def test_cancel_during_scan_returns_cancelled(self):
        class CancelAfterChecks:
            calls = 0
            def is_set(self):
                self.calls += 1
                return self.calls > 4
        for index in range(10):
            self.write(f'f{index}.py', 'pass')
        self.assertEqual(self.run_skill('find_files', query='*', cancel=CancelAfterChecks())['status'], 'cancelled')

    def test_file_limit_returns_useful_partial_inventory(self):
        for index in range(8):
            self.write(f'f{index}.py', 'pass')
        result = self.run_skill('find_files', query='*.py', limits=ScanLimits(max_files=3))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['metrics']['files_seen'], 3)
        self.assertEqual(len([d for d in result['details'] if d.endswith('.py')]), 3)

    def test_byte_and_result_limits_are_explicit(self):
        self.write('a.py', '# TODO: first\n')
        self.write('b.py', '# TODO: second\n')
        limited = self.run_skill('todo_report', limits=ScanLimits(max_bytes=16))
        self.assertEqual(limited['status'], 'partial')
        self.assertIn('first', str(limited))
        self.assertNotIn('second', str(limited))
        capped = self.run_skill('todo_report', limits=ScanLimits(max_results=1))
        self.assertEqual(capped['status'], 'partial')
        self.assertEqual(len([d for d in capped['details'] if 'TODO:' in d]), 1)

    def test_time_limit_is_explicit(self):
        with mock.patch('offline_assistant.time.monotonic', side_effect=[0, 99, 99, 99]):
            result = self.run_skill('project_report')
        self.assertEqual(result['status'], 'partial')

    def test_directory_and_file_symlinks_are_skipped(self):
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / 'outside.py'; secret.write_text('EXTERNAL-MARKER')
            try:
                (self.root / 'linked').symlink_to(outside, target_is_directory=True)
                (self.root / 'linked.py').symlink_to(secret)
            except OSError:
                self.skipTest('Creating symlinks is unavailable on this host')
            result = self.run_skill('find_files', query='*')
            self.assertEqual(result['details'], [])

    def test_git_missing_is_friendly(self):
        with mock.patch('offline_assistant.subprocess.Popen', side_effect=FileNotFoundError):
            self.assertEqual(self.run_skill('git_changes')['status'], 'unavailable')

    @unittest.skipUnless(shutil.which('git'), 'Git not installed')
    def test_git_reports_tracked_changes_without_refreshing_index(self):
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        def git(*args):
            return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                                  capture_output=True, creationflags=flags)
        git('init')
        self.write('app.py', 'pass\n'); self.write('credentials.json', '{}')
        git('add', '.')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Fixture')
        self.write('app.py', 'print("changed")\n'); self.write('credentials.json', '{"private":true}')
        index = self.root / '.git' / 'index'
        before = index.read_bytes(); stamp = index.stat().st_mtime_ns
        result = self.run_skill('git_changes')
        self.assertIn('app.py', str(result['details']))
        self.assertNotIn('credentials.json', str(result['details']))
        self.assertNotIn('print(', str(result['details']))
        self.assertEqual(index.read_bytes(), before)
        self.assertEqual(index.stat().st_mtime_ns, stamp)
        with mock.patch.dict(os.environ, {'GIT_DIR': str(self.root / 'missing'),
                                         'GIT_INDEX_FILE': str(self.root / 'wrong-index'),
                                         'GIT_TRACE': str(self.root / 'unexpected-trace.log')}):
            isolated = self.run_skill('git_changes')
        self.assertEqual(isolated['status'], 'completed')
        self.assertIn('app.py', str(isolated['details']))
        self.assertFalse((self.root / 'wrong-index').exists())
        self.assertFalse((self.root / 'unexpected-trace.log').exists())
        child = self.root / 'child'; child.mkdir()
        self.assertEqual(run_offline(child, command='git_changes')['status'], 'unavailable')

    def test_cli_json_and_failure_exit_code(self):
        self.write('bad.py', 'return 1\n')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(['--project', str(self.root), '--command', 'python_syntax', '--json'])
        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(output.getvalue())['findings'], 1)

    def test_quick_check_shares_inventory_and_preserves_errors(self):
        marker = self.root / 'executed.txt'
        self.write('app.py', f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
        self.write('broken.py', 'return 1\n# TODO: finish menu\n')
        real_scandir = os.scandir
        with mock.patch('offline_assistant.os.scandir', wraps=real_scandir) as scans:
            with mock.patch('offline_assistant._fixed_git', return_value=(1, b'')):
                result = self.run_skill('quick_check')
        self.assertEqual(scans.call_count, 1)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['findings'], 1)
        self.assertEqual(result['todo_count'], 1)
        self.assertIn('outside function', str(result['details']))
        self.assertIn('Git metadata is unavailable', str(result['details']))
        self.assertEqual(result['metrics']['skills_run'], ['project_report', 'python_syntax', 'todo_report', 'git_changes'])
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / '__pycache__').exists())

    def test_quick_check_reports_budget_without_pretending_all_stages_ran(self):
        self.write('main.py', '# TODO: handle errors\n')
        with mock.patch('offline_assistant._fixed_git', side_effect=AssertionError('budget exhausted')):
            result = self.run_skill('quick_check', limits=ScanLimits(max_bytes=1))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['metrics']['skills_run'], ['project_report', 'python_syntax'])
        self.assertIn('Remaining quick-check stages were not run', str(result['details']))


if __name__ == '__main__':
    unittest.main()
