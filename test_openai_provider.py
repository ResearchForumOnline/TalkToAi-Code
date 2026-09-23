"""Own-key API regressions; dummy credentials and loopback fixtures only."""
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import http.client
import agent_core
import providers


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


if __name__=='__main__':unittest.main()
