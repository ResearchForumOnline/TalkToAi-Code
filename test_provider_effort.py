"""Effort, output limits and cut-off file writes on API models. Loopback fixtures only."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_core
import providers
from provider_stream import cut_off_tool
from test_openai_provider import responses_fixture


def completed(output, status='completed', reason=None):
    response = {'status': status, 'output': output, 'usage': {'input_tokens': 20, 'output_tokens': 5, 'total_tokens': 25}}
    if reason:
        response['incomplete_details'] = {'reason': reason}
    return {'type': 'response.completed' if status == 'completed' else 'response.incomplete', 'response': response}


def text(value, identifier='msg_fixture'):
    return {'id': identifier, 'type': 'message', 'role': 'assistant', 'status': 'completed',
            'content': [{'type': 'output_text', 'text': value, 'annotations': []}]}


class ApiEffortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'LOCALAPPDATA': str(self.root)}, clear=True)
        self.env.start()
        self.keys = patch.object(providers, '_SESSION_KEYS', {})
        self.keys.start()
        if os.name != 'nt':
            for fixture in (patch.object(providers, '_secure_keyring'), patch('keyring.get_password', return_value=None),
                            patch('keyring.delete_password')):
                fixture.start()
                self.addCleanup(fixture.stop)

    def tearDown(self):
        self.keys.stop()
        self.env.stop()
        self.temp.cleanup()

    def openai(self, **options):
        profile = providers.ProviderProfile('OpenAI', 'https://api.openai.com/v1', 'gpt-6.2-sol', 'OPENAI_API_KEY',
                                            supports_vision=True, max_output_tokens=8192, **options)
        providers.store_api_key(profile, 'dummy-key')
        return profile

    def run_agent(self, profile, replies, request, performance):
        events = []
        with responses_fixture(replies) as received, patch.object(agent_core, 'ACTIVE_PROVIDER', profile), \
                patch.object(agent_core, 'DESKTOP_ACCESS', False):
            agent_core.run_agent('fixture', profile.model, [{'role': 'user', 'content': request}], self.root, True,
                                 threading.Event(), lambda kind, value: events.append((kind, value)), performance=performance)
        return [body for _, body in received], events

    def test_cut_off_tool_keeps_only_the_name_and_path(self):
        self.assertEqual(cut_off_tool('write_file', '{"path": "a/b.py", "content": "import os\\nprint('),
                         {'name': 'write_file', 'path': 'a/b.py'})
        self.assertEqual(cut_off_tool('write_file', '{"content": "x'), {'name': 'write_file', 'path': ''})
        self.assertEqual(cut_off_tool('write_file', '{"path": "caf\\u00e9.txt", "content": "'), {'name': 'write_file', 'path': 'café.txt'})
        self.assertIsNone(cut_off_tool('', '{}'))
        self.assertIsNone(cut_off_tool(None, None))

    def test_the_apps_effort_level_reaches_a_model_with_no_saved_effort(self):
        replies = [(200, [completed([text('Done.')])])] * 3
        low, _ = self.run_agent(self.openai(), replies[:1], 'Say done', {'effort': 'low'})
        high, _ = self.run_agent(self.openai(), replies[:1], 'Say done', {'effort': 'high'})
        saved, _ = self.run_agent(self.openai(reasoning_effort='medium'), replies[:1], 'Say done', {'effort': 'max'})
        self.assertEqual(low[0]['reasoning'], {'effort': 'low'})
        self.assertNotIn('reasoning', high[0])
        self.assertEqual(saved[0]['reasoning'], {'effort': 'medium'})

    def test_a_file_write_cut_off_by_the_output_limit_is_finished_as_a_continuable_reply(self):
        page = '<!DOCTYPE html>\n<html>\n<body>\n' + ''.join(f'<p>Row {i}</p>\n' for i in range(30)) + '</body>\n</html>\n'
        whole = json.dumps({'path': 'index.html', 'content': page})
        cut = whole[:len(whole) * 2 // 3]
        saved = agent_core.whole_lines(agent_core.read_draft(cut, truncated=True).content)
        partial_call = {'type': 'function_call', 'status': 'incomplete', 'call_id': 'call_cut', 'name': 'write_file',
                        'arguments': '{"path": "index.html", "content": "<!DOCTYPE html>\\n<html>\\n<bo'}
        replies = [
            (200, [completed([partial_call], 'incomplete', 'max_output_tokens')]),
            (200, [completed([text(cut)], 'incomplete', 'max_output_tokens')]),
            (200, [completed([text(json.dumps({'path': 'index.html', 'content': page[len(saved):]}))])]),
            (200, [completed([{'type': 'function_call', 'status': 'completed', 'call_id': 'call_check', 'name': 'run_checks', 'arguments': '{}'}])]),
            (200, [completed([text('Saved index.html; the page structure check passed.')])]),
        ]
        requests, events = self.run_agent(self.openai(reasoning_effort='high'), replies, 'Create a web page for my shop',
                                          {'effort': 'high'})
        self.assertEqual((self.root / 'index.html').read_text(encoding='utf-8'), page)
        self.assertIn(('status', 'Ready'), events)
        self.assertNotIn('tools', requests[1])
        self.assertIn('"index.html"', requests[1]['input'][-1]['content'])
        # The saved High setting is honoured until a reply runs out of room; then reasoning is capped for the retry.
        self.assertEqual(requests[1]['reasoning'], {'effort': 'high'})
        self.assertEqual(requests[2]['reasoning'], {'effort': 'low'})
        self.assertIn('Continue from the line after this excerpt', requests[2]['input'][-1]['content'])
        self.assertTrue(all(request['max_output_tokens'] == 8192 for request in requests))
        self.assertFalse(any(kind == 'status' and 'Paused' in str(value) for kind, value in events))


if __name__ == '__main__':
    unittest.main()
