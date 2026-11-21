"""Checks that plain-language requests expose usable tools on the first model turn."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_core as core


def _done():
    return {'message': {'content': 'Observed.'}, 'done': True, 'done_reason': 'stop'}


class FirstTurnToolTests(unittest.TestCase):
    def first_turn(self, request, *, act=False, task_kind='code', desktop=False, remote=False, mail=False):
        payloads = []
        events = []

        def stream(_url, payload, _cancel):
            payloads.append(copy.deepcopy(payload))
            return iter([_done()])

        def browser_result(_self, name, _args):
            if name == 'browser':
                return json.dumps({'links': [], 'search_engine': 'fixture', 'url': 'https://godotengine.org/download/archive/', 'title': 'Godot', 'page': 'Release archive'})
            return original(_self, name, _args)

        original = core.ProjectTools.execute
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(core, 'stream_chat', side_effect=stream), \
             patch.object(core, 'model_supports_vision', return_value=False), \
             patch.object(core, 'desktop_projects', return_value={'projects': [], 'bounded': False}), \
             patch.object(core.ProjectTools, 'execute', browser_result), \
             patch.object(core, 'DESKTOP_ACCESS', desktop), \
             patch.object(core, 'ACTIVE_REMOTE_ALLOWED', remote), \
             patch.object(core, 'REMOTE_PILOT', remote):
            core.run_agent('fixture', 'fixture', [{'role': 'user', 'content': request}], folder, act,
                           threading.Event(), lambda kind, value: events.append((kind, value)),
                           performance={'num_ctx': 8192}, task_kind=task_kind)
        self.assertEqual(len(payloads), 1)
        return {tool['function']['name'] for tool in payloads[0]['tools']}, events

    def test_named_desktop_request_has_discovery_on_first_turn(self):
        names, _ = self.first_turn('Find my NIGHTFALL game on Desktop', desktop=True)
        self.assertIn('desktop_projects', names)
        self.assertIn('invoke_chain', names)

    def test_mail_request_has_connector_reads_on_first_turn(self):
        names, _ = self.first_turn('Read my Gmail inbox', mail=True)
        self.assertIn('gmail_status', names)
        self.assertIn('gmail_search', names)

    def test_explicit_remote_project_request_has_status_tool(self):
        names, _ = self.first_turn('Check my VPS project over SSH', act=True, remote=True)
        self.assertIn('remote_status', names)
        self.assertIn('remote_project_info', names)

    def test_public_chat_freshness_uses_fixed_query_and_browser(self):
        names, events = self.first_turn('What is the latest Godot release?', task_kind='chat')
        self.assertIn('browser', names)
        searches = [value for kind, value in events if kind == 'tool' and value.get('automatic') and value.get('args', {}).get('action') == 'search']
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0]['args']['target'], 'site:godotengine.org Godot Engine latest release')

    def test_private_chat_freshness_does_not_search(self):
        _, events = self.first_turn('Check my latest Gmail inbox message', task_kind='chat')
        self.assertFalse(any(kind == 'tool' and value.get('automatic') and value.get('name') == 'browser' for kind, value in events))

    def test_shopping_compares_in_a_headless_read_only_browser(self):
        payloads = []
        events = []
        query = '2 TB SSD retail price and delivery'

        def stream(_url, payload, _cancel):
            payloads.append(copy.deepcopy(payload))
            step = len(payloads)
            actions = [('search', query), ('click', 'Order now')]
            if step > len(actions):
                return iter([_done()])
            action, target = actions[step - 1]
            return iter([{'message': {'tool_calls': [{'function': {
                'name': 'browser', 'arguments': {'action': action, 'target': target},
            }}]}, 'done': True}])

        with tempfile.TemporaryDirectory() as folder, \
             patch.object(core, 'stream_chat', side_effect=stream), \
             patch.object(core, 'model_supports_vision', return_value=False), \
             patch.object(core, 'load_project_instructions', side_effect=AssertionError('Shopping opened a project note')), \
             patch.object(core, 'DESKTOP_ACCESS', True), \
             patch('browser_tools.BrowserTools') as browser:
            browser.return_value.execute.return_value = json.dumps({'page':'2 TB SSD, £89 from a fixture seller.'})
            core.run_agent('fixture','fixture',[{'role':'user','content':'Find a 2 TB SSD under £100.'}],
                           folder,True,threading.Event(),lambda kind,value:events.append((kind,value)),
                           task_kind='chat',focus='shopping')

        self.assertGreaterEqual(len(payloads), 3)
        self.assertEqual([tool['function']['name'] for tool in payloads[0]['tools']], ['browser'])
        self.assertIn('You are Agent Zero', payloads[0]['messages'][0]['content'])
        browser.assert_called_once()
        self.assertEqual(browser.call_args.kwargs['mode'],'headless')
        browser.return_value.execute.assert_called_once_with('search',query,'')
        self.assertTrue(any('Plan mode permits web search' in str(value) for kind,value in events if kind=='result'))

    def test_code_first_model_turn_receives_named_project_source(self):
        payloads = []
        events = []

        def stream(_url, payload, _cancel):
            payloads.append(copy.deepcopy(payload))
            return iter([_done()])

        with tempfile.TemporaryDirectory() as folder, \
             patch.object(core, 'stream_chat', side_effect=stream), \
             patch.object(core, 'model_supports_vision', return_value=False):
            (Path(folder) / 'README.txt').write_text('One-room puzzle instructions.\n', encoding='utf-8')
            (Path(folder) / 'main.py').write_text('print("one-room puzzle")\n', encoding='utf-8')
            core.run_agent('fixture', 'fixture',
                           [{'role': 'user', 'content': 'Inspect README.txt and main.py, then explain the project.'}],
                           folder, False, threading.Event(), lambda kind, value: events.append((kind, value)),
                           performance={'num_ctx': 8192})
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]['tools'], [])
        system = payloads[0]['messages'][0]['content']
        self.assertIn('Automatic first-turn project evidence', system)
        self.assertIn('One-room puzzle instructions.', system)
        self.assertIn('print(\\"one-room puzzle\\")', system)
        self.assertTrue(any(kind == 'project_context' for kind, _ in events))


if __name__ == '__main__':
    unittest.main()
