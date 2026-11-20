"""Own-key API regressions; dummy credentials and loopback fixtures only."""
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
import providers
from api_router import ProviderHTTPError, ProviderPartialResponseError
from provider_stream import response_scope


@contextmanager
def responses_fixture(replies):
    """Exercise HTTP and SSE parsing without credentials or inference on the network."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            index = len(received)
            received.append((self.path, request))
            status, events = replies[index]
            self.send_response(status)
            self.send_header('X-Request-ID', 'req_fixture_123')
            self.send_header('Content-Type', 'text/event-stream' if status == 200 else 'application/json')
            self.end_headers()
            if status != 200:
                self.wfile.write(json.dumps(events).encode())
                return
            for event in events:
                if event is None:
                    self.wfile.write(b'data: [DONE]\n\n')
                    continue
                self.wfile.write(('event: ' + event['type'] + '\r\n').encode())
                for line in json.dumps(event, indent=1, ensure_ascii=False).splitlines():
                    self.wfile.write(('data: ' + line + '\r\n').encode())
                self.wfile.write(b'\r\n')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with patch('agent_core.http.client.HTTPSConnection', side_effect=lambda *args, **kwargs: http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)):
            yield received
    finally:
        server.shutdown()
        server.server_close()


class OwnKeyProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'LOCALAPPDATA':str(self.root)},clear=True);self.env.start()
        self.keys=patch.object(providers,'_SESSION_KEYS',{});self.keys.start()
        self.profile=providers.ProviderProfile('OpenAI','https://api.openai.com/v1','user-selected-model','OPENAI_API_KEY')
    def tearDown(self):self.keys.stop();self.env.stop();self.temp.cleanup()

    def test_openai_requires_users_key_without_network(self):
        with patch('agent_core.http.client.HTTPSConnection') as connection:
            with self.assertRaisesRegex(ValueError,'key is missing'):
                list(agent_core._provider_stream(self.profile,{'messages':[]},threading.Event()))
            connection.return_value.connect.assert_not_called()

    def test_session_key_never_enters_profile_or_environment(self):
        providers.store_api_key(self.profile,'dummy-key-for-tests')
        providers.save_profiles(self.root/'profiles.json',[self.profile])
        self.assertEqual(providers.api_key(self.profile),'dummy-key-for-tests')
        self.assertNotIn('dummy-key',(self.root/'profiles.json').read_text())
        self.assertNotIn('OPENAI_API_KEY',os.environ)
        self.assertFalse(providers.key_path(self.profile).exists())
        providers.forget_api_key(self.profile)
        with self.assertRaises(ValueError):providers.api_key(self.profile)

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_remembered_key_is_encrypted_and_scoped_to_endpoint(self):
        providers.store_api_key(self.profile,'dummy-key-for-dpapi-tests',remember=True)
        path=providers.key_path(self.profile)
        self.assertNotIn(b'dummy-key',path.read_bytes())
        providers._SESSION_KEYS.clear()
        self.assertEqual(providers.api_key(self.profile),'dummy-key-for-dpapi-tests')
        other=providers.ProviderProfile('Other','https://example.invalid/v1','model','OPENAI_API_KEY')
        self.assertEqual(providers.api_key(other),'')
        providers.forget_api_key(self.profile);self.assertFalse(path.exists())

    def test_first_party_parameters_and_compatible_backwards_support(self):
        request=providers.configure_request(self.profile,{}, {})
        self.assertEqual(request['max_completion_tokens'],2048)
        self.assertNotIn('max_tokens',request);self.assertNotIn('temperature',request)
        self.assertFalse(request['store']);self.assertTrue(request['stream_options']['include_usage'])
        other=providers.ProviderProfile('Local','http://127.0.0.1:1234/v1','local')
        self.assertIn('max_tokens',providers.configure_request(other,{},{}))
        for url in ('https://key@example.invalid/v1','https://example.invalid/v1?key=secret','http://api.openai.com/v1'):
            with self.assertRaises(ValueError):providers.ProviderProfile('bad',url,'m')

    def test_auto_never_selects_an_api_when_local_models_are_unavailable(self):
        import routing
        config={'local_model':'local-fixture','server_model':'server-fixture','active_provider':'OpenAI'}
        with patch.object(routing,'inventory',return_value=[]),patch('providers.api_key') as key:
            with self.assertRaises(ConnectionError):routing.choose_route(config,'auto')
            key.assert_not_called()

    def test_api_keys_are_not_sent_to_plain_http_remote_hosts(self):
        profile=providers.ProviderProfile('Remote','http://example.invalid/v1','model','FIXTURE_API_KEY')
        with self.assertRaises(ValueError):providers.store_api_key(profile,'dummy-key')
        with patch.dict(os.environ,{'FIXTURE_API_KEY':'dummy-key'}):
            with self.assertRaises(ValueError):providers.api_key(profile)

    def test_first_party_environment_key_rejected_for_custom_host(self):
        other = providers.ProviderProfile('Custom', 'https://example.invalid/v1', 'model', 'OPENAI_API_KEY')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'dummy-openai-environment-key'}):
            with self.assertRaisesRegex(ValueError, 'different API host'):
                providers.api_key(other)
            self.assertEqual(providers.api_key(self.profile), 'dummy-openai-environment-key')
            providers.store_api_key(other, 'dummy-custom-key')
            self.assertEqual(providers.api_key(other), 'dummy-custom-key')

    def test_model_listing_is_metadata_only(self):
        providers.store_api_key(self.profile,'dummy-key')
        response=io.BytesIO(b'{"data":[{"id":"model-b"},{"id":"model-a"}]}')
        with patch('providers.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=response
            self.assertEqual(providers.list_models(self.profile),['model-a','model-b'])
            request=opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url,'https://api.openai.com/v1/models')
            self.assertEqual(request.get_method(),'GET');self.assertIsNone(request.data)

    def test_openai_tool_stream_and_usage_with_no_external_request(self):
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                if len(received)==1:
                    delta={'tool_calls':[{'index':0,'id':'fixture-call','function':{'name':'list_files','arguments':'{}'}}]};reason='tool_calls'
                else:delta={'content':'Inspected fixture files.'};reason='stop'
                events=[{'choices':[{'delta':delta,'finish_reason':reason}]},{'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}]
                for event in events:self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode())
                self.wfile.write(b'data: [DONE]\n\n')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        events=[];providers.store_api_key(self.profile,'dummy-key')
        try:
            with patch.object(agent_core,'ACTIVE_PROVIDER',self.profile),patch.object(agent_core.http.client,'HTTPSConnection',side_effect=lambda *args,**kw:http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)):
                agent_core.run_agent('fixture','user-selected-model',[{'role':'user','content':'List project files'}],self.root,False,threading.Event(),lambda k,v:events.append((k,v)))
            self.assertEqual(len(received),2)
            self.assertEqual(received[0]['max_completion_tokens'],2048)
            self.assertNotIn('temperature',received[0]);self.assertNotIn('max_tokens',received[0])
            self.assertEqual(received[1]['messages'][-1]['tool_call_id'],'fixture-call')
            self.assertTrue(any(k=='metrics' and v.get('api_usage',{}).get('total_tokens')==15 for k,v in events))
            self.assertNotIn('dummy-key',json.dumps(received))
        finally:server.shutdown();server.server_close()

    def responses_profile(self, **options):
        profile = providers.ProviderProfile('OpenAI', self.profile.base_url, 'gpt-6.2-sol',
                                            'OPENAI_API_KEY', supports_vision=True, **options)
        providers.store_api_key(profile, 'dummy-key')
        return profile

    def test_responses_tool_loop_replays_phase_and_reasoning_privately(self):
        profile = self.responses_profile(reasoning_effort='medium', max_output_tokens=8192)
        reasoning = {'id': 'rs_fixture', 'type': 'reasoning', 'encrypted_content': 'fixture-encrypted-reasoning', 'summary': []}
        preamble = {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                    'phase': 'commentary', 'content': [{'type': 'output_text', 'text': 'Checking ✓. ', 'annotations': []}]}
        function = {'id': 'fc_fixture', 'type': 'function_call', 'status': 'completed',
                    'call_id': 'call_fixture', 'name': 'list_files', 'arguments': '{}'}
        output = {'id': 'msg_final', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                  'phase': 'final_answer', 'content': [{'type': 'output_text', 'text': 'Checked fixture files.', 'annotations': []}]}
        usage = {'input_tokens': 20, 'output_tokens': 5, 'total_tokens': 25}
        replies = [
            (200, [{'type': 'response.output_text.delta', 'delta': 'Checking ✓. '},
                   {'type': 'response.function_call_arguments.delta', 'delta': '{'},
                   {'type': 'response.completed', 'response': {'status': 'completed', 'output': [reasoning, preamble, function], 'usage': usage}}]),
            (200, [{'type': 'response.completed', 'response': {'status': 'completed', 'output': [output], 'usage': usage}}]),
        ]
        events = []
        with responses_fixture(replies) as received, patch.object(agent_core, 'ACTIVE_PROVIDER', profile):
            agent_core.run_agent('fixture', profile.model, [{'role': 'user', 'content': 'List project files'}],
                                 self.root, False, threading.Event(), lambda kind, value: events.append((kind, value)))
        self.assertEqual([path for path, _ in received], ['/v1/responses', '/v1/responses'])
        first, second = [request for _, request in received]
        self.assertEqual(first['input'][0]['role'], 'developer')
        self.assertEqual(first['reasoning'], {'effort': 'medium'})
        self.assertEqual(first['include'], ['reasoning.encrypted_content'])
        self.assertEqual(first['max_output_tokens'], 8192)
        self.assertFalse(first['store'])
        self.assertFalse(first['parallel_tool_calls'])
        self.assertIn('list_files', [tool['name'] for tool in first['tools']])
        self.assertEqual(second['input'][-4:-1], [reasoning, preamble, function])
        self.assertEqual(second['input'][-1]['type'], 'function_call_output')
        self.assertEqual(second['input'][-1]['call_id'], 'call_fixture')
        self.assertTrue(any(kind == 'metrics' and value['api_usage']['completion_tokens'] == 5 for kind, value in events))
        self.assertNotIn('fixture-encrypted-reasoning', json.dumps(events))

    def test_responses_screenshot_formats_and_stale_reasoning_recovery(self):
        profile = self.responses_profile()
        scope = response_scope(profile, 'dummy-key')
        reasoning = {'type': 'reasoning', 'id': 'rs_fixture', 'encrypted_content': 'dummy-stale-reasoning', 'summary': []}
        replies = [
            (400, {'error': {'code': 'invalid_encrypted_content', 'message': 'private fixture text'}}),
            (200, [{'type': 'response.completed', 'response': {'status': 'completed', 'output': []}}]),
        ]
        history = [
            {'role': 'user', 'content': 'Hello'},
            {'role': 'assistant', 'content': 'Earlier reply', '_openai_response': {
                'scope': scope, 'call_ids': [], 'content': 'Earlier reply',
                'output': [reasoning, {'role': 'assistant', 'content': 'Earlier reply'}],
            }},
            {'role': 'user', 'content': 'Inspect this', 'images': ['ZmFrZQ==']},
        ]
        with responses_fixture(replies) as received:
            events = list(agent_core._provider_stream(profile, {'messages': history}, threading.Event()))
        self.assertTrue(events[0]['_openai_replay_reset'])
        self.assertTrue(events[-1]['done'])
        self.assertIn(reasoning, received[0][1]['input'])
        self.assertNotIn(reasoning, received[1][1]['input'])
        self.assertEqual(received[1][1]['input'][-1]['content'][-1]['image_url'], 'data:image/jpeg;base64,ZmFrZQ==')
        self.assertNotIn('_openai_response', json.dumps(agent_core.provider_messages(history)))

    def test_responses_incomplete_tool_output_never_executes(self):
        profile = self.responses_profile()
        function = {'type': 'function_call', 'status': 'completed', 'call_id': 'call_fixture',
                    'name': 'write_file', 'arguments': '{"path":"unexpected.txt","content":"fixture"}'}
        with responses_fixture([(200, [{'type': 'response.output_item.done', 'item': function}, None])]):
            with self.assertRaises(ProviderPartialResponseError):
                list(agent_core._provider_stream(profile, {'messages': []}, threading.Event()))
        terminal = {'type': 'response.incomplete', 'response': {'status': 'incomplete', 'output': [function],
                    'incomplete_details': {'reason': 'max_output_tokens'}}}
        with responses_fixture([(200, [terminal])]):
            events = list(agent_core._provider_stream(profile, {'messages': []}, threading.Event()))
        self.assertEqual(events[-1]['done_reason'], 'length')
        self.assertNotIn('tool_calls', events[-1]['message'])
        self.assertFalse((self.root / 'unexpected.txt').exists())

    def test_responses_error_reports_safe_request_id_without_provider_body(self):
        profile = self.responses_profile()
        with responses_fixture([(429, {'error': {'code': 'insufficient_quota', 'message': 'private fixture project content'}})]):
            with self.assertRaises(ProviderHTTPError) as failure:
                list(agent_core._provider_stream(profile, {'messages': []}, threading.Event()))
        self.assertIn('billing limits', str(failure.exception))
        self.assertIn('req_fixture_123', str(failure.exception))
        self.assertNotIn('private fixture', str(failure.exception))

    def test_saved_openai_settings_override_automatic_protocol(self):
        profile = self.responses_profile(api_mode='chat_completions', reasoning_effort='low')
        path = self.root / 'profiles.json'
        providers.save_profiles(path, [profile])
        loaded, = providers.load_profiles(path)
        self.assertFalse(loaded.uses_responses)
        self.assertEqual(providers.configure_request(loaded, {}, {})['reasoning_effort'], 'low')

    def test_xai_uses_responses_with_provider_compatible_request(self):
        profile = providers.ProviderProfile('xAI', 'https://api.x.ai/v1', 'grok-4.7', 'XAI_API_KEY')
        providers.store_api_key(profile, 'dummy-xai-key')
        terminal = {'type': 'response.completed', 'response': {'status': 'completed', 'output': []}}
        with responses_fixture([(200, [terminal])]) as received:
            list(agent_core._provider_stream(profile, {'messages': [{'role': 'system', 'content': 'fixture'}]}, threading.Event()))
        path, body = received[0]
        self.assertEqual(path, '/v1/responses')
        self.assertEqual(body['input'][0]['role'], 'system')
        self.assertNotIn('include', body)
        self.assertFalse(body['store'])

    def test_mistral_catalog_uses_documented_metadata_endpoint(self):
        profile = providers.ProviderProfile('Mistral', 'https://api.mistral.ai/openai/v1', 'fixture-model', 'MISTRAL_API_KEY')
        providers.store_api_key(profile, 'dummy-mistral-key')
        response = io.BytesIO(b'{"data":[{"id":"fixture-model"}]}')
        with patch('providers.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(providers.list_models(profile), ['fixture-model'])
            request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.mistral.ai/v1/models')
        self.assertEqual(request.get_method(), 'GET')


if __name__=='__main__':unittest.main()
