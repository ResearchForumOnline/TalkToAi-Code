import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from work_packet import build_work_packet, complete_direct_read_request


class FixtureTools:
    def __init__(self, files=None, *, list_result=None, info=None):
        self.cancel = threading.Event()
        self.files = files or {}
        self.list_result = list_result
        self.info = info or {
            'project': '/fixture', 'engine': 'Godot', 'godot': '/bin/godot',
            'blender': None, 'child_projects': [],
            'checks': {'engine': 'Godot', 'commands': [], 'note': 'Import check available.'},
        }
        self.calls = []

    def execute(self, name, args):
        self.calls.append((name, args))
        if self.cancel.is_set():
            raise InterruptedError('Stopped')
        if name == 'project_info':
            return json.dumps(self.info)
        if name == 'list_files':
            return self.list_result if self.list_result is not None else '\n'.join(self.files)
        if name == 'read_project_files':
            requests = json.loads(args['requests'])
            entries = []
            for request in requests:
                path = request['path']
                if path not in self.files:
                    entries.append({'path': path, 'error': 'Missing fixture file.'})
                    continue
                content = self.files[path]
                limit = request['limit']
                entries.append({'path': path, 'content': content[:limit],
                                'bytes': len(content.encode()),
                                'sha256': hashlib.sha256(content.encode()).hexdigest(),
                                'complete': len(content) <= limit,
                                'next_offset': limit if len(content) > limit else None})
            return json.dumps({'files': entries})
        if name == 'git_changes':
            return 'git status --short -- .\n M player.gd\n?? notes.txt\ngit diff --no-ext-diff --no-textconv --stat -- .\n'
        raise AssertionError('Unexpected tool call: ' + name)


class WorkPacketTests(unittest.TestCase):
    def test_named_file_is_read_in_one_batch_after_info_and_inventory(self):
        source = {'project.godot': 'config_version=5\n',
                  'README.txt': 'Player notes and setup.\n',
                  'player.gd': 'func move():\n    pass\n'}
        tools = FixtureTools(source)
        packet = build_work_packet(tools, 'Read README.txt and explain the player code',
                                   max_files=2)
        self.assertEqual(packet['status'], 'completed')
        self.assertEqual([name for name, _ in tools.calls],
                         ['project_info', 'list_files', 'read_project_files'])
        self.assertEqual(packet['files'][0]['path'], 'README.txt')
        self.assertEqual(packet['files'][0]['sha256'], hashlib.sha256(source['README.txt'].encode()).hexdigest())
        self.assertEqual(packet['project_info']['checks']['executed'], False)

    def test_relevant_source_and_manifest_are_selected_without_user_filename(self):
        tools = FixtureTools({'project.godot': 'config_version=5',
                              'enemy.gd': 'func attack(): pass',
                              'player.gd': 'func move(): pass',
                              'README.md': 'A game.'})
        packet = build_work_packet(tools, 'Improve the player movement', max_files=3)
        names = [entry['path'] for entry in packet['files']]
        self.assertIn('player.gd', names)
        self.assertIn('project.godot', names)
        self.assertNotIn('enemy.gd', names)

    def test_duplicate_basename_does_not_guess_a_specific_file(self):
        tools = FixtureTools({'src/player.gd': 'src version',
                              'archive/player.gd': 'archive version',
                              'project.godot': 'config_version=5'})
        packet = build_work_packet(tools, 'Read player.gd', max_files=1)
        self.assertEqual(packet['files'][0]['path'], 'project.godot')
        self.assertEqual(packet['ambiguous_names'], ['archive/player.gd', 'src/player.gd'])

    def test_private_paths_are_never_selected_or_reported_and_sensitive_lines_are_redacted(self):
        tools = FixtureTools({'credentials.json': '{"token":"FAKE-SECRET"}',
                              '.env': 'PASSWORD=FAKE-SECRET',
                              'private/notes.md': 'FAKE-SECRET',
                              'player.gd': 'func move(): pass\nAPI_KEY=FAKE-SECRET\n'})
        packet = build_work_packet(tools, 'Read credentials.json and player.gd')
        encoded = json.dumps(packet)
        self.assertNotIn('FAKE-SECRET', encoded)
        self.assertNotIn('credentials.json', encoded)
        self.assertNotIn('private/notes.md', encoded)
        self.assertEqual(packet['inventory']['observed_files'], 1)
        self.assertTrue(packet['files'][0]['content_redacted'])

    def test_incomplete_file_and_scan_are_labelled(self):
        tools = FixtureTools({'long.py': 'x' * 2_000},
                             list_result='long.py\n[File scan limit reached; use a focused pattern.]')
        packet = build_work_packet(tools, 'Read long.py', per_file_chars=100)
        self.assertTrue(packet['inventory']['source_bounded'])
        self.assertTrue(packet['files'][0]['content_truncated'])
        self.assertEqual(packet['files'][0]['next_offset'], 100)

    def test_output_is_bounded_and_preserves_full_file_hash(self):
        files = {f'src/source_{index}.py': 'A' * 1_600 for index in range(8)}
        tools = FixtureTools(files)
        packet = build_work_packet(tools, 'Inspect source', max_output_bytes=2_000,
                                   per_file_chars=1_600, max_files=5)
        self.assertLessEqual(len(json.dumps(packet, ensure_ascii=False, separators=(',', ':')).encode()), 2_000)
        self.assertTrue(packet['bounds']['output_truncated'])
        for entry in packet['files']:
            self.assertEqual(entry['sha256'], hashlib.sha256(files[entry['path']].encode()).hexdigest())
            if entry['content_truncated']:
                self.assertIsNone(entry['next_offset'])

    def test_preexisting_cancel_does_not_call_tools(self):
        tools = FixtureTools({'main.py': 'pass'})
        tools.cancel.set()
        packet = build_work_packet(tools, 'Inspect main.py')
        self.assertEqual(packet['status'], 'cancelled')
        self.assertEqual(tools.calls, [])

    def test_cooperative_deadline_ends_before_later_steps(self):
        class SlowTools(FixtureTools):
            def execute(self, name, args):
                if name == 'project_info':
                    time.sleep(1.02)
                return super().execute(name, args)
        tools = SlowTools({'main.py': 'pass'})
        packet = build_work_packet(tools, 'Inspect main.py', max_seconds=1)
        self.assertEqual(packet['status'], 'timed_out')
        self.assertEqual([name for name, _ in tools.calls], ['project_info'])

    def test_git_is_opt_in_and_returns_counts_without_private_paths(self):
        tools = FixtureTools({'main.py': 'pass'})
        packet = build_work_packet(tools, 'Inspect changes', include_git=True, max_seconds=60)
        self.assertEqual(packet['git']['state'], 'repository')
        self.assertEqual(packet['git']['changed_entries'], 2)
        self.assertTrue(packet['git']['dirty'])
        self.assertNotIn('notes.txt', json.dumps(packet['git']))
        self.assertEqual(tools.calls[-1][0], 'git_changes')

    def test_git_is_skipped_when_its_own_timeout_exceeds_packet_budget(self):
        tools = FixtureTools({'main.py': 'pass'})
        packet = build_work_packet(tools, 'Inspect changes', include_git=True)
        self.assertEqual(packet['git']['state'], 'skipped_budget')
        self.assertEqual([name for name, _ in tools.calls],
                         ['project_info', 'list_files', 'read_project_files'])

    def test_real_project_tools_keeps_path_guard_and_does_not_write(self):
        from agent_core import ProjectTools
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'README.txt').write_text('Run this fixture.\n', encoding='utf-8')
            (root / 'credentials.json').write_text('FAKE-SECRET', encoding='utf-8')
            tools = ProjectTools(root, act=False)
            packet = build_work_packet(tools, 'Read README.txt and credentials.json')
            self.assertEqual(packet['status'], 'completed')
            self.assertIn('README.txt', [entry['path'] for entry in packet['files']])
            self.assertNotIn('FAKE-SECRET', json.dumps(packet))
            self.assertEqual(tools.changes, [])

    def test_complete_direct_read_request_accepts_only_fully_observed_named_files(self):
        request = ('Inspect README.txt and main.py in this selected project and explain what the '
                   'program does. Use the supplied project evidence. Do not edit files or run commands.')
        packet = build_work_packet(
            FixtureTools({'README.txt': 'Small sample.\n',
                          'main.py': 'def add(left, right):\n    return left + right\n'}),
            request, max_files=2, per_file_chars=500)
        self.assertEqual(packet['direct_reference_counts'], {'readme.txt': 1, 'main.py': 1})
        self.assertTrue(complete_direct_read_request(packet, request))
        self.assertTrue(complete_direct_read_request(packet,
            'Summarize README.txt and main.py, but do not modify them.'))

    def test_complete_direct_read_request_rejects_other_work_and_missing_references(self):
        packet = build_work_packet(FixtureTools({'README.txt': 'Instructions.', 'main.py': 'pass'}),
                                   'Explain README.txt and main.py', max_files=2)
        unsafe = (
            'Explain README.txt and main.py and edit main.py',
            'Describe main.py, then run tests',
            'Summarize README.txt and check current docs on the web',
            'Explain README.txt and research its history online',
            'Explain README.txt and overall codebase architecture',
            'Explain README.txt and compare it with main.py',
            'Explain README.txt and main.py and inspect git status',
            'Explain README.txt and missing.py',
            'Explain README.txt and release.pdf',
            'Explain README.txt and credentials.json',
            'Explain the project',
        )
        for request in unsafe:
            with self.subTest(request=request):
                self.assertFalse(complete_direct_read_request(packet, request))

    def test_complete_direct_read_request_rejects_ambiguous_partial_and_redacted_packet(self):
        request = 'Explain player.gd'
        packet = build_work_packet(
            FixtureTools({'src/player.gd': 'src', 'archive/player.gd': 'archive'}), request)
        self.assertEqual(packet['direct_reference_counts']['player.gd'], 2)
        self.assertFalse(complete_direct_read_request(packet, request))
        explicit = 'Explain src/player.gd'
        packet = build_work_packet(
            FixtureTools({'src/player.gd': 'src', 'archive/player.gd': 'archive'}), explicit)
        self.assertTrue(complete_direct_read_request(packet, explicit))

        request = 'Explain main.py'
        partial = build_work_packet(FixtureTools({'main.py': 'x' * 200}), request,
                                    per_file_chars=100)
        self.assertFalse(complete_direct_read_request(partial, request))
        redacted = build_work_packet(FixtureTools({'main.py': 'API_KEY=FAKE-SECRET\n'}), request)
        self.assertFalse(complete_direct_read_request(redacted, request))
        complete = build_work_packet(FixtureTools({'main.py': 'pass\n'}), request)
        for key, value in (('status', 'partial'), ('errors', [{'tool': 'fixture', 'error': 'bad'}])):
            altered = dict(complete, **{key: value})
            self.assertFalse(complete_direct_read_request(altered, request))
        altered = dict(complete, inventory=dict(complete['inventory'], source_bounded=True))
        self.assertFalse(complete_direct_read_request(altered, request))
        altered = dict(complete, bounds=dict(complete['bounds'], output_truncated=True))
        self.assertFalse(complete_direct_read_request(altered, request))


if __name__ == '__main__':
    unittest.main()
