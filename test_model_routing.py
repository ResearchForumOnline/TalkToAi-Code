"""Checks for local-only bootstrap and explicit server installation."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import Mock, patch

import amd_runtime
from model_library import DownloadCancelled, QWEN_LOCAL_MODEL
import routing


class ModelRoutingTests(unittest.TestCase):
    def test_auto_chat_reuses_large_model_without_contacting_a_server(self):
        config = {'local_model': 'small', 'local_large_model': QWEN_LOCAL_MODEL, 'server_model': 'remote'}
        with patch('routing.inventory', return_value=[QWEN_LOCAL_MODEL]) as inventory:
            route = routing.choose_route(config, include_server=False)
        self.assertEqual(route['model'], QWEN_LOCAL_MODEL)
        self.assertEqual(route['route'], 'local_large')
        inventory.assert_called_once_with(routing.LOCAL_PORT)

    def test_automatic_default_bootstraps_only_a_missing_windows_runtime(self):
        cancel = threading.Event()
        for problem, expected_setup in ((None, False), (routing.OllamaMissingError(), True),
                                        (ConnectionError('occupied'), False)):
            with self.subTest(problem=problem), patch.object(routing.sys, 'platform', 'win32'), patch(
                'routing.install_model', side_effect=[problem, routing.DEFAULT_LOCAL_MODEL] if problem else [routing.DEFAULT_LOCAL_MODEL]
            ), patch('runtime_setup.require_model_runtime') as setup:
                if type(problem) is ConnectionError:
                    with self.assertRaisesRegex(ConnectionError, 'occupied'):
                        routing.ensure_local_model(routing.DEFAULT_LOCAL_MODEL, cancel=cancel, allow_default_download=True)
                else:
                    self.assertTrue(routing.ensure_local_model(routing.DEFAULT_LOCAL_MODEL, cancel=cancel, allow_default_download=True))
                self.assertEqual(setup.called, expected_setup)

    def test_disabled_first_run_download_leaves_setup_to_the_user(self):
        with patch('routing.require_local_runtime', side_effect=routing.OllamaMissingError), patch('runtime_setup.require_model_runtime') as setup:
            with self.assertRaises(routing.OllamaMissingError):
                routing.ensure_local_model(routing.DEFAULT_LOCAL_MODEL, allow_default_download=False)
        setup.assert_not_called()

    def test_first_local_start_forces_loopback_and_uses_the_configured_model_store(self):
        notify = Mock()
        with patch('routing._version', side_effect=[None, '0.40.11']), \
                patch('routing._local_port_in_use', return_value=False), \
                patch('routing.ollama_executable', return_value='fixture-ollama'), \
                patch('routing.ollama_store', return_value='fixture-models'), \
                patch('routing.subprocess.Popen') as launch, \
                patch.dict('os.environ', {'OLLAMA_HOST': 'http://192.0.2.10:11434'}):
            self.assertEqual(routing.require_local_runtime(notify=notify), '0.40.11')
        self.assertEqual(launch.call_args.args[0], ['fixture-ollama', 'serve'])
        self.assertEqual(launch.call_args.kwargs['env']['OLLAMA_HOST'], '127.0.0.1:11434')
        self.assertEqual(launch.call_args.kwargs['env']['OLLAMA_MODELS'], 'fixture-models')
        notify.assert_called_once_with('Starting Ollama on this computer', 0, 0)

    def test_no_runtime_and_occupied_port_are_distinct_failures(self):
        with patch('routing._version', return_value=None), \
                patch('routing._local_port_in_use', return_value=False), \
                patch('routing.ollama_executable', return_value=None):
            with self.assertRaises(routing.OllamaMissingError):
                routing.require_local_runtime()
        with patch('routing._version', return_value=None), \
                patch('routing._local_port_in_use', return_value=True), \
                patch('routing.ollama_executable') as executable:
            with self.assertRaisesRegex(ConnectionError, 'did not report a valid Ollama'):
                routing.require_local_runtime(timeout=0)
        executable.assert_not_called()

    def test_local_qwen_install_never_opens_ssh(self):
        cancel, progress = threading.Event(), Mock()
        with patch('routing.require_local_runtime', return_value='0.40.11') as start, \
                patch('routing.list_models', side_effect=[[], [QWEN_LOCAL_MODEL]]), \
                patch('routing.pull_model') as pull, \
                patch('amd_runtime.ensure_amd_tunnel') as ssh:
            self.assertEqual(routing.install_model(QWEN_LOCAL_MODEL, cancel, progress), QWEN_LOCAL_MODEL)
        pull.assert_called_once_with(QWEN_LOCAL_MODEL, cancel, progress, 7200)
        start.assert_called_once_with(cancel, progress)
        ssh.assert_not_called()

    def test_explicit_server_install_can_connect_before_model_exists(self):
        cancel, progress = threading.Event(), Mock()
        settings = {'active_ssh_alias': 'fixture', 'server_model': QWEN_LOCAL_MODEL}
        with patch('routing.require_local_runtime') as start, \
                patch('routing.list_models', side_effect=[[], [QWEN_LOCAL_MODEL]]), \
                patch('routing.pull_model') as pull, \
                patch('amd_runtime.ensure_amd_tunnel', return_value=(True, 'reachable')) as ssh:
            self.assertEqual(routing.install_model(QWEN_LOCAL_MODEL, cancel, progress, port=11435,
                                                   server_config=settings), QWEN_LOCAL_MODEL)
        start.assert_not_called()
        ssh.assert_called_once_with(settings, require_model=False, cancel=cancel)
        self.assertEqual(pull.call_args.kwargs['endpoint'], 'http://127.0.0.1:11435')
        with self.assertRaises(ValueError):
            routing.install_model(QWEN_LOCAL_MODEL, cancel, progress, port=11435)

    def test_server_probe_distinguishes_missing_model_from_missing_connection(self):
        with patch('amd_runtime.model_inventory', return_value=[]), \
                patch('amd_runtime.subprocess.Popen') as start:
            ok, detail = amd_runtime.ensure_amd_tunnel({'server_model': QWEN_LOCAL_MODEL})
            self.assertFalse(ok)
            self.assertIn('selected model is missing', detail)
            self.assertTrue(amd_runtime.ensure_amd_tunnel({}, require_model=False)[0])
        start.assert_not_called()

    def test_deletes_only_selected_local_model_through_api_and_checks_inventory(self):
        names = ['keep:latest', 'Remove:latest']
        deleted = []

        class ModelService(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                body = json.dumps({'version': '0.40.11'} if self.path == '/api/version' else
                                  {'models': [{'name': name} for name in names]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_DELETE(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                model = data['model']
                if self.path != '/api/delete' or model not in names:
                    self.send_error(404)
                    return
                names.remove(model)
                deleted.append(model)
                self.send_response(200)
                self.send_header('Content-Length', '0')
                self.end_headers()

        with ThreadingHTTPServer(('127.0.0.1', 0), ModelService) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with patch('routing.LOCAL_PORT', server.server_port):
                    cancel = threading.Event()
                    cancel.set()
                    with self.assertRaises(DownloadCancelled):
                        routing.delete_local_model('remove', cancel)
                    self.assertEqual(deleted, [])
                    self.assertEqual(routing.delete_local_model('remove'), 'Remove:latest')
                    self.assertEqual(deleted, ['Remove:latest'])
                    self.assertEqual(routing.list_models(server.server_port), ['keep:latest'])
            finally:
                server.shutdown()
                worker.join()


if __name__ == '__main__':
    unittest.main()
