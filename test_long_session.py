"""Observable model activity and bounded long-session behavior."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json

import agent_core as core


class LongSessionTests(unittest.TestCase):
    def test_agent_recovers_broken_ollama_step_without_running_partial_or_previous_calls(self):
        requests = []
        events = []
        executed = []
        def action(command):
            return {'function': {'name': 'run_command', 'arguments': {'command': command}}}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(request)
                if len(requests) == 1:
                    body = json.dumps({'message': {'tool_calls': [action('first action')]}, 'done': True}).encode()
                elif len(requests) == 2:
                    frame = {'message': {'content': 'unfinished', 'tool_calls': [action('uncommitted')]}, 'done': False}
                    body = json.dumps(frame).encode() + b'\n{"message":'
                else:
                    body = b'{"message":{"content":"The requested check finished."},"done":true}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with tempfile.TemporaryDirectory() as folder, patch.object(core, 'ACTIVE_PROVIDER', None), \
                        patch.object(core, 'AUTO_CONTEXT', False), patch.object(core, 'model_supports_vision', return_value=False):
                    tools = core.ProjectTools(folder, True)
                    def execute(name, args):
                        executed.append((name, args))
                        return 'Exit 0\nChecked once.'
                    with patch.object(tools, 'execute', side_effect=execute):
                        core._run_agent(f'http://127.0.0.1:{server.server_port}', 'fixture',
                                        [{'role': 'user', 'content': 'Check the project.'}], folder, True,
                                        threading.Event(), lambda k, v: events.append((k, v)), 2, {}, tools)
            finally:
                server.shutdown()
                thread.join()
        self.assertEqual([r['stream'] for r in requests], [True, True, False])
        self.assertEqual(requests[1]['messages'], requests[2]['messages'])
        self.assertEqual(requests[2]['messages'][-1]['content'], 'Exit 0\nChecked once.')
        self.assertEqual(executed, [('run_command', {'command':'first action'})])
        self.assertEqual(sum(k == 'discard_partial' for k, _ in events), 1)
        saved = [v for k,v in events if k == 'message']
        self.assertNotIn('unfinished', str(saved))
        self.assertNotIn('uncommitted', str(saved))
        self.assertEqual(saved[-1]['content'], 'The requested check finished.')
        self.assertIn(('status', 'Ready'), events)

    def test_inference_retries_bound_empty_local_stream_and_do_not_replay_apis(self):
        for provider, expected_requests in ((None, 2), (object(), 1)):
            with self.subTest(local=provider is None), patch.object(core, 'ACTIVE_PROVIDER', provider), \
                    patch.object(core, 'stream_chat', side_effect=lambda *args, **kwargs: iter(())) as stream:
                with self.assertRaisesRegex(core._IncompleteAgentReply, 'before finishing this step'):
                    list(core._agent_reply_stream('fixture', {'stream':True}, threading.Event()))
                self.assertEqual(stream.call_count, expected_requests)

    def test_stop_between_attempts_suppresses_reconnect(self):
        cancel = threading.Event()
        with patch.object(core, 'ACTIVE_PROVIDER', None), patch.object(core, 'stream_chat', return_value=iter(())) as stream:
            frames = core._agent_reply_stream('fixture', {'stream':True}, cancel)
            self.assertTrue(next(frames)['_retry_local_inference'])
            cancel.set()
            with self.assertRaises(InterruptedError):
                next(frames)
            self.assertEqual(stream.call_count, 1)

    def test_slow_model_response_has_its_own_timeout_and_owned_socket(self):
        connection = Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.readline.side_effect = [b'{"done":true}\n', b'']
        with patch.object(core.http.client, 'HTTPConnection', return_value=connection) as transport:
            result = list(core._http_stream('http://127.0.0.1:11434', {}, threading.Event()))
        self.assertEqual(result, [{'done': True}])
        transport.assert_called_once_with('127.0.0.1', 11434, timeout=10)
        connection.sock.settimeout.assert_called_once_with(600)
        connection.sock.dup.return_value.close.assert_called_once()
        connection.getresponse.return_value.close.assert_called_once()

    def test_buffered_tool_calls_get_time_to_finish_on_local_cpu(self):
        # Production Ollama can produce no HTTP frame until all arguments are generated.
        for port,payload,expected in (
            (11434,{'tools':[{'type':'function'}],'options':{'num_predict':1536}},2904),
            (11434,{'format':'json','options':{'num_predict':3072}},5208),
            (11434,{'format':'json','options':{'num_predict':16384}},21600),
            (11435,{'tools':[{}]},600),
            (11434,{'options':{'num_predict':3072}},600)):
            with self.subTest(port=port,payload=payload):
                connection=Mock()
                response=connection.getresponse.return_value
                response.status=200
                response.readline.side_effect=[b'{"done":true}\n',b'']
                with patch.object(core.http.client,'HTTPConnection',return_value=connection):
                    self.assertEqual(list(core._http_stream(f'http://127.0.0.1:{port}',payload,threading.Event())),[{'done':True}])
                connection.sock.settimeout.assert_called_once_with(expected)

    def test_wait_for_a_buffered_reply_follows_the_measured_speed_of_that_model(self):
        url = 'http://127.0.0.1:11434'
        payload = {'model': 'slow-fixture', 'format': 'json', 'options': {'num_predict': 8192}}
        with patch.dict(core.MODEL_SPEED, {(url, 'slow-fixture'): 4.0}, clear=True):
            # Four tokens a second: 8,192 tokens take 34 minutes; allow twice that plus the cold start.
            self.assertEqual(core.buffered_wait_seconds(url, payload), 600 + 4096)
            self.assertEqual(core.buffered_wait_seconds(url, dict(payload, model='unmeasured')), 600 + int(1.5 * 8192))
        with patch.dict(core.MODEL_SPEED, {(url, 'slow-fixture'): 80.0}, clear=True):
            self.assertEqual(core.buffered_wait_seconds(url, payload), 600 + int(8192 * 0.025))

    def test_local_speed_is_recorded_from_the_final_frame(self):
        connection = Mock()
        connection.getresponse.return_value.status = 200
        frame = json.dumps({'done': True, 'eval_count': 100, 'eval_duration': 20_000_000_000}).encode() + b'\n'
        connection.getresponse.return_value.readline.side_effect = [frame, b'']
        with patch.object(core.http.client, 'HTTPConnection', return_value=connection), \
                patch.dict(core.MODEL_SPEED, {}, clear=True):
            list(core._http_stream('http://127.0.0.1:11434', {'model': 'm'}, threading.Event()))
            self.assertEqual(core.MODEL_SPEED[('http://127.0.0.1:11434', 'm')], 5.0)

    def test_unsupported_ollama_thinking_retries_without_it(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append(request)
                if request.get('think'):
                    body = b'{"error":"thinking is not supported by this model"}'
                    self.send_response(400)
                else:
                    body = b'{"message":{"content":"ready"},"done":true}\n'
                    self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            chunks = list(core._http_stream(f'http://127.0.0.1:{server.server_port}',
                                             {'think': True}, threading.Event()))
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual([request['think'] for request in received], [True, False])
        self.assertTrue(chunks[0]['_thinking_unavailable'])
        self.assertEqual(chunks[-1]['message']['content'], 'ready')

    def test_opt_in_reasoning_is_activity_only_and_not_chat_history(self):
        events = []
        payloads = []
        secret_reasoning = 'I am considering a file change.'

        def stream(_url, payload, _cancel):
            payloads.append(payload)
            yield {'message': {'thinking': secret_reasoning}, 'done': False}
            yield {'message': {'content': 'I can help with that.'}, 'done': False}
            yield {'message': {}, 'done': True, 'eval_count': 3, 'eval_duration': 1000000000}

        with tempfile.TemporaryDirectory() as folder:
            with patch.object(core, 'stream_chat', side_effect=stream), \
                 patch.object(core, 'AUTO_CONTEXT', False), \
                 patch.object(core, 'ACTIVE_PROVIDER', None), \
                 patch.object(core, 'model_supports_vision', return_value=False):
                core.run_agent('fixture', 'fixture', [{'role': 'user', 'content': 'Help.'}],
                               folder, False, threading.Event(),
                               lambda kind, value: events.append((kind, value)),
                               rounds=1, performance={'show_thinking': True})

        self.assertTrue(payloads[0]['think'])
        self.assertEqual([v['active'] for k, v in events if k == 'reasoning'], [True, False])
        self.assertTrue(any(k == 'model_wait' for k, _ in events))
        self.assertNotIn(secret_reasoning, str([v for k, v in events if k == 'message']))
        self.assertEqual([v['content'] for k, v in events if k == 'message' and v.get('role') == 'assistant'],
                         ['I can help with that.'])

    def test_extended_session_tracks_passes_until_its_deadline(self):
        events = []
        calls = 0

        def stream(_url, _payload, _cancel):
            nonlocal calls
            path = f'{calls}.txt'
            calls += 1
            yield {'message': {'content': '', 'tool_calls': [
                {'id': f'call-{calls}', 'function': {'name': 'read_file', 'arguments': {'path': path}}}]},
                   'done': True, 'eval_count': 3, 'eval_duration': 1000000000}

        with tempfile.TemporaryDirectory() as folder:
            for index in range(15):
                Path(folder, f'{index}.txt').write_text(f'Independent observation {index}', encoding='utf-8')
            with patch.object(core, 'stream_chat', side_effect=stream), \
                 patch.object(core, 'AUTO_CONTEXT', False), \
                 patch.object(core, 'model_supports_vision', return_value=False), \
                 patch.object(core.time, 'monotonic', side_effect=lambda: 100 + calls * 1000):
                core.run_agent('fixture', 'fixture', [{'role': 'user', 'content': 'Inspect these sources.'}],
                               folder, True, threading.Event(),
                               lambda kind, value: events.append((kind, value)),
                               rounds=1, keep_going=True,
                               performance={'work_session_minutes': 240})

        self.assertEqual(calls, 15)
        checkpoints = [value for kind, value in events if kind == 'goal_checkpoint']
        self.assertEqual(checkpoints[-1]['state'], 'paused')
        self.assertIsNone(checkpoints[-1]['total_passes'])
        self.assertIn('Work session time limit reached', checkpoints[-1]['blockers'][0])
        self.assertEqual(sum(kind == 'tool' for kind, _ in events), 14)
        self.assertEqual(checkpoints[-1]['session_minutes'], 240)
        self.assertTrue(any(value['state'] == 'continuing' for value in checkpoints))


if __name__ == '__main__':
    unittest.main()
