import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tool_chains import invoke_chain, run_tool_chain


def steps(*items):
    return json.dumps([{'tool': name, 'args': args} for name, args in items])


class FakeTools:
    def __init__(self, answers=None):
        self.cancel = threading.Event()
        self.browser = None
        self.answers = answers or {}
        self.calls = []

    def execute(self, name, args):
        self.calls.append((name, args))
        answer = self.answers.get(name, 'ok')
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            return answer(self, args)
        return answer


class ToolChainTests(unittest.TestCase):
    def test_invokes_in_order_and_returns_structured_evidence(self):
        tools = FakeTools({'project_info': '{"engine":"Godot"}', 'list_files': 'project.godot'})
        original_cancel = tools.cancel
        result = json.loads(invoke_chain(tools, steps(('project_info', {}), ('list_files', {'pattern': '*.godot'}))))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual((result['completed'], result['failed'], result['skipped']), (2, 0, 0))
        self.assertEqual([call[0] for call in tools.calls], ['project_info', 'list_files'])
        self.assertEqual(result['steps'][1]['result'], 'project.godot')
        self.assertEqual(result['steps'][1]['result_sha256'], hashlib.sha256(b'project.godot').hexdigest())
        self.assertFalse(result['steps'][1]['truncated'])
        self.assertIs(tools.cancel, original_cancel)

    def test_invalid_later_step_is_rejected_before_any_execution(self):
        tools = FakeTools()
        with self.assertRaisesRegex(ValueError, 'read-only chains'):
            run_tool_chain(tools, steps(('project_info', {}), ('write_file', {'path': 'x', 'content': 'x'})))
        self.assertEqual(tools.calls, [])
        with self.assertRaisesRegex(ValueError, '1-6'):
            run_tool_chain(tools, json.dumps([{'tool': 'project_info', 'args': {}}] * 7))

    def test_no_argument_step_can_omit_args_for_small_models(self):
        tools = FakeTools()
        result = run_tool_chain(tools, '[{"tool":"project_info"}]')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(tools.calls, [('project_info', {})])

    def test_browser_chain_is_search_read_only_and_defaults_to_free_engine(self):
        tools = FakeTools()
        result = run_tool_chain(tools, steps(('browser', {'action': 'search', 'target': 'Godot official docs'}),
                                             ('browser', {'action': 'open', 'target': 'https://docs.godotengine.org'})))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(tools.calls[0][1]['value'], 'auto')
        for action in ('click', 'fill', 'press', 'screenshot'):
            with self.assertRaisesRegex(ValueError, 'browser action'):
                run_tool_chain(tools, steps(('browser', {'action': action, 'target': 'x'})))
        with self.assertRaisesRegex(ValueError, 'search engine'):
            run_tool_chain(tools, steps(('browser', {'action': 'search', 'target': 'x', 'value': 'serper'})))

    def test_existing_permission_failure_stops_remaining_calls(self):
        tools = FakeTools({'remote_status': PermissionError('Remote Pilot is off.')})
        result = run_tool_chain(tools, steps(('remote_status', {}), ('remote_project_info', {})))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual([step['status'] for step in result['steps']], ['failed', 'skipped'])
        self.assertEqual([call[0] for call in tools.calls], ['remote_status'])
        self.assertIn('Remote Pilot is off', result['steps'][0]['error'])

    def test_nonzero_remote_read_exit_is_failure(self):
        tools = FakeTools({'remote_project_info': 'Exit 255\nHost unavailable'})
        result = run_tool_chain(tools, steps(('remote_project_info', {'cwd': '~/project'}), ('project_info', {})))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['steps'][0]['result'], 'Exit 255\nHost unavailable')
        self.assertEqual(result['steps'][1]['status'], 'skipped')

    def test_batch_file_error_stops_chain(self):
        tools = FakeTools({'read_project_files': json.dumps({'files': [{'path': 'missing.py', 'error': 'Not found'}]})})
        result = run_tool_chain(tools, steps(('read_project_files', {'requests': '[{"path":"missing.py"}]'}),
                                             ('project_info', {})))
        self.assertEqual(result['status'], 'failed')
        self.assertIn('per-file errors', result['steps'][0]['error'])
        self.assertEqual(result['steps'][1]['status'], 'skipped')

    def test_source_scan_bounds_are_visible_separately_from_response_clipping(self):
        tools = FakeTools({'desktop_projects': json.dumps({'projects': [], 'bounded': True})})
        result = run_tool_chain(tools, steps(('desktop_projects', {})))
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['steps'][0]['source_bounded'])
        self.assertFalse(result['steps'][0]['truncated'])

    def test_missing_git_executable_stops_chain_but_nonrepository_is_valid_observation(self):
        tools = FakeTools({'git_changes': 'Git is not installed.'})
        result = run_tool_chain(tools, steps(('git_changes', {}), ('project_info', {})))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['steps'][1]['status'], 'skipped')
        tools = FakeTools({'git_changes': 'Selected project is not inside a Git repository.'})
        result = run_tool_chain(tools, steps(('git_changes', {}), ('project_info', {})))
        self.assertEqual(result['status'], 'completed')

    def test_results_are_truncated_with_original_digest_and_byte_count(self):
        raw = 'é' * 2500
        tools = FakeTools({'list_files': raw})
        result = run_tool_chain(tools, steps(('list_files', {})))
        record = result['steps'][0]
        self.assertTrue(record['truncated'])
        self.assertEqual(record['result_bytes'], len(raw.encode('utf-8')))
        self.assertLessEqual(record['returned_bytes'], 3000)
        self.assertEqual(record['result_sha256'], hashlib.sha256(raw.encode('utf-8')).hexdigest())

    def test_preexisting_cancel_skips_all_steps(self):
        tools = FakeTools()
        tools.cancel.set()
        result = run_tool_chain(tools, steps(('project_info', {}), ('list_files', {})))
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(result['skipped'], 2)
        self.assertEqual(tools.calls, [])

    def test_cooperative_deadline_stops_later_steps(self):
        def wait_for_deadline(tools, _args):
            while not tools.cancel.is_set():
                time.sleep(0.01)
            raise InterruptedError('stopped by deadline')
        tools = FakeTools({'project_info': wait_for_deadline})
        started = time.monotonic()
        result = run_tool_chain(tools, steps(('project_info', {}), ('list_files', {})), max_seconds=1)
        self.assertLess(time.monotonic() - started, 1.4)
        self.assertEqual(result['status'], 'timed_out')
        self.assertEqual(result['steps'][0]['status'], 'failed')
        self.assertEqual(result['steps'][1]['status'], 'skipped')

    def test_actual_project_tool_respects_read_only_path_checks(self):
        from agent_core import ProjectTools
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'project.godot').write_text('config_version=5\n', encoding='utf-8')
            Path(folder, '.env').write_text('PRIVATE', encoding='utf-8')
            tools = ProjectTools(folder, act=False)
            result = run_tool_chain(tools, steps(('read_file', {'path': 'project.godot'})))
            self.assertEqual(result['status'], 'completed')
            blocked = run_tool_chain(tools, steps(('read_file', {'path': '.env'})))
            self.assertEqual(blocked['status'], 'failed')
            self.assertIn('Credential/config-secret', blocked['steps'][0]['error'])

    def test_outer_agent_receives_chain_evidence_and_blocked_step(self):
        import agent_core
        payloads = []
        events = []
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'sample.py').write_text('value = 42\n', encoding='utf-8')
            Path(folder, '.env').write_text('PRIVATE', encoding='utf-8')
            chain = steps(('read_file', {'path': '.env'}), ('read_file', {'path': 'sample.py'}))

            def stream(_url, payload, _cancel):
                payloads.append(payload)
                if len(payloads) == 1:
                    message = {'tool_calls': [{'id': 'call_chain', 'function': {'name': 'invoke_chain', 'arguments': {'steps': chain}}}]}
                else:
                    message = {'content': 'The first read was blocked; the second was skipped.'}
                return iter([{'message': message, 'done': True}])

            with patch.object(agent_core, 'stream_chat', side_effect=stream), patch.object(agent_core, 'model_supports_vision', return_value=False):
                agent_core.run_agent('http://fixture', 'fixture', [{'role': 'user', 'content': 'Inspect sample.py'}],
                                     folder, False, threading.Event(), lambda *event: events.append(event), rounds=2)

        self.assertGreaterEqual(len(payloads), 2)
        tool_messages = [message for message in payloads[1]['messages'] if message['role'] == 'tool']
        self.assertEqual(len(tool_messages), 1)
        observed = json.loads(tool_messages[0]['content'])
        self.assertEqual(observed['status'], 'failed')
        self.assertEqual([step['status'] for step in observed['steps']], ['failed', 'skipped'])
        self.assertEqual(tool_messages[0]['tool_call_id'], 'call_chain')
        self.assertTrue(any(kind == 'tool' and data['name'] == 'invoke_chain' for kind, data in events))


if __name__ == '__main__':
    unittest.main()
