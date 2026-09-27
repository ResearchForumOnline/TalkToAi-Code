"""Regression checks for source evidence outside the editor tool path."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_core as core
from workspace_change_evidence import WorkspaceChangeTracker


class WorkspaceEvidenceTests(unittest.TestCase):
    def test_detects_shell_style_source_change_without_reading_private_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'main.gd').write_text('extends Node\n', encoding='utf-8')
            (root / '.env').write_text('private', encoding='utf-8')
            (root / 'secret_token.py').write_text('private', encoding='utf-8')
            tracker = WorkspaceChangeTracker(root)
            (root / 'main.gd').write_text('extends Node2D\n', encoding='utf-8')
            (root / '.env').write_text('changed', encoding='utf-8')
            (root / 'secret_token.py').write_text('changed', encoding='utf-8')
            report = tracker.observe('run_command')
            self.assertEqual(report['paths'], ['main.gd'])
            self.assertEqual(report['count'], 1)
            self.assertTrue(report['complete'])

    def test_incomplete_baseline_does_not_claim_preexisting_file_is_new(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'a.py').write_text('a', encoding='utf-8')
            (root / 'b.py').write_text('b', encoding='utf-8')
            tracker = WorkspaceChangeTracker(root, max_files=1)
            report = tracker.observe('run_command')
            self.assertFalse(report['complete'])
            self.assertEqual(report['count'], 0)

    def test_private_directory_contents_are_never_opened_for_hashing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            for directory in ('credentials', 'secrets', '.ssh', '.aws', 'browser-profile', 'User Data'):
                private = root / directory
                private.mkdir()
                (private / 'innocent_name.py').write_text('private material', encoding='utf-8')
            (root / 'safe.py').write_text('safe source', encoding='utf-8')
            opened = []
            original = Path.read_bytes

            def observe_open(path):
                opened.append(Path(path).relative_to(root).as_posix())
                return original(path)

            with patch.object(Path, 'read_bytes', observe_open):
                tracker = WorkspaceChangeTracker(root)
                tracker.observe('run_command')
            self.assertEqual(opened, ['safe.py', 'safe.py'])
            self.assertEqual(list(tracker.baseline), ['safe.py'])

    def test_shell_written_source_requests_a_fresh_check_before_completion(self):
        events = []
        calls = [
            {'function': {'name': 'run_command', 'arguments': {'command': 'make change'}}, 'id': 'c1'},
            {'function': {'name': 'run_checks', 'arguments': {}}, 'id': 'c2'},
        ]
        responses = [
            {'message': {'content': '', 'tool_calls': [calls[0]]}, 'done': True},
            {'message': {'content': 'Done.'}, 'done': True},
            {'message': {'content': '', 'tool_calls': [calls[1]]}, 'done': True},
            {'message': {'content': 'Verified.'}, 'done': True},
        ]
        supplied = iter(responses)

        def stream(*_args):
            yield next(supplied)

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, 'game.gd')
            source.write_text('old', encoding='utf-8')
            original = core.ProjectTools.execute

            def execute(tools, name, args):
                if name == 'run_command':
                    source.write_text('new', encoding='utf-8')
                    return 'Exit 0\n'
                if name == 'run_checks':
                    return 'Exit 0\nPassed'
                return original(tools, name, args)

            with patch.object(core, 'stream_chat', side_effect=stream), \
                 patch.object(core, 'AUTO_CONTEXT', False), \
                 patch.object(core, 'model_supports_vision', return_value=False), \
                 patch.object(core.ProjectTools, 'execute', execute):
                core.run_agent('fixture', 'fixture', [{'role': 'user', 'content': 'Change game.'}],
                               folder, True, threading.Event(), lambda kind, value: events.append((kind, value)),
                               rounds=4)

        workspace = [value for kind, value in events if kind == 'workspace_changes']
        self.assertEqual(workspace[-1]['paths'], ['game.gd'])
        self.assertTrue(any(kind == 'status' and value == 'Verifying changes before finishing'
                            for kind, value in events))
        self.assertEqual([value for kind, value in events if kind == 'status'][-1], 'Ready')

    def test_incomplete_scan_cannot_clear_an_unobserved_shell_change(self):
        events = []
        responses = iter([
            {'message': {'content': '', 'tool_calls': [
                {'id': 'c1', 'function': {'name': 'run_command', 'arguments': {'command': 'create source'}}}]}, 'done': True},
            {'message': {'content': 'Done.'}, 'done': True},
            {'message': {'content': 'Still done.'}, 'done': True},
        ])

        def stream(*_args):
            yield next(responses)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'a.py').write_text('known', encoding='utf-8')
            (root / 'b.py').write_text('preexisting, beyond scan cap', encoding='utf-8')
            original = core.ProjectTools.execute

            def execute(tools, name, args):
                if name == 'run_command':
                    (root / 'c.py').write_text('new, unobserved', encoding='utf-8')
                    return 'Exit 0\n'
                return original(tools, name, args)

            def limited(root_path):
                return WorkspaceChangeTracker(root_path, max_files=1)

            with patch.object(core, 'stream_chat', side_effect=stream), \
                 patch.object(core, 'AUTO_CONTEXT', False), \
                 patch.object(core, 'model_supports_vision', return_value=False), \
                 patch.object(core, 'WorkspaceChangeTracker', side_effect=limited), \
                 patch.object(core.ProjectTools, 'execute', execute):
                core.run_agent('fixture', 'fixture', [{'role': 'user', 'content': 'Create source.'}],
                               folder, True, threading.Event(), lambda kind, value: events.append((kind, value)),
                               rounds=2, keep_going=True)

        checkpoints = [value for kind, value in events if kind == 'goal_checkpoint']
        self.assertEqual(checkpoints[-1]['state'], 'paused')
        self.assertTrue(checkpoints[-1]['workspace_change_uncertain'])
        self.assertFalse(checkpoints[-1]['workspace_changes']['complete'])
        self.assertIn('verification remains unfinished', ' '.join(checkpoints[-1]['blockers']))
        self.assertFalse(any(kind == 'status' and value == 'Ready' for kind, value in events))

    def test_failed_shell_action_is_named_in_pause_checkpoint(self):
        events = []
        responses = iter([
            {'message': {'content': '', 'tool_calls': [
                {'id': 'c1', 'function': {'name': 'run_command', 'arguments': {'command': 'bad command'}}}]}, 'done': True},
            {'message': {'content': 'Done.'}, 'done': True},
            {'message': {'content': 'Still done.'}, 'done': True},
        ])

        def stream(*_args):
            yield next(responses)

        with tempfile.TemporaryDirectory() as folder:
            original = core.ProjectTools.execute

            def execute(tools, name, args):
                if name == 'run_command':
                    return 'Exit 1\nprivate diagnostic that must not enter the checkpoint'
                return original(tools, name, args)

            with patch.object(core, 'stream_chat', side_effect=stream), \
                 patch.object(core, 'AUTO_CONTEXT', False), \
                 patch.object(core, 'model_supports_vision', return_value=False), \
                 patch.object(core.ProjectTools, 'execute', execute):
                core.run_agent('fixture', 'fixture', [{'role': 'user', 'content': 'Build something.'}],
                               folder, True, threading.Event(), lambda kind, value: events.append((kind, value)),
                               rounds=3, keep_going=True)

        checkpoint = [value for kind, value in events if kind == 'goal_checkpoint'][-1]
        self.assertEqual(checkpoint['state'], 'paused')
        self.assertEqual(checkpoint['last_tool_error'], 'run_command: exit 1')
        self.assertNotIn('private diagnostic', str(checkpoint))

    def test_hard_cap_emits_app_pause_report_after_edit_and_passed_check(self):
        events = []
        responses = iter([
            {'message': {'content': '', 'tool_calls': [
                {'id': 'c1', 'function': {'name': 'edit_file', 'arguments': {
                    'path': 'calculator.py', 'old_text': 'return 0', 'new_text': 'return 2'}}}]}, 'done': True},
            {'message': {'content': '', 'tool_calls': [
                {'id': 'c2', 'function': {'name': 'run_checks', 'arguments': {}}}]}, 'done': True},
        ])

        def stream(*_args):
            yield next(responses)

        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'calculator.py').write_text('def value():\n    return 0\n', encoding='utf-8')
            original = core.ProjectTools.execute

            def execute(tools, name, args):
                if name == 'run_checks':
                    return 'Exit 0\n2 tests passed'
                return original(tools, name, args)

            with patch.object(core, 'stream_chat', side_effect=stream), \
                 patch.object(core, 'AUTO_CONTEXT', False), \
                 patch.object(core, 'model_supports_vision', return_value=False), \
                 patch.object(core.ProjectTools, 'execute', execute):
                core.run_agent('fixture', 'fixture', [{'role': 'user', 'content': 'Fix the value.'}],
                               folder, True, threading.Event(), lambda kind, value: events.append((kind, value)),
                               rounds=2)

        summaries = [value for kind, value in events if kind == 'run_summary']
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]['state'], 'paused')
        self.assertEqual(summaries[0]['reason'], 'step_budget')
        self.assertEqual(summaries[0]['steps'], 2)
        self.assertEqual(summaries[0]['editor_changes'], 1)
        self.assertEqual(summaries[0]['workspace_changes']['paths'], ['calculator.py'])
        self.assertEqual(summaries[0]['verification']['status'], 'passed')
        self.assertFalse(any(kind == 'status' and value == 'Ready' for kind, value in events))


if __name__ == '__main__':
    unittest.main()
