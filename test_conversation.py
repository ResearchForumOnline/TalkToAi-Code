"""Exercise conversational turns through the real HTTP clients, with local fixtures."""
import json
import threading
import unittest
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import agent_core as core
from api_router import ApiRouter, ProviderHTTPError, ProviderPartialResponseError
from conversation import _conversation_payload
from providers import ProviderProfile
from test_openai_provider import responses_fixture


@contextmanager
def fixture(replies):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            index = len(received)
            received.append((self.path, request))
            status, content_type, body = replies[index]
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .025}, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


CHAT_RESPONSE = (200, 'application/json', {'choices': [{
    'message': {'role': 'assistant', 'content': 'Hello!'}, 'finish_reason': 'stop'}]})
OLLAMA_RESPONSE = (200, 'application/x-ndjson', b'{"message":{"content":"Hi"}}\n'
                   b'{"message":{"content":"!"},"done":true,"eval_count":2,"eval_duration":1000000000}\n')


class ConversationTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(core, 'ACTIVE_PROVIDER', None))
        stack.enter_context(patch.object(core, 'ACTIVE_PROVIDER_POOL', ()))
        stack.enter_context(patch('providers.api_key', return_value=''))
        stack.enter_context(patch('api_router.DEFAULT_ROUTER', ApiRouter()))
        self.events = []
        self.cancel = threading.Event()

    def run_turn(self, url, history=None):
        core.run_conversation(url, 'qwen3.6:35b', history or [{'role': 'user', 'content': 'hello'}],
                              self.cancel, lambda kind, value: self.events.append((kind, value)))

    def test_greeting_and_followup_use_no_tools_or_project_context(self):
        history = [{'role': 'user', 'content': 'My dog is Ada.'}, {'role': 'assistant', 'content': 'Hello, Ada!'},
                   {'role': 'assistant', 'source': 'app', 'content': 'private app report'},
                   {'role': 'assistant', 'source': 'chat_error', 'content': 'private chat error'},
                   {'role': 'tool', 'content': 'private workspace output'},
                   {'role': 'assistant', 'content': 'Task error: private legacy diagnostic'},
                   {'role': 'user', '_automation_nudge': True, 'content': 'private code nudge'},
                   {'role': 'user', 'content': 'What was her name?', 'display_content': 'local filename'}]
        with fixture([OLLAMA_RESPONSE]) as (url, received), \
                patch.object(core, 'ProjectTools', side_effect=AssertionError('Chat opened a workspace')), \
                patch.object(core, 'ProcessJobs', side_effect=AssertionError('Chat started a task')):
            self.run_turn(url, history)
        path, request = received[0]
        self.assertEqual(path, '/api/chat')
        self.assertEqual(request['model'], 'qwen3.6:35b')
        self.assertNotIn('tools', request)
        self.assertIs(request['think'], False)
        self.assertEqual(request['options']['num_ctx'], 4096)
        self.assertNotIn('private', json.dumps(request))
        self.assertNotIn('local filename', json.dumps(request))
        self.assertEqual([item['content'] for item in request['messages'][1:]],
                         ['My dog is Ada.', 'Hello, Ada!', 'What was her name?'])
        self.assertLess(len(json.dumps(request)), 1200)
        self.assertEqual([v for k, v in self.events if k == 'delta'], ['Hi', '!'])
        self.assertEqual([v for k, v in self.events if k == 'message'], [{'role': 'assistant', 'content': 'Hi!'}])
        self.assertEqual(next(v for k, v in self.events if k == 'metrics')['tokens_per_second'], 2)

    def test_history_respects_budget_without_silently_truncating_current_message(self):
        older = [{'role': 'user', 'content': 'old' * 9000}, {'role': 'assistant', 'content': 'old result'}]
        latest = {'role': 'user', 'content': 'Read this: attached excerpt'}
        payload = _conversation_payload('model', older + [latest], {})
        self.assertEqual(payload['messages'][1:], [latest])
        with self.assertRaisesRegex(ValueError, 'split the attachment'):
            _conversation_payload('model', [older[0]], {})
        long = _conversation_payload('model', [{'role': 'user', 'content': 'a' * 9000}], {})
        self.assertEqual(long['options']['num_ctx'], 8192)

    def test_older_ollama_rejects_false_think_field_and_preserves_prompt(self):
        error = (400, 'application/json', {'error': 'unknown field "think"'})
        with fixture([error, OLLAMA_RESPONSE]) as (url, received):
            self.run_turn(url)
        first, second = [request for _, request in received]
        self.assertIs(first.pop('think'), False)
        self.assertEqual(first, second)

    def test_ollama_memory_and_runner_errors_do_not_expose_private_provider_text(self):
        cases = [('model requires more system memory (21.0 GiB) than is available (8.5 GiB)', '21.0 GiB', True),
                 ('llama runner process has terminated', 'runner stopped', True),
                 ('unspecified service problem', 'request failed', False)]
        for detail, expected, openzero in cases:
            with self.subTest(detail=detail):
                with fixture([(500, 'application/json', {'error': detail + ' PRIVATE_LOCAL_PATH'})]) as (url, _):
                    with self.assertRaises(RuntimeError) as error:
                        self.run_turn(url)
                message = str(error.exception)
                self.assertIn(expected, message)
                self.assertEqual('OpenZero' in message, openzero)
                self.assertNotIn('PRIVATE_LOCAL_PATH', message)
                self.assertNotIn('memory', message if not openzero else '')
                self.assertEqual(isinstance(error.exception, core.OllamaCapacityError), expected == '21.0 GiB')

    def test_only_explicit_memory_error_before_output_can_trigger_recovery(self):
        memory_error = b'{"error":"CUDA error: out of memory"}\n'
        for initial, retryable in [
            (b'{"message":{},"done":false}\n', True),
            (b'{"message":{"content":"Hello"},"done":false}\n', False),
            (b'{"message":{"thinking":"Working"},"done":false}\n', False),
            (b'{"message":{"tool_calls":[{"function":{"name":"fixture"}}]},"done":false}\n', False),
        ]:
            with self.subTest(first=initial):
                self.events.clear()
                with fixture([(200, 'application/x-ndjson', initial + memory_error)]) as (url, received):
                    with self.assertRaises(RuntimeError) as error:
                        self.run_turn(url)
                    self.assertEqual(len(received), 1)
                self.assertEqual(isinstance(error.exception, core.OllamaCapacityError), retryable)
                self.assertFalse(any(k == 'message' for k, _ in self.events))

    def test_ambiguous_buffer_error_and_echoed_input_do_not_enable_retry(self):
        for body in [{'error': 'failed to allocate compute buffer: invalid dimensions'},
                     {'error': 'invalid request', 'input': 'out of memory'}]:
            with self.subTest(body=body):
                with fixture([(500, 'application/json', body)]) as (url, received):
                    with self.assertRaises(RuntimeError) as error:
                        self.run_turn(url)
                self.assertNotIsInstance(error.exception, core.OllamaCapacityError)
                self.assertEqual(len(received), 1)

    def test_remote_chat_repairs_only_rejected_options_before_output(self):
        errors = [(422, 'application/json', {'detail': [{'loc': ['body', 'temperature'],
                  'type': 'extra_forbidden', 'msg': 'Extra inputs are not permitted'}]}),
                  (400, 'application/json', {'error': {'code': 'unsupported_parameter', 'param': 'max_tokens',
                   'message': "Use 'max_completion_tokens' instead."}})]
        with fixture([*errors, CHAT_RESPONSE]) as (url, received):
            core.set_active_provider(ProviderProfile('Own API', url + '/v1', 'text-model', max_output_tokens=1900))
            self.run_turn(url)
        first, second, third = [request for _, request in received]
        self.assertEqual([path for path, _ in received], ['/v1/chat/completions'] * 3)
        self.assertNotIn('tools', first)
        self.assertIn('temperature', first)
        self.assertNotIn('temperature', second)
        self.assertEqual(second['max_tokens'], third['max_completion_tokens'])
        self.assertEqual(third['max_completion_tokens'], 1900)
        self.assertEqual(first['messages'], third['messages'])
        self.assertEqual([v['content'] for k, v in self.events if k == 'message'], ['Hello!'])

    def test_responses_chat_sends_conversation_without_tool_options(self):
        response = {'type': 'response.completed', 'response': {'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': 'Hi from Responses.'}]}]}}
        selected = ProviderProfile('Own OpenAI', 'https://api.openai.com/v1', 'gpt-5', max_output_tokens=1536)
        core.set_active_provider(selected)
        delta = {'type': 'response.output_text.delta', 'delta': 'Hi '}
        with responses_fixture([(200, [delta, response])]) as received:
            self.run_turn('unused')
        path, request = received[0]
        self.assertEqual(path, '/v1/responses')
        self.assertNotIn('tools', request)
        self.assertNotIn('parallel_tool_calls', request)
        self.assertFalse(request['store'])
        self.assertEqual(request['input'][-1], {'role': 'user', 'content': 'hello'})
        self.assertEqual([v for k, v in self.events if k == 'delta'], ['Hi ', 'from Responses.'])
        self.assertEqual([v['content'] for k, v in self.events if k == 'message'], ['Hi from Responses.'])

    def test_opaque_rejection_and_partial_disconnect_never_replay(self):
        for response, exception in [
            ((400, 'application/json', {'error': {'message': 'private text and PRIVATE_KEY'}}), ProviderHTTPError),
            ((200, 'text/event-stream', b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'),
             ProviderPartialResponseError),
        ]:
            with self.subTest(response=response[0]):
                self.events.clear()
                with fixture([response]) as (url, received):
                    core.set_active_provider(ProviderProfile('Fixture', url, 'text-model'))
                    with self.assertRaises(exception) as error:
                        self.run_turn(url)
                self.assertEqual(len(received), 1)
                self.assertNotIn('PRIVATE_KEY', str(error.exception))
                self.assertFalse(any(k == 'message' for k, _ in self.events))

    def test_cutoff_answer_and_real_wait_activity(self):
        def events(*_args):
            yield {'_heartbeat': True, 'silent_seconds': 15}
            yield {'message': {'content': 'Part one.'}}
            yield {'_heartbeat': True, 'silent_seconds': 15}
            yield {'done': True, 'done_reason': 'length', 'eval_duration': None, 'eval_count': None}
        with patch.object(core, 'stream_chat', events):
            self.run_turn('fixture')
        waits = [v for k, v in self.events if k == 'model_wait']
        self.assertTrue(all(v['conversation'] for v in waits))
        self.assertEqual(waits[-1]['phase'], 'waiting_for_next_output')
        self.assertEqual([v for k, v in self.events if k == 'message'],
                         [{'role': 'assistant', 'content': 'Part one.', 'finish_reason': 'length'}])

    def test_cancellation_never_commits_a_partial_reply(self):
        def events(*_args):
            yield {'message': {'content': 'Beginning'}}
            self.cancel.set()
            yield {'done': True}
        with patch.object(core, 'stream_chat', events):
            self.run_turn('fixture')
        self.assertFalse(any(k == 'message' for k, _ in self.events))
        self.assertIn(('status', 'Stopped'), self.events)

    def test_tool_rejection_only_switches_to_enabled_model_and_retains_schema(self):
        denied = (400, 'application/json', {'error': {'message': 'This model does not support function calling'}})
        payload = {'stream': True, 'messages': [{'role': 'user', 'content': 'Inspect my project'}],
                   'tools': [core.schema('list_files', 'List files', {})]}
        with fixture([denied, denied]) as (url, primary_received), fixture([CHAT_RESPONSE]) as (fallback_url, fallback_received):
            primary = ProviderProfile('Primary', url, 'text-only')
            fallback = ProviderProfile('Local fallback', fallback_url, 'tool-model', fallback_enabled=True, cost_tier='self_hosted')
            router = ApiRouter()
            with patch('api_router.key_ready', return_value=True):
                with self.assertRaisesRegex(ProviderHTTPError, 'This model cannot use function tools'):
                    list(router.stream(primary, [fallback], payload, self.cancel, core._provider_stream))
                self.assertFalse(fallback_received)
                primary.fallback_enabled = True
                result = list(router.stream(primary, [fallback], payload, self.cancel, core._provider_stream))
            self.assertEqual(len(primary_received), 2)
            self.assertEqual(fallback_received[0][1]['tools'], payload['tools'])
            self.assertEqual(result[-1]['api_model'], 'tool-model')
            self.assertEqual(router.remaining(primary), 0)

    def test_repeated_tool_call_ids_never_reach_execution_or_another_model(self):
        function = {'id': 'same', 'type': 'function', 'function': {'name': 'list_files', 'arguments': '{}'}}
        response = (200, 'application/json', {'choices': [{'message': {'tool_calls':
            [dict(function, index=0), dict(function, index=1)]}, 'finish_reason': 'tool_calls'}]})
        with fixture([response]) as (url, received):
            profile = ProviderProfile('Primary', url, 'tool-model', fallback_enabled=True)
            events = []
            with self.assertRaises(ProviderPartialResponseError):
                for event in core._provider_stream(profile, {'messages': [], 'tools': [core.schema('list_files', 'List files', {})]}, self.cancel):
                    events.append(event)
        self.assertEqual(len(received), 1)
        self.assertFalse(any(event.get('message', {}).get('tool_calls') for event in events))


if __name__ == '__main__':
    unittest.main()
