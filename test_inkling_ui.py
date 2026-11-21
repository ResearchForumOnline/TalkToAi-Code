"""Inkling selection, existing-file reuse, and Chat and Code dispatch."""

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox, QDialog

import agent_core
import model_library_dialog as model_ui
import studio
import test_chat_ui
from inkling_models import INKLING_PRESETS, INKLING_REPO, INKLING_REVISION
from model_library import DEFAULT_LOCAL_MODEL, QWEN_LOCAL_MODEL


class InklingUITests(unittest.TestCase):
    def test_existing_q8_uses_the_dedicated_runner_and_preserves_original(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            legacy = root / INKLING_PRESETS['q8'].legacy.name
            legacy.write_bytes(b'legacy fixture')
            compatible = root / 'Mainline_Support' / legacy.name
            runner, selected = Mock(), Mock()
            runner.status.return_value = None
            runner.diagnostic.return_value = None
            dialog = model_ui.ModelLibraryDialog(None, root, selected, Mock(), Mock(), Mock(), Mock(), inkling=runner)
            try:
                with patch.object(model_ui, 'prepare_inkling', return_value=compatible) as prepare, \
                        patch.object(model_ui, 'require_model_runtime') as ollama, \
                        patch('model_library_dialog.threading.Thread') as worker:
                    dialog._import_existing([str(legacy)])
                    runner.status.return_value = SimpleNamespace(label='CPU', reason='No compatible Vulkan device found.')
                    worker.call_args.kwargs['target']()
                self.assertEqual(prepare.call_args.args[1], 'q8')
                self.assertEqual(prepare.call_args.kwargs['source'], legacy)
                runner.start.assert_called_once()
                self.assertEqual(runner.start.call_args.kwargs, {'acceleration': 'auto'})
                selected.assert_called_once_with('q8', 'inkling')
                ollama.assert_not_called()
                self.assertIn('CPU. No compatible Vulkan device found.', dialog.inkling_status.text())
                self.assertEqual(legacy.read_bytes(), b'legacy fixture')
            finally:
                dialog.close()
                dialog.deleteLater()
                app.processEvents()

    def test_hugging_face_inkling_import_uses_compatible_preset(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            runner = Mock()
            runner.status.return_value = None
            runner.diagnostic.return_value = None
            dialog = model_ui.ModelLibraryDialog(None, Path(folder), Mock(), Mock(), Mock(), Mock(), Mock(), inkling=runner)
            try:
                file = INKLING_PRESETS['q8'].legacy
                dialog._finished(('files', INKLING_REPO, INKLING_REVISION, [file], file.name))
                self.assertTrue(dialog.install.isEnabled())
                self.assertIn('llama.cpp', dialog.install.text())
                dialog.install.setChecked(True)
                with patch.object(dialog, '_inkling_setup') as setup:
                    dialog.download.click()
                setup.assert_called_once_with('q8')
            finally:
                dialog.close()
                dialog.deleteLater()
                app.processEvents()

    def test_chat_and_code_use_the_local_endpoint_with_real_tool_dispatch(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, self.headers.get('Authorization'), request))
                tool_messages = [item for item in request['messages'] if item['role'] == 'tool']
                if tool_messages:
                    text = 'Read the verified project file.' if 'Inkling fixture evidence' in str(tool_messages) else 'Wrong file.'
                    message, reason = {'content': text}, 'stop'
                elif request.get('tools'):
                    message, reason = {'tool_calls': [{
                        'id': 'inkling-read-1', 'type': 'function',
                        'function': {'name': 'read_file', 'arguments': '{"path":"evidence.txt"}'},
                    }]}, 'tool_calls'
                else:
                    message, reason = {'content': '5'}, 'stop'
                data = json.dumps({'choices':[{'message':message, 'finish_reason':reason}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        provider_url = f'http://127.0.0.1:{server.server_port}/v1'
        runner = Mock()
        runner.start.return_value = (provider_url, 'inkling-fixture-key')
        runner.status.return_value = SimpleNamespace(label='GPU · 24/42 layers', reason='Vulkan model check passed.')
        case = test_chat_ui.ChatUITests('test_native_enter_click_stream_and_follow_up')
        case.setUpClass()
        self.addCleanup(case.doCleanups)
        case.enterContext(patch.object(studio, 'inkling_supported', return_value=True))
        case.enterContext(patch.object(studio, 'InklingRuntime', return_value=runner))
        case.setUp()
        case.enterContext(patch.object(studio, 'run_agent', agent_core.run_agent_zero))
        weights = case.root / 'Inkling-2-Mini-UD-Q8_K_XL.gguf'
        (case.root / 'evidence.txt').write_text('Inkling fixture evidence', encoding='utf-8')
        case.enterContext(patch.object(studio, 'prepared_path', return_value=weights))
        window = case.w
        window.config['inkling_acceleration'] = 'gpu'
        window._select_downloaded_model('q8', 'inkling')
        case.submit('What is 2 + 3?')
        case.wait_for(lambda: not window.busy, timeout=12)
        self.assertEqual(window.task['messages'][-1]['content'], '5')
        index = studio.MODEL_ROUTES.index('inkling')
        self.assertIn('GPU · 24/42 layers', window.route.currentText())
        self.assertEqual(window.route.itemData(index, Qt.ToolTipRole), 'Vulkan model check passed.')
        window.switch_workspace('code')
        window.task['project'] = str(case.root)
        case.enterContext(patch.object(window, 'prepare_task_target', return_value=True))
        case.enterContext(patch.object(agent_core, 'load_project_instructions', return_value=''))
        case.submit('Inspect the project and report what the evidence file contains.')
        case.wait_for(lambda: not window.busy, timeout=12)
        self.assertIn('Read the verified project file.', window.transcript.toPlainText())
        self.assertTrue(all(path == '/v1/chat/completions' and auth == 'Bearer inkling-fixture-key'
                            and body['model'] == 'inkling-2-mini' for path, auth, body in requests))
        self.assertTrue(any(any(message['role'] == 'tool' for message in body['messages']) for _, _, body in requests))
        self.assertEqual([call.kwargs for call in runner.start.call_args_list],
                         [{'acceleration': 'gpu'}, {'acceleration': 'gpu'}])
        self.assertEqual(window.config['local_model'], DEFAULT_LOCAL_MODEL)
        self.assertEqual(window.config['local_large_model'], QWEN_LOCAL_MODEL)
        self.assertIsNone(agent_core.ACTIVE_PROVIDER)
        case.local.assert_not_called()
        window.route.setCurrentIndex(studio.MODEL_ROUTES.index('local_large'))
        runner.stop.assert_called()

    def test_acceleration_setting_saves_and_restarts_managed_runner(self):
        runner = Mock()
        runner.status.return_value = None
        runner.diagnostic.return_value = None
        case = test_chat_ui.ChatUITests('test_native_enter_click_stream_and_follow_up')
        case.setUpClass()
        self.addCleanup(case.doCleanups)
        case.enterContext(patch.object(studio, 'inkling_supported', return_value=True))
        case.enterContext(patch.object(studio, 'InklingRuntime', return_value=runner))
        case.setUp()
        window = case.w
        runner.stop.reset_mock()

        def choose_gpu(dialog):
            choice = dialog.findChild(QComboBox, 'inkling_acceleration')
            self.assertEqual(choice.currentData(), 'auto')
            choice.setCurrentIndex(choice.findData('gpu'))
            return QDialog.Accepted

        with patch.object(studio.QDialog, 'exec', choose_gpu), \
                patch('search_provider.configured', return_value=False), \
                patch.object(window, 'set_start_with_windows'):
            window.settings()
        self.assertEqual(window.inkling_acceleration(), 'gpu')
        stored = json.loads((case.root / 'config.json').read_text(encoding='utf-8'))
        self.assertEqual(stored['inkling_acceleration'], 'gpu')
        runner.stop.assert_called_once()
        runner.start.assert_not_called()

    def test_unknown_architecture_error_identifies_inkling_and_hides_ordinary_runner_detail(self):
        err = agent_core._ollama_error(500, "llama-server process has terminated: unknown model architecture: 'inkling2' /private/key")
        self.assertIn('Inkling 2 Mini', str(err))
        self.assertIn('Saved downloads', str(err))
        self.assertNotIn('/private/key', str(err))
