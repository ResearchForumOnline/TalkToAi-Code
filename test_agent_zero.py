import tempfile
import threading
import unittest
from unittest.mock import patch

import agent_core
from model_library import QWEN_LOCAL_MODEL
import routing


class AgentZeroTests(unittest.TestCase):
    def test_native_entry_point_supplies_agent_identity(self):
        requests = []

        def stream(_endpoint, payload, _cancel):
            requests.append(payload)
            yield {'message': {'content': 'Fixture response'}, 'done': True}

        with tempfile.TemporaryDirectory() as project, patch.object(agent_core, 'stream_chat', side_effect=stream), \
                patch.object(agent_core, 'model_supports_vision', return_value=False):
            agent_core.run_agent_zero('local-fixture', 'fixture', [{'role': 'user', 'content': 'Explain my fixture'}],
                                      project, False, threading.Event(), lambda *_: None)
        self.assertIs(agent_core.run_agent, agent_core.run_agent_zero)
        self.assertTrue(requests[0]['messages'][0]['content'].startswith('You are Agent Zero'))

    def test_only_the_compact_preset_bootstraps_automatically(self):
        cancelled = threading.Event()
        with patch.object(routing, 'require_local_runtime', return_value='fixture'), \
                patch.object(routing, 'list_models', side_effect=[[], [routing.DEFAULT_LOCAL_MODEL], [], [], []]), \
                patch.object(routing, 'pull_model') as pull:
            self.assertTrue(routing.ensure_local_model(routing.DEFAULT_LOCAL_MODEL, cancel=cancelled,
                                                       allow_default_download=True))
            self.assertEqual(pull.call_args.args[0], routing.DEFAULT_LOCAL_MODEL)
            self.assertIs(pull.call_args.args[1], cancelled)
            with self.assertRaisesRegex(ConnectionError, 'Model downloads'):
                routing.ensure_local_model(QWEN_LOCAL_MODEL, allow_default_download=True)
            with self.assertRaisesRegex(ConnectionError, 'Model downloads'):
                routing.ensure_local_model(routing.DEFAULT_LOCAL_MODEL)
            with self.assertRaisesRegex(ConnectionError, 'Model downloads'):
                routing.ensure_local_model('custom:latest', allow_default_download=True)
            pull.assert_called_once()
