"""Real loopback provider transport and isolated vault startup integration checks."""
import json
import tempfile
import threading
import unittest
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock
from types import SimpleNamespace

import agent_core
from api_router import ApiRouter, ProviderPartialResponseError
from providers import ProviderProfile

@contextmanager
def provider_server(mode):
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            raw=self.rfile.read(int(self.headers.get('Content-Length',0)))
            requests.append({'path':self.path,'payload':json.loads(raw),'authorization':self.headers.get('Authorization','')})
            if mode=='limited':
                self.send_response(429);self.send_header('Retry-After','120');self.end_headers();self.wfile.write(b'{"error":"limited"}');return
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            if mode=='partial_tool':
                event={'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call_partial','function':{'name':'read_file','arguments':'{'}}]}}]}
                self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode());self.wfile.flush();self.close_connection=True;return
            for event in [
                {'choices':[{'delta':{'content':'Ready.'}}]},
                {'choices':[{'delta':{},'finish_reason':'stop'}],'x_groq':{'usage':{'prompt_tokens':12,'completion_tokens':3,'total_tokens':15}}},
            ]:
                self.wfile.write(('data: '+json.dumps(event)+'\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:yield 'http://127.0.0.1:'+str(server.server_port)+'/v1',requests
    finally:server.shutdown();server.server_close();worker.join(timeout=3)

class ProviderTransportIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.keys=patch('providers._SESSION_KEYS',{});self.keys.start();self.addCleanup(self.keys.stop)
    def profile(self,label,url):
        return ProviderProfile(label,url,'fixture-model',fallback_enabled=True,cost_tier='self_hosted',supports_vision=True)
    def test_keyless_local_transport_with_unavailable_system_keyring(self):
        backend=SimpleNamespace(get_keyring=lambda:type('PlaintextKeyring',(),{})(),get_password=Mock())
        fixture_path=Path('fixture-provider.dpapi')
        with provider_server('success') as (endpoint,requests):
            with patch('providers.os.name','posix'),patch('providers.key_path',return_value=fixture_path),patch.dict('sys.modules',{'keyring':backend}):
                events=list(agent_core._provider_stream(self.profile('fixture',endpoint),{'messages':[{'role':'user','content':'fixture'}],'stream':True},threading.Event()))
        backend.get_password.assert_not_called()
        self.assertEqual(len(requests),1);self.assertEqual(requests[0]['authorization'],'')
        self.assertTrue(events[-1]['done']);self.assertEqual(events[-1]['api_usage']['total_tokens'],15)
    def test_real_http_429_switches_to_other_endpoint_and_reports_actual_usage(self):
        with provider_server('limited') as (first,first_requests),provider_server('success') as (second,second_requests):
            a=self.profile('primary',first);b=self.profile('alternate',second);router=ApiRouter()
            with patch('api_router.DEFAULT_ROUTER',router),patch.object(agent_core,'ACTIVE_PROVIDER',a),patch.object(agent_core,'ACTIVE_PROVIDER_POOL',(b,)):
                events=list(agent_core.stream_chat(first,{'messages':[{'role':'user','content':'fixture request'}],'model':'wrong-original-model','stream':True},threading.Event()))
            self.assertEqual(len(first_requests),1);self.assertEqual(len(second_requests),1)
            self.assertEqual(second_requests[0]['path'],'/v1/chat/completions')
            self.assertEqual(second_requests[0]['payload']['model'],'fixture-model')
            self.assertEqual(events[-1]['api_usage']['total_tokens'],15)
            self.assertEqual(events[-1]['api_usage_source'],'provider_reported')
            self.assertEqual(events[-1]['api_provider'],'alternate')
            self.assertTrue(100<router.remaining(a)<=120)
            self.assertTrue(any(event.get('_provider_switch',{}).get('reason')=='rate_limit' for event in events))
    def test_partial_tool_fragment_disconnect_never_calls_fallback_or_exposes_action(self):
        with provider_server('partial_tool') as (first,first_requests),provider_server('success') as (second,second_requests):
            events=[]
            with self.assertRaises(ProviderPartialResponseError):
                for event in ApiRouter().stream(self.profile('primary',first),[self.profile('alternate',second)],{'messages':[{'role':'user','content':'fixture'}],'stream':True},threading.Event(),agent_core._provider_stream):events.append(event)
            self.assertEqual(len(first_requests),1);self.assertEqual(second_requests,[])
            self.assertFalse(any((event.get('message') or {}).get('tool_calls') for event in events))
    def test_tool_screenshots_follow_all_paired_results_and_key_is_not_history(self):
        history=[{'role':'assistant','content':'','tool_calls':[
            {'id':'call_1','function':{'name':'computer','arguments':{'action':'screenshot'}}},
            {'id':'call_2','function':{'name':'read_file','arguments':{'path':'fixture.txt'}}},
        ]},{'role':'tool','tool_name':'computer','tool_call_id':'call_1','content':'captured','images':['aW1hZ2U=']},
          {'role':'tool','tool_name':'read_file','tool_call_id':'call_2','content':'file result'}]
        converted=agent_core.provider_messages(history)
        self.assertEqual([m['role'] for m in converted],['assistant','tool','tool','user'])
        self.assertEqual(converted[1]['tool_call_id'],'call_1');self.assertEqual(converted[2]['tool_call_id'],'call_2')
        self.assertEqual(converted[3]['content'][1]['image_url']['url'],'data:image/jpeg;base64,aW1hZ2U=')
        with provider_server('success') as (endpoint,requests):
            profile=self.profile('fixture',endpoint)
            with patch('providers.api_key',return_value='dummy-private-fixture-key'):
                list(agent_core._provider_stream(profile,{'messages':history,'stream':True},threading.Event()))
            self.assertEqual(requests[0]['authorization'],'Bearer dummy-private-fixture-key')
            self.assertNotIn('dummy-private-fixture-key',json.dumps(requests[0]['payload']))
            self.assertNotIn('dummy-private-fixture-key',json.dumps(history))

class APIStartupIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])
    def test_fresh_install_api_default_vault_cancel_preserves_draft_and_history(self):
        import studio
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            for name,value in [('HOME',root),('STATE',root),('SESSION',root/'studio.json'),('CONNECTIONS',root/'connections.json'),('PROVIDERS',root/'providers.json')]:stack.enter_context(patch.object(studio,name,value))
            stack.enter_context(patch.object(studio.Studio,'health'));stack.enter_context(patch.object(studio.Studio,'install_tray'))
            stack.enter_context(patch('studio.EscapeCancel'))
            window=studio.Studio()
            try:
                self.assertEqual(window.route.currentIndex(),4)
                window.prompt.setPlainText('Build my fixture application')
                before=list(window.task['messages'])
                with patch.object(window,'providers_dialog') as vault,patch('studio.threading.Thread') as worker:
                    window.send()
                vault.assert_called_once();worker.assert_not_called()
                self.assertEqual(window.prompt.toPlainText(),'Build my fixture application')
                self.assertEqual(window.task['messages'],before)
                self.assertFalse(window.busy)
            finally:
                window.allow_quit=True;window.close();window.deleteLater();self.app.processEvents()

if __name__=='__main__':unittest.main()
