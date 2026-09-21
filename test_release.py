import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
import agent_core
from browser_tools import BrowserTools
from providers import ProviderProfile


class ReleaseTests(unittest.TestCase):
    def test_malformed_tool_text_is_retried_not_executed(self):
        streams=[
            [{'message':{'content':'<function=write_file>not executed</function></tool_call>'},'done':True}],
            [{'message':{'tool_calls':[{'function':{'name':'write_file','arguments':{'path':'fixed.txt','content':'verified'}}}]},'done':True}],
            [{'message':{'content':'Done'},'done':True}],
            [{'message':{'content':'This text-only fixture has no test suite; verification was not run.'},'done':True}]]
        events=[]
        with tempfile.TemporaryDirectory() as folder,patch.object(agent_core,'stream_chat',side_effect=lambda *args:iter(streams.pop(0))):
            agent_core.run_agent('fixture','fixture',[{'role':'user','content':'Create fixed.txt'}],folder,True,threading.Event(),lambda k,v:events.append((k,v)))
            self.assertEqual((Path(folder)/'fixed.txt').read_text(),'verified')
            self.assertEqual(len([e for e in events if e[0]=='tool']),1)
            self.assertTrue(any('Retrying malformed' in str(e) for e in events))
            self.assertTrue(any('Verifying changes before finishing' in str(e) for e in events))
            self.assertEqual(streams,[])

    def test_api_executes_tool_then_sends_matching_reply(self):
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                if len(received)==1:
                    delta={'tool_calls':[{'index':0,'id':'call_fixture','function':{'name':'write_file','arguments':json.dumps({'path':'result.txt','content':'verified'})}}]}
                    reason='tool_calls'
                else:delta={'content':'File verified.'};reason='stop'
                self.wfile.write(('data: '+json.dumps({'choices':[{'delta':delta,'finish_reason':reason}]})+'\n\ndata: [DONE]\n\n').encode())
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        profile=ProviderProfile('fixture',f'http://127.0.0.1:{server.server_port}/v1','fixture')
        try:
            with tempfile.TemporaryDirectory() as folder,patch.object(agent_core,'ACTIVE_PROVIDER',profile):
                agent_core.run_agent(profile.base_url,'fixture',[{'role':'user','content':'Create result.txt'}],folder,True,threading.Event(),lambda *_:None)
                self.assertEqual((Path(folder)/'result.txt').read_text(),'verified')
            reply=received[1]['messages'][-1]
            self.assertEqual(reply['tool_call_id'],'call_fixture')
            self.assertNotIn('tool_name',reply)
            self.assertIsInstance(received[1]['messages'][-2]['tool_calls'][0]['function']['arguments'],str)
        finally:server.shutdown();server.server_close()

    def test_interrupted_batch_is_repaired_before_steering(self):
        history=[{'role':'user','content':'start'},{'role':'assistant','content':'','tool_calls':[{'id':'a','function':{'name':'list_files','arguments':{}}},{'id':'b','function':{'name':'read_file','arguments':{'path':'x'}}}]},{'role':'tool','tool_call_id':'a','tool_name':'list_files','content':'x'},{'role':'user','content':'Change direction'}]
        repaired=agent_core.repair_tool_history(history)
        self.assertEqual(repaired[-2]['tool_call_id'],'b')
        self.assertIn('Interrupted',repaired[-2]['content'])
        self.assertEqual(repaired[-1]['content'],'Change direction')

    def test_api_incomplete_stream_never_executes_tools(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']));self.send_response(200);self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            profile=ProviderProfile('fixture',f'http://127.0.0.1:{server.server_port}/v1','fixture')
            with self.assertRaisesRegex(RuntimeError,'disconnected'):
                list(agent_core._provider_stream(profile,{'messages':[]},threading.Event()))
        finally:server.shutdown();server.server_close()

    def test_edge_interaction_and_screenshot(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers()
                self.wfile.write(b'<title>TalkToAi Browser Test</title><label>Player name<input id="player"></label><button onclick="document.querySelector(\'h1\').textContent=\'Hello \'+document.querySelector(\'input\').value">Start</button><h1>Ready</h1>')
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                browser=BrowserTools(folder,threading.Event())
                try:
                    self.assertIn('Ready',browser.execute('open',f'http://127.0.0.1:{server.server_port}'))
                    browser.execute('fill','Player name','Builder')
                    self.assertIn('Hello Builder',browser.execute('click','Start'))
                    shot=json.loads(browser.execute('screenshot'))
                    self.assertGreater(Path(shot['artifact']).stat().st_size,1000)
                finally:browser.close()
        finally:server.shutdown();server.server_close()

if __name__=='__main__':unittest.main()
