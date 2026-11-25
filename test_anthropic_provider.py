"""Claude API regressions; dummy credentials and loopback fixtures only. No request leaves this computer."""
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import http.client

import agent_core
import anthropic_stream
import providers
from api_router import ProviderHTTPError, ProviderPartialResponseError
from provider_stream import response_scope, stream_provider


@contextmanager
def messages_fixture(replies):
    """Serve Messages API event streams from loopback and record what was sent."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            index = len(received)
            received.append((self.path, {name.lower(): value for name, value in self.headers.items()}, request))
            status, events = replies[index]
            self.send_response(status)
            self.send_header('request-id', 'req_fixture_456')
            self.send_header('Content-Type', 'text/event-stream' if status == 200 else 'application/json')
            self.end_headers()
            if status != 200:
                self.wfile.write(json.dumps(events).encode())
                return
            for event in events:
                self.wfile.write(('event: ' + event['type'] + '\n').encode())
                self.wfile.write(('data: ' + json.dumps(event, ensure_ascii=False) + '\n\n').encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with patch('agent_core.http.client.HTTPSConnection',
                   side_effect=lambda *args, **kwargs: http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)):
            yield received
    finally:
        server.shutdown()
        server.server_close()


def start(usage=None):
    return {'type': 'message_start', 'message': {'id': 'msg_fixture', 'role': 'assistant', 'content': [],
                                                 'usage': usage or {'input_tokens': 12, 'output_tokens': 1}}}


def text_block(index, *parts):
    yield {'type': 'content_block_start', 'index': index, 'content_block': {'type': 'text', 'text': ''}}
    for part in parts:
        yield {'type': 'content_block_delta', 'index': index, 'delta': {'type': 'text_delta', 'text': part}}
    yield {'type': 'content_block_stop', 'index': index}


def thinking_block(index, text, signature):
    yield {'type': 'content_block_start', 'index': index, 'content_block': {'type': 'thinking', 'thinking': '', 'signature': ''}}
    yield {'type': 'content_block_delta', 'index': index, 'delta': {'type': 'thinking_delta', 'thinking': text}}
    yield {'type': 'content_block_delta', 'index': index, 'delta': {'type': 'signature_delta', 'signature': signature}}
    yield {'type': 'content_block_stop', 'index': index}


def tool_block(index, identifier, name, *json_parts, close=True):
    yield {'type': 'content_block_start', 'index': index,
           'content_block': {'type': 'tool_use', 'id': identifier, 'name': name, 'input': {}}}
    for part in json_parts:
        yield {'type': 'content_block_delta', 'index': index, 'delta': {'type': 'input_json_delta', 'partial_json': part}}
    if close:
        yield {'type': 'content_block_stop', 'index': index}


def finish(reason, output_tokens=7):
    return [{'type': 'message_delta', 'delta': {'stop_reason': reason}, 'usage': {'output_tokens': output_tokens}},
            {'type': 'message_stop'}]


class ClaudeProviderTests(unittest.TestCase):
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
        self.profile = self.claude()
        providers.store_api_key(self.profile, 'dummy-claude-key')

    def tearDown(self):
        self.keys.stop()
        self.env.stop()
        self.temp.cleanup()

    @staticmethod
    def claude(model='claude-sonnet-5-5', **settings):
        settings.setdefault('max_output_tokens', 16384)
        return providers.ProviderProfile('Claude', 'https://api.anthropic.com/v1', model, 'ANTHROPIC_API_KEY',
                                         cost_tier='paid', supports_vision=True, **settings)

    def stream(self, payload, profile=None):
        return list(stream_provider(profile or self.profile, payload, threading.Event(), agent_core.provider_messages))

    def test_profile_rules(self):
        self.assertTrue(self.profile.is_anthropic)
        self.assertFalse(self.profile.uses_responses)
        self.assertEqual(self.claude(reasoning_effort='max').reasoning_effort, 'max')
        with self.assertRaisesRegex(ValueError, 'https://api.anthropic.com/v1'):
            providers.ProviderProfile('Claude', 'https://api.anthropic.com', 'claude-opus-5-5')
        with self.assertRaisesRegex(ValueError, 'Max effort is a Claude API setting'):
            providers.ProviderProfile('OpenAI', 'https://api.openai.com/v1', 'gpt-6.2-sol', reasoning_effort='max')
        with self.assertRaisesRegex(ValueError, 'Responses'):
            self.claude(api_mode='responses')
        with self.assertRaisesRegex(ValueError, 'reasoning effort'):
            providers.ProviderProfile('Other', 'https://example.com/v1', 'model', reasoning_effort='low')

    def test_key_is_required_and_the_environment_key_is_bound_to_the_claude_host(self):
        providers.forget_session_api_key(self.profile)
        with self.assertRaisesRegex(ValueError, 'Claude API key is missing'):
            providers.api_key(self.profile)
        other = providers.ProviderProfile('Elsewhere', 'https://example.com/v1', 'model', 'ANTHROPIC_API_KEY')
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'dummy-environment-key'}):
            self.assertEqual(providers.api_key(self.profile), 'dummy-environment-key')
            with self.assertRaisesRegex(ValueError, 'different API host'):
                providers.api_key(other)

    def test_model_listing_uses_the_claude_headers_and_is_metadata_only(self):
        response = io.BytesIO(b'{"data":[{"id":"claude-sonnet-5-5"},{"id":"claude-opus-5-5"}],"has_more":false}')
        with patch('providers.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(providers.list_models(self.profile), ['claude-opus-5-5', 'claude-sonnet-5-5'])
            request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.anthropic.com/v1/models?limit=1000')
        self.assertEqual(request.get_method(), 'GET')
        self.assertIsNone(request.data)
        self.assertEqual(request.get_header('X-api-key'), 'dummy-claude-key')
        self.assertEqual(request.get_header('Anthropic-version'), providers.ANTHROPIC_VERSION)
        self.assertIsNone(request.get_header('Authorization'))

    def test_text_stream_request_shape_and_usage(self):
        replies = [(200, [start({'input_tokens': 10, 'cache_read_input_tokens': 90, 'cache_creation_input_tokens': 20, 'output_tokens': 1}),
                          {'type': 'ping'}, *text_block(0, 'Hello', ' ✓ there'), *finish('end_turn', 9)])]
        payload = {'messages': [{'role': 'system', 'content': 'Be brief.'}, {'role': 'user', 'content': 'Hi'}],
                   'options': {'temperature': 0.2, 'num_predict': 2048, 'top_p': 0.9}, 'think': False, 'keep_alive': '1h'}
        with messages_fixture(replies) as received:
            events = self.stream(payload)
        path, headers, request = received[0]
        self.assertEqual(path, '/v1/messages')
        self.assertEqual(headers['x-api-key'], 'dummy-claude-key')
        self.assertEqual(headers['anthropic-version'], '2026-10-21')
        self.assertNotIn('authorization', headers)
        self.assertEqual(request['model'], 'claude-sonnet-5-5')
        self.assertEqual(request['max_tokens'], 16384)
        self.assertEqual(request['system'], 'Be brief.')
        self.assertEqual(request['messages'], [{'role': 'user', 'content': [{'type': 'text', 'text': 'Hi'}]}])
        self.assertEqual(request['thinking'], {'type': 'adaptive'})
        self.assertEqual(request['cache_control'], {'type': 'ephemeral'})
        self.assertTrue(request['stream'])
        # This API version rejects sampling parameters; none may be sent.
        for name in ('temperature', 'top_p', 'top_k', 'options', 'keep_alive', 'think', 'tool_choice', 'output_config', 'tools'):
            self.assertNotIn(name, request)
        self.assertEqual(''.join(event['message'].get('content', '') for event in events), 'Hello ✓ there')
        final = events[-1]
        self.assertEqual((final['done'], final['done_reason']), (True, 'stop'))
        self.assertEqual(final['api_usage'], {'prompt_tokens': 120, 'completion_tokens': 9, 'total_tokens': 129, 'cached_tokens': 90})

    def test_tool_loop_returns_the_models_own_blocks_unchanged(self):
        thinking = {'type': 'thinking', 'thinking': 'List the files first.', 'signature': 'fixture-signature=='}
        replies = [
            (200, [start(), *thinking_block(0, thinking['thinking'], thinking['signature']), *text_block(1, 'Checking. '),
                   *tool_block(2, 'toolu_fixture', 'list_files', '{', '}'), *finish('tool_use')]),
            (200, [start(), *text_block(0, 'Checked the fixture files.'), *finish('end_turn')]),
        ]
        events = []
        profile = self.claude(reasoning_effort='high')
        providers.store_api_key(profile, 'dummy-claude-key')
        with messages_fixture(replies) as received, patch.object(agent_core, 'ACTIVE_PROVIDER', profile):
            agent_core.run_agent('fixture', profile.model, [{'role': 'user', 'content': 'List project files'}],
                                 self.root, False, threading.Event(), lambda kind, value: events.append((kind, value)))
        first, second = [request for _, _, request in received]
        self.assertEqual(first['output_config'], {'effort': 'high'})
        tool = next(tool for tool in first['tools'] if tool['name'] == 'list_files')
        self.assertEqual(tool['input_schema']['type'], 'object')
        self.assertNotIn('function', tool)
        assistant = second['messages'][-2]
        self.assertEqual(assistant['role'], 'assistant')
        self.assertEqual(assistant['content'], [thinking, {'type': 'text', 'text': 'Checking. '},
                                                {'type': 'tool_use', 'id': 'toolu_fixture', 'name': 'list_files', 'input': {}}])
        result = second['messages'][-1]
        self.assertEqual(result['role'], 'user')
        self.assertEqual(result['content'][0]['type'], 'tool_result')
        self.assertEqual(result['content'][0]['tool_use_id'], 'toolu_fixture')
        self.assertTrue(all(block['type'] != 'tool_result' for block in result['content'][1:]))
        self.assertIn(('status', 'Ready'), events)
        # Reasoning is kept for the API only: never shown, never in the transcript events.
        self.assertNotIn('fixture-signature', json.dumps([value for kind, value in events if kind != 'message']))
        self.assertNotIn('List the files first.', json.dumps(events))

    def test_history_from_another_model_is_sent_as_text_never_as_unsigned_tool_blocks(self):
        history = [
            {'role': 'user', 'content': 'List files'},
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'call_local', 'function': {'name': 'list_files', 'arguments': {'path': '.'}}}]},
            {'role': 'tool', 'tool_name': 'list_files', 'tool_call_id': 'call_local', 'content': 'a.txt'},
            {'role': 'assistant', 'content': 'There is one file.'},
            {'role': 'user', 'content': 'Thanks. And now?'},
        ]
        scope = response_scope(self.profile, 'dummy-claude-key')
        system, turns = anthropic_stream.messages_request(agent_core.provider_messages(history, openai_scope=scope), scope)
        self.assertEqual(system, '')
        self.assertEqual([turn['role'] for turn in turns], ['user', 'assistant', 'user', 'assistant', 'user'])
        kinds = {block['type'] for turn in turns for block in turn['content']}
        self.assertEqual(kinds, {'text'})
        self.assertIn('list_files', turns[1]['content'][0]['text'])
        self.assertIn('a.txt', turns[2]['content'][0]['text'])

    def test_a_saved_turn_from_another_key_or_model_is_not_replayed(self):
        scope = response_scope(self.profile, 'dummy-claude-key')
        blocks = [{'type': 'thinking', 'thinking': 't', 'signature': 's'},
                  {'type': 'tool_use', 'id': 'toolu_1', 'name': 'list_files', 'input': {}}]
        history = [{'role': 'user', 'content': 'List files'},
                   {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'toolu_1', 'function': {'name': 'list_files', 'arguments': '{}'}}],
                    '_openai_response': {'scope': scope, 'output': blocks, 'content': '', 'call_ids': ['toolu_1']}},
                   {'role': 'tool', 'tool_name': 'list_files', 'tool_call_id': 'toolu_1', 'content': 'a.txt'}]
        _, own = anthropic_stream.messages_request(agent_core.provider_messages(history, openai_scope=scope), scope)
        self.assertEqual(own[1]['content'], blocks)
        self.assertEqual(own[2]['content'][0], {'type': 'tool_result', 'tool_use_id': 'toolu_1', 'content': 'a.txt'})
        other = response_scope(self.claude('claude-opus-5-5'), 'dummy-claude-key')
        _, foreign = anthropic_stream.messages_request(agent_core.provider_messages(history, openai_scope=other), other)
        self.assertTrue(all(block['type'] == 'text' for turn in foreign for block in turn['content']))

    def test_a_write_cut_off_by_the_output_limit_is_reported_and_never_executed(self):
        replies = [(200, [start(), *tool_block(0, 'toolu_cut', 'write_file', '{"path": "site/index.html", "content": "<html><bo', close=False),
                          *finish('max_tokens')])]
        with messages_fixture(replies):
            events = self.stream({'messages': [{'role': 'user', 'content': 'Make a page'}], 'tools': agent_core.TOOLS[:1]})
        final = events[-1]
        self.assertEqual(final['done_reason'], 'length')
        self.assertNotIn('tool_calls', final['message'])
        self.assertEqual(final['_cut_off_tool'], {'name': 'write_file', 'path': 'site/index.html'})
        self.assertNotIn('_openai_response', final)
        self.assertNotIn('<html>', json.dumps(events))

    def test_unsupported_optional_fields_are_removed_one_at_a_time_and_nothing_else(self):
        def rejection(text):
            return 400, {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': text}}
        replies = [rejection('thinking.type: adaptive is not supported by this model'),
                   rejection('output_config.effort: Extra inputs are not permitted'),
                   (200, [start(), *text_block(0, 'Done.'), *finish('end_turn')])]
        profile = self.claude('claude-older-model', reasoning_effort='low')
        providers.store_api_key(profile, 'dummy-claude-key')
        with messages_fixture(replies) as received:
            events = self.stream({'messages': [{'role': 'user', 'content': 'Hi'}], 'tools': agent_core.TOOLS[:1]}, profile)
        first, second, third = [request for _, _, request in received]
        self.assertIn('thinking', first)
        self.assertNotIn('thinking', second)
        self.assertIn('output_config', second)
        self.assertNotIn('output_config', third)
        for request in (second, third):
            self.assertEqual((request['model'], request['max_tokens'], len(request['tools'])), ('claude-older-model', 16384, 1))
        self.assertEqual(events[-1]['done_reason'], 'stop')

    def test_other_rejections_stop_with_a_safe_message(self):
        secret = 'private prompt text quoted by the provider'
        replies = [(400, {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'messages.0: ' + secret}}),
                   (404, {'type': 'error', 'error': {'type': 'not_found_error', 'message': 'model: ' + secret}}),
                   (400, {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'prompt is too long: 1200000 tokens'}})]
        payload = {'messages': [{'role': 'user', 'content': 'Hi'}]}
        with messages_fixture(replies) as received:
            with self.assertRaises(ProviderHTTPError) as first:
                self.stream(payload)
            with self.assertRaises(ProviderHTTPError) as second:
                self.stream(payload)
            with self.assertRaises(ProviderHTTPError) as third:
                self.stream(payload)
        self.assertEqual(len(received), 3)
        self.assertNotIn(secret, str(first.exception) + str(second.exception))
        self.assertIn('Request ID req_fixture_456', str(first.exception))
        self.assertIn('Fetch models in the API vault', str(second.exception))
        self.assertIn('context window', str(third.exception))

    def test_overload_before_output_can_fail_over_but_a_partial_reply_is_never_replayed(self):
        overloaded = {'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'Overloaded'}}
        payload = {'messages': [{'role': 'user', 'content': 'Hi'}]}
        with messages_fixture([(200, [start(), overloaded])]):
            with self.assertRaises(ProviderHTTPError) as early:
                self.stream(payload)
        self.assertEqual(early.exception.status, 529)
        with messages_fixture([(200, [start(), *list(text_block(0, 'Partial'))[:2], overloaded])]):
            with self.assertRaises(ProviderPartialResponseError):
                self.stream(payload)
        with messages_fixture([(200, [start(), *text_block(0, 'No stop reason')])]):
            with self.assertRaises(ProviderPartialResponseError):
                self.stream(payload)

    def test_thinking_is_shown_only_when_asked_and_images_use_the_claude_format(self):
        replies = [(200, [start(), *thinking_block(0, 'Considering.', 'sig'), *text_block(1, 'Seen.'), *finish('end_turn')])] * 2
        picture = {'role': 'user', 'content': [{'type': 'text', 'text': 'What is this?'},
                                                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,QUJD'}}]}
        with messages_fixture(replies) as received:
            quiet = self.stream({'messages': [picture]})
            shown = self.stream({'messages': [picture], 'think': True})
        self.assertFalse(any(event['message'].get('thinking') for event in quiet))
        self.assertEqual(''.join(event['message'].get('thinking', '') for event in shown), 'Considering.')
        self.assertEqual(received[1][2]['thinking'], {'type': 'adaptive', 'display': 'summarized'})
        self.assertEqual(received[0][2]['messages'][0]['content'][1],
                         {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'QUJD'}})

    def test_effort_follows_the_profile_then_the_apps_level_and_a_ceiling_only_lowers_it(self):
        effort = providers.request_effort
        plain, high = self.claude(), self.claude(reasoning_effort='high')
        self.assertEqual(effort(plain, {}), '')
        self.assertEqual(effort(plain, {'reasoning_effort': 'low'}), 'low')
        self.assertEqual(effort(high, {'reasoning_effort': 'low'}), 'high')
        self.assertEqual(effort(high, {'reasoning_ceiling': 'low'}), 'low')
        self.assertEqual(effort(plain, {'reasoning_ceiling': 'low'}), 'low')
        self.assertEqual(effort(self.claude(reasoning_effort='low'), {'reasoning_ceiling': 'high'}), 'low')
        self.assertEqual(effort(plain, {'reasoning_effort': 'max'}), 'max')
        openai = providers.ProviderProfile('OpenAI', 'https://api.openai.com/v1', 'gpt-6.2-sol', 'OPENAI_API_KEY')
        self.assertEqual(effort(openai, {'reasoning_effort': 'max'}), 'xhigh')
        self.assertEqual(effort(openai, {'reasoning_effort': 'nonsense'}), '')
        other = providers.ProviderProfile('Other', 'https://example.com/v1', 'model')
        self.assertEqual(effort(other, {'reasoning_effort': 'high'}), '')

    def test_refusal_is_reported_as_text(self):
        with messages_fixture([(200, [start(), *finish('refusal')])]):
            events = self.stream({'messages': [{'role': 'user', 'content': 'Hi'}]})
        self.assertIn('declined', ''.join(event['message'].get('content', '') for event in events))
        self.assertEqual(events[-1]['done_reason'], 'stop')


if __name__ == '__main__':
    unittest.main()
