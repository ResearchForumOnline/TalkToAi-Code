"""Model capability must govern coordinate control, even with Desktop access."""

import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import agent_core as core


class ComputerRouteTests(unittest.TestCase):
    def test_text_model_point_click_is_blocked_before_computer_session_starts(self):
        with tempfile.TemporaryDirectory() as project:
            tools = core.ProjectTools(project, True, threading.Event())
            tools.desktop = object()
            with self.assertRaisesRegex(PermissionError, 'vision-capable model'):
                tools.execute('computer', {'action': 'click_point', 'value': '20,20'})
            self.assertIsNone(tools.computer)

            tools.vision_enabled = True
            tools.computer = Mock()
            tools.computer.execute.return_value = 'Input delivered.'
            self.assertEqual(tools.execute('computer', {'action': 'click_point', 'value': '20,20'}),
                             'Input delivered.')
            tools.computer.execute.assert_called_once_with('click_point', '', '20,20')

    @unittest.skipUnless(os.name == 'nt', 'Windows PC Pilot schema')
    def test_tool_schema_offers_point_click_only_to_vision_models(self):
        for supports_vision in (False, True):
            payloads = []

            def stream(_url, payload, _cancel):
                payloads.append(payload)
                if len(payloads) == 1:
                    yield {'message': {'content': '', 'tool_calls': [
                        {'id': 'desktop-pack', 'function': {'name': 'enable_tools',
                                                            'arguments': {'group': 'desktop'}}}]},
                           'done': True, 'eval_count': 1, 'eval_duration': 1000000000}
                    return
                yield {'message': {'content': 'Finished.'}, 'done': True,
                       'eval_count': 1, 'eval_duration': 1000000000}

            with tempfile.TemporaryDirectory() as project, \
                 patch.object(core, 'stream_chat', side_effect=stream), \
                 patch.object(core, 'model_supports_vision', return_value=supports_vision), \
                 patch.object(core, 'DESKTOP_ACCESS', True), \
                 patch.object(core, 'PC_PILOT', True), \
                 patch.object(core, 'AUTO_CONTEXT', False), \
                 patch.object(core, 'ACTIVE_PROVIDER', None):
                core.run_agent('fixture', 'fixture', [{'role': 'user', 'content': 'Inspect a window.'}],
                               project, True, threading.Event(), lambda *_: None,
                               rounds=2, performance={'num_ctx': 32768})
            computer = [item for item in payloads[1]['tools'] if item['function']['name'] == 'computer']
            self.assertEqual(len(computer), 1)
            action_hint = computer[0]['function']['parameters']['properties']['action']['description']
            self.assertEqual('click_point' in action_hint, supports_vision)


if __name__ == '__main__':
    unittest.main()
