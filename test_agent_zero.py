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

    def test_code_requests_use_local_retention_without_changing_the_server_tunnel(self):
        sent = []

        def stream(endpoint, payload, _cancel):
            sent.append((endpoint,payload['keep_alive']))
            yield {'message':{'content':'Fixture response'},'done':True}

        with tempfile.TemporaryDirectory() as project, patch.object(agent_core,'stream_chat',side_effect=stream), \
                patch.object(agent_core,'model_supports_vision',return_value=False):
            for port, settings in ((11434,{}),(11434,{'ollama_keep_alive':'until_released'}),
                                   (11435,{'ollama_keep_alive':'24h'})):
                agent_core.run_agent_zero(f'http://127.0.0.1:{port}','fixture',
                    [{'role':'user','content':'Explain my fixture'}],project,False,threading.Event(),lambda *_:None,
                    performance=settings)
        self.assertEqual([value for _,value in sent],['60m',-1,'15m'])
