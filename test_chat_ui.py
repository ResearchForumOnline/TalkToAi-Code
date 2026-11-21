"""Exercise the native composer and real Ollama HTTP client with an isolated loopback server."""
import json
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import studio


class ChatUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for key, path in [('HOME', self.root), ('STATE', self.root), ('SESSION', self.root / 'studio.json'),
                          ('CONNECTIONS', self.root / 'connections.json'), ('PROVIDERS', self.root / 'providers.json')]:
            self.stack.enter_context(patch.object(studio, key, path))
        for method in ('health', 'refresh_mail_status', 'install_tray'):
            self.stack.enter_context(patch.object(studio.Studio, method))
        self.hook = self.stack.enter_context(patch('studio.EscapeCancel')).return_value
        self.hook.arm.return_value = False
        self.w = self.make_window()
        self.local = self.stack.enter_context(patch('studio.ensure_local_model'))
        self.runtime = self.stack.enter_context(patch('studio.ensure_local_runtime'))
        self.tunnel = self.stack.enter_context(patch('studio.ensure_amd_tunnel'))
        self.agent = self.stack.enter_context(patch('studio.run_agent'))

    def make_window(self):
        window = studio.Studio()
        window.show()
        self.app.processEvents()
        def close():
            window.allow_quit = True
            window.close()
            window.deleteLater()
            self.app.processEvents()
        self.stack.callback(close)
        return window

    def server(self):
        requests = []
        received = threading.Event()
        release = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers['Content-Length'])
                payload = json.loads(self.rfile.read(length))
                requests.append((self.path, payload))
                received.set()
                release.wait(4)
                try:
                    if self.server.memory_failure=='before' and payload['model']==studio.QWEN_LOCAL_MODEL:
                        self.send_response(500)
                        self.end_headers()
                        self.wfile.write(b'{"error":"model requires more system memory (24 GiB) than is available (17 GiB)"}')
                        return
                    if self.server.failure:
                        self.send_error(503, 'Fixture offline')
                        return
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/x-ndjson')
                    self.end_headers()
                    for chunk in ('Let’s start ', 'with a short list.'):
                        line = {'message': {'role': 'assistant', 'content': chunk}, 'done': False}
                        self.wfile.write((json.dumps(line) + '\n').encode())
                        if self.server.memory_failure=='after' and payload['model']==studio.QWEN_LOCAL_MODEL:
                            self.wfile.write(b'{"error":"out of memory"}\n')
                            self.wfile.flush()
                            return
                    self.wfile.write(b'{"done":true,"eval_count":7,"eval_duration":1000000000}\n')
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # The Stop button intentionally closes the live HTTP socket.

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.failure = False
        server.memory_failure = None
        server.auto_route = 'local'
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.stack.callback(thread.join, 2)
        self.stack.callback(server.server_close)
        self.stack.callback(server.shutdown)
        self.stack.callback(release.set)
        def route(config, preference, benchmarks, *, include_server=True):
            if self.w.execution_kind=='conversation' and preference=='auto' and not self.w.config.get('active_ssh_alias'):
                self.assertFalse(include_server)
            key = preference if preference in ('local', 'local_large') else server.auto_route
            return {'route': key, 'url': f'http://127.0.0.1:{server.server_port}',
                    'model': config[key + '_model'], 'reason': 'isolated fixture'}
        self.stack.enter_context(patch('studio.choose_route', side_effect=route))
        return server, requests, received, release

    def wait_for(self, predicate, timeout=4):
        end = time.monotonic() + timeout
        while not predicate() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(.01)
        self.assertTrue(predicate(), self.w.status.text())

    def submit(self, text):
        self.w.prompt.setPlainText(text)
        QTest.keyClick(self.w.prompt, Qt.Key_Return)

    def test_fresh_chat_and_saved_route_survive_restart(self):
        w = self.w
        self.assertEqual(w.workspace, 'chat')
        self.assertTrue(w.chat_space.isChecked())
        self.assertTrue(w.chat_welcome.isVisible())
        self.assertTrue(w.chat_models.isVisible())
        self.assertTrue(w.chat_tools.isVisible())
        for widget in (w.right, w.reconnect_button, w.mode, w.step_budget, w.performance_label, w.stop):
            self.assertFalse(widget.isVisible())
        self.assertEqual(w.route.currentIndex(), 0)
        self.assertEqual(w.config['local_model'],studio.DEFAULT_LOCAL_MODEL)
        w.handle_event('local_model_state',{'state':'reachable','models':[]})
        self.assertTrue(w.model_setup.isVisible())
        self.assertIn('OpenZero',w.model_setup_label.text())
        w.handle_event('local_model_state',{'state':'reachable','models':[w.config['local_large_model']]})
        self.assertFalse(w.model_setup.isVisible())
        w.route.setCurrentIndex(3)
        w.route.activated.emit(3)
        restored = self.make_window()
        self.assertEqual(restored.workspace, 'chat')
        self.assertEqual(restored.route.currentIndex(), 3)
        self.assertIn('Qwen', restored.route.currentText())
        restored.switch_workspace('code')
        self.assertTrue(restored.right.isVisible())
        self.assertTrue(restored.step_budget.isVisible())
        self.assertEqual(self.make_window().workspace, 'code')

    def test_native_enter_click_stream_and_follow_up(self):
        w = self.w
        _, requests, received, release = self.server()
        w.task['project'] = str(self.root / 'removed-project')
        with patch.object(w, 'prepare_task_target') as target, patch('studio.ProjectTools') as tools, patch('studio.ProcessJobs') as jobs:
            self.submit('Help me plan tomorrow.')
            self.wait_for(received.is_set)
            self.assertTrue(w.stop.isEnabled())
            w.prompt.setPlainText('Where should I begin?')
            QTest.keyClick(w.prompt, Qt.Key_Return)
            QTest.mouseClick(w.send_button, Qt.LeftButton)
            self.assertEqual(w.prompt.toPlainText(), 'Where should I begin?')
            self.assertFalse(w.cancel.is_set())
            self.assertEqual(w.pending_prompt, '')
            release.set()
            self.wait_for(lambda: not w.busy)
            self.assertEqual([m['role'] for m in w.task['messages']], ['user','assistant'])
            self.assertIn('Let’s start with a short list.', w.transcript.toPlainText())
            self.assertEqual(w.status.text(), '')
            QTest.mouseClick(w.send_button, Qt.LeftButton)
            self.wait_for(lambda: len(requests) == 2 and not w.busy)
            path, payload = requests[1]
            self.assertEqual(path, '/api/chat')
            self.assertNotIn('tools', payload)
            self.assertEqual([m['role'] for m in payload['messages']], ['system','user','assistant','user'])
            self.assertEqual(payload['messages'][-1]['content'], 'Where should I begin?')
            self.assertNotIn('removed-project', json.dumps(payload))
            target.assert_not_called()
            tools.assert_not_called()
            jobs.assert_not_called()
        self.agent.assert_not_called()
        self.tunnel.assert_not_called()
        self.hook.arm.assert_not_called()

    def test_stop_during_cold_load_and_retry_smaller_model(self):
        w = self.w
        _, requests, received, release = self.server()
        w.route.setCurrentIndex(3)
        self.submit('Hello')
        self.wait_for(lambda: received.is_set() and w.run_phase=='Waiting for reply')
        w.started_at -= 20
        w.tick()
        self.assertIn('Stop, then Models', w.status.text())
        self.assertNotIn('shell', w.status.text().lower())
        QTest.mouseClick(w.stop, Qt.LeftButton)
        self.wait_for(lambda: not w.busy, 2)
        self.assertTrue(w.retry_button.isVisible())
        self.assertTrue(w.chat_models.isEnabled())
        self.assertIn('stopped', w.status.text())
        release.set()
        w.route.setCurrentIndex(1)
        QTest.mouseClick(w.retry_button, Qt.LeftButton)
        self.wait_for(lambda: len(requests) == 2 and not w.busy)
        self.assertEqual([r[1]['model'] for r in requests],
                         [w.config['local_large_model'], w.config['local_model']])
        self.assertEqual([m['role'] for m in w.task['messages']], ['user','assistant'])

    def test_retry_failure_preserves_attachment_and_next_draft(self):
        w = self.w
        http, requests, _, release = self.server()
        release.set()
        http.failure = True
        note = self.root / 'notes.txt'
        note.write_text('Remember blue marbles.', encoding='utf-8')
        w.attach_files([str(note)])
        self.submit('Summarize the attached note')
        self.wait_for(lambda: not w.busy)
        self.assertIn('Couldn’t finish', w.transcript.toPlainText())
        self.assertNotIn('App pause report', w.transcript.toPlainText())
        self.assertTrue(w.retry_button.isVisible())
        self.assertEqual(w.prompt.toPlainText(), '')
        http.failure = False
        w.prompt.setPlainText('Another question for later')
        QTest.mouseClick(w.retry_button, Qt.LeftButton)
        self.wait_for(lambda: len(requests) == 2 and not w.busy)
        self.assertEqual(requests[0][1]['messages'], requests[1][1]['messages'])
        self.assertIn('Remember blue marbles.', requests[1][1]['messages'][-1]['content'])
        self.assertEqual(w.prompt.toPlainText(), 'Another question for later')
        self.assertEqual([m['role'] for m in w.task['messages']], ['user','assistant'])
        self.assertFalse(w.retry_button.isVisible())

    def test_auto_recovers_from_pre_output_memory_rejection_once_on_this_pc(self):
        w=self.w
        http,requests,_,release=self.server()
        http.auto_route='local_large'
        http.memory_failure='before'
        release.set()
        w.config['auto_install_openzero']=False
        self.submit('Give me a short list')
        self.wait_for(lambda:not w.busy)
        self.assertEqual([r[1]['model'] for r in requests],[studio.QWEN_LOCAL_MODEL,studio.DEFAULT_LOCAL_MODEL])
        self.assertFalse(self.local.call_args.kwargs['allow_default_download'])
        self.assertEqual([m['role'] for m in w.task['messages']],['user','assistant'])
        self.assertEqual(w.task['messages'][1]['auto_model'],'OpenZero 1.7B')
        self.assertIn('OpenZero 1.7B',w.status.text())
        self.assertIn('OpenZero 1.7B',w.transcript.toPlainText())
        self.tunnel.assert_not_called()
        self.assertEqual(w.route.currentIndex(),0)

        w.route.setCurrentIndex(3)
        self.submit('Try with Qwen explicitly')
        self.wait_for(lambda:not w.busy)
        self.assertEqual(len(requests),3)
        self.assertIn('24 GiB',w.transcript.toPlainText())
        self.assertEqual(w.task['messages'][-1]['source'],'chat_error')

        http.memory_failure='after'
        w.route.setCurrentIndex(0)
        self.submit('Start another reply')
        self.wait_for(lambda:not w.busy)
        self.assertEqual(len(requests),4)
        self.assertEqual(w.task['messages'][-2]['source'],'chat_partial')
        self.assertEqual(w.task['messages'][-1]['source'],'chat_error')

    def test_legacy_failed_greeting_retry_and_auto_without_ssh(self):
        w = self.w
        _, requests, _, release = self.server()
        release.set()
        w.task['messages'] = [{'role':'user','content':'hello'},
            {'role':'assistant','source':'app','content':'App pause report\nTask paused after 0 model steps.'},
            {'role':'assistant','content':'Task error: Provider HTTP 400.'}]
        w.route.setCurrentIndex(0)
        with patch('studio.ensure_local_runtime'):
            self.submit('hello')
            self.wait_for(lambda: not w.busy)
        self.assertEqual([m['role'] for m in w.task['messages']], ['user','assistant'])
        self.assertEqual(len(requests), 1)
        self.assertEqual(w.prompt.toPlainText(), '')
        self.tunnel.assert_not_called()

    def test_operational_requests_and_manual_tools_keep_agent_zero(self):
        w = self.w
        self.server()
        w.task['project'] = str(self.root)
        for text in ('How do I open Blender?', 'Show me how to use a browser', 'Find ideas for my next project'):
            self.assertFalse(studio.chat_requests_tools(text))
        self.submit('Please open Brave on my computer')
        self.wait_for(lambda: not w.busy)
        self.assertEqual(self.agent.call_count, 1)
        self.assertEqual(self.agent.call_args.kwargs['task_kind'], 'chat')
        self.assertTrue(self.agent.call_args.args[4])
        QTest.mouseClick(w.chat_tools, Qt.LeftButton)
        self.assertTrue(w.mode.isVisible())
        w.mode.setCurrentText('Plan')
        self.submit('Take a closer look.')
        self.wait_for(lambda: not w.busy)
        self.assertEqual(self.agent.call_count, 2)
        self.assertFalse(self.agent.call_args.args[4])
        self.assertFalse(w.chat_tools.isChecked())

    def test_tune_runs_from_chat_and_undo_restores_retention(self):
        from local_tuning import Phase, RetentionTrial

        model = studio.DEFAULT_LOCAL_MODEL
        trial = RetentionTrial(model,'60m',Phase.BOUNDARY,
                              f'{model} loaded in 2.74s and 0.03s.','4h','a'*64)
        with patch('local_tuning.measure_retention',return_value=trial) as probe:
            self.submit('/tune')
            self.wait_for(lambda: not self.w.busy)
        probe.assert_called_once()
        self.agent.assert_not_called()
        self.assertIn('OpenZero 1.7B', self.w.transcript.toPlainText())
        self.assertIn('four hours', self.w.transcript.toPlainText())
        self.assertEqual('4h', self.w.model_performance()['ollama_keep_alive'])
        self.submit('/tune undo')
        self.assertEqual('60m', self.w.model_performance()['ollama_keep_alive'])
        self.assertIn('Restored the local model retention', self.w.transcript.toPlainText())

    def test_model_poll_can_finish_after_its_qt_signal_is_destroyed(self):
        from shiboken6 import delete

        bus = studio.Bus()
        received = []
        bus.event.connect(lambda kind, data: received.append((kind, data)))
        self.assertTrue(bus.publish('local_model_state', {'models': []}))
        delete(bus)
        self.assertFalse(bus.publish('health', 'Local online'))
        self.assertEqual([('local_model_state', {'models': []})], received)


if __name__ == '__main__':
    unittest.main()
