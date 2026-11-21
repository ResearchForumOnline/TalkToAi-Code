import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from model_library import DownloadCancelled
from runtime_diagnostics import check_local_reply, measure_local_reply, last_reply_check, probe, diagnose, report, MAX_BYTES, running_probe, residency_summary

EMPTY_RUNNING = {'state':'reachable','models':[]}

class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_real_loopback_chat_probe_and_saved_result(self):
        digest='b'*64
        events=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass

            def do_GET(self):
                events.append(('GET',self.path,None))
                self.send_response(200)
                self.end_headers()
                models=[] if self.path=='/api/ps' else [{'name':'fixture:latest','digest':digest}]
                self.wfile.write(json.dumps({'models':models}).encode())

            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                events.append(('POST',self.path,payload))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"message":{"role":"assistant","content":"5"},"done":false}\n{"done":true,"done_reason":"stop","load_duration":120000000}\n')

        with ThreadingHTTPServer(('127.0.0.1',0),Handler) as server, tempfile.TemporaryDirectory() as directory:
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                result=measure_local_reply('fixture',threading.Event(),Mock(),port=server.server_port,history_dir=directory)
            finally:
                server.shutdown()
                thread.join(2)
            self.assertEqual([(method,path) for method,path,_ in events],[('GET','/api/tags'),('GET','/api/ps'),('POST','/api/chat'),('GET','/api/tags')])
            self.assertEqual(events[2][2]['messages'][-1]['content'],'What is 2 + 3? Reply with just the digit.')
            self.assertEqual(events[2][2]['keep_alive'],'60m')
            self.assertEqual(result['load_seconds'],.12)
            self.assertNotIn('load_seconds',last_reply_check(directory,digest))
            self.assertEqual(result['outcome'],'verified')
            self.assertTrue(result['saved'])
            self.assertEqual(last_reply_check(directory,digest)['checked_at'],result['checked_at'])

    def test_reply_check_runs_selected_local_model_and_waits_for_completion(self):
        cancel = threading.Event()
        frames = [{'message': {'content': 'Good '}}, {'message': {'content': 'morning!'}, 'done': True}]
        with patch('routing.require_local_runtime') as start, patch('runtime_diagnostics.running_probe',return_value=EMPTY_RUNNING), patch('routing.installed_model_records', return_value=[{'name':'First:latest'},{'name':'Chosen:latest'}]), patch('agent_core._http_stream', return_value=iter(frames)) as stream:
            result = check_local_reply('chosen', cancel, Mock())
        start.assert_called_once()
        url, payload, event = stream.call_args.args
        self.assertEqual(url, 'http://127.0.0.1:11434')
        self.assertEqual(payload['model'], 'Chosen:latest')
        self.assertNotIn('tools', payload)
        self.assertIs(event, cancel)
        self.assertIn('Good morning!', result)
        self.assertIn('answered differently',result)
        self.assertEqual(payload['messages'][-1]['content'],'What is 2 + 3? Reply with just the digit.')
        self.assertEqual(payload['messages'][0]['role'],'system')
        self.assertEqual(payload['options']['temperature'],0)
        self.assertEqual(payload['options']['num_predict'],64)

    def test_reply_check_rejects_truncated_empty_and_removed_models(self):
        cancel = threading.Event()
        with patch('routing.require_local_runtime'), patch('routing.installed_model_records', return_value=[]), patch('agent_core._http_stream') as stream:
            with self.assertRaisesRegex(ValueError, 'no longer installed'):
                check_local_reply('demo', cancel, Mock())
            stream.assert_not_called()
        for frames in ([{'message': {'content': 'half'}}], [{'done': True}]):
            with self.subTest(frames=frames), patch('routing.require_local_runtime'), patch('runtime_diagnostics.running_probe',return_value=EMPTY_RUNNING), patch('routing.installed_model_records', return_value=[{'name':'demo'}]), patch('agent_core._http_stream', return_value=iter(frames)):
                with self.assertRaisesRegex(ConnectionError, 'did not finish'):
                    check_local_reply('demo', cancel, Mock())

    def test_stopping_the_reply_check_preserves_the_install(self):
        with patch('routing.require_local_runtime'), patch('runtime_diagnostics.running_probe',return_value=EMPTY_RUNNING), patch('routing.installed_model_records', return_value=[{'name':'demo'}]), patch('agent_core._http_stream', side_effect=InterruptedError), patch('routing.delete_local_model') as delete:
            with self.assertRaises(DownloadCancelled):
                check_local_reply('demo', threading.Event(), Mock())
            delete.assert_not_called()

    def test_exact_and_different_replies_persist_only_measurements(self):
        digest='a'*64
        with tempfile.TemporaryDirectory() as directory, patch('runtime_diagnostics.running_probe',return_value=EMPTY_RUNNING):
            root=Path(directory)
            for reply,outcome in [(' \n5\t','verified'),('A private-looking output 5.','different')]:
                frames=[{'message':{'content':reply[:2]}},{'message':{'content':reply[2:]},'done':True}]
                with self.subTest(reply=reply), patch('routing.require_local_runtime') as default_runtime, patch('routing.installed_model_records',return_value=[{'name':'personal-name:latest','digest':'sha256:'+digest}]) as records, patch('agent_core._http_stream',return_value=iter(frames)) as stream:
                    result=measure_local_reply('personal-name',threading.Event(),Mock(),history_dir=root,port=32145)
                self.assertEqual(stream.call_args.args[0],'http://127.0.0.1:32145')
                self.assertEqual(records.call_count,2)
                default_runtime.assert_not_called()
                self.assertEqual(result['outcome'],outcome)
                self.assertTrue(result['saved'])
                saved=last_reply_check(root,'sha256:'+digest.upper())
                self.assertEqual(saved['outcome'],outcome)
                self.assertEqual(saved['digest'],digest)
                self.assertTrue(saved['checked_at'].endswith('+00:00'))
                self.assertGreaterEqual(saved['elapsed_seconds'],saved['first_token_seconds'])
                data=(root/(digest+'.json')).read_text()
                self.assertNotIn('private-looking',data)
                self.assertNotIn('personal-name',data)
                self.assertNotIn('What is 2 + 3',data)
                self.assertNotIn('preview',saved)
            self.assertIsNone(last_reply_check(root,'b'*64))
            self.assertIsNone(last_reply_check(root,'../private'))

    def test_incomplete_replies_do_not_replace_prior_completed_check(self):
        records=[{'name':'test:latest','digest':'a'*64}]
        with tempfile.TemporaryDirectory() as root, patch('runtime_diagnostics.running_probe',return_value=EMPTY_RUNNING), patch('routing.installed_model_records',return_value=records):
            with patch('agent_core._http_stream',return_value=iter([{'message':{'content':'5'},'done':True}])):
                completed=measure_local_reply('test',threading.Event(),Mock(),port=32145,history_dir=root)
            original=last_reply_check(root,completed['digest'])
            for frames in [[{'message':{'content':'5'}}],[{'done':True}],[{'message':{'content':'5'},'done':True,'done_reason':'length'}]]:
                with self.subTest(frames=frames), patch('agent_core._http_stream',return_value=iter(frames)):
                    result=measure_local_reply('test',threading.Event(),Mock(),port=32145,history_dir=root)
                self.assertEqual(result['outcome'],'incomplete')
                self.assertFalse(result['saved'])
                self.assertEqual(last_reply_check(root,completed['digest']),original)

    def test_retagged_unknown_and_unwritable_measurements_still_show_reply(self):
        a,b='a'*64,'b'*64
        with tempfile.TemporaryDirectory() as root, patch('runtime_diagnostics.running_probe',return_value=EMPTY_RUNNING):
            for before,after in [(a,b),(a,None),('abcd','abcd'),(None,None)]:
                inventories=[[{'name':'test','digest':before}],[{'name':'test','digest':after}]]
                with self.subTest(before=before,after=after), patch('routing.installed_model_records',side_effect=inventories), patch('agent_core._http_stream',return_value=iter([{'message':{'content':'5'},'done':True}])):
                    result=measure_local_reply('test',threading.Event(),Mock(),history_dir=root,port=32145)
                self.assertEqual(result['outcome'],'verified')
                self.assertEqual(result['preview'],'5')
                self.assertFalse(result['saved'])
                self.assertIsNone(result['digest'])
            self.assertEqual(list(Path(root).iterdir()),[])
            with patch('routing.installed_model_records',return_value=[{'name':'test','digest':a}]), patch('agent_core._http_stream',return_value=iter([{'message':{'content':'x'*900},'done':True}])), patch('session_store.atomic_write',side_effect=PermissionError):
                result=measure_local_reply('test',threading.Event(),Mock(),history_dir=root,port=32145)
            self.assertEqual(result['outcome'],'different')
            self.assertFalse(result['saved'])
            self.assertLessEqual(len(result['preview']),241)
            self.assertIn('could not be saved',result['note'])

    def test_valid_inventory(self):
        with patch('runtime_diagnostics._loopback.open', return_value=io.BytesIO(b'{"models":[{"name":"a:latest"}]}')):
            self.assertEqual(probe(11434)['models'], ['a:latest'])

    def test_reply_check_preserves_a_longer_lease_and_loaded_context(self):
        deadline=(datetime.now(timezone.utc)+timedelta(hours=3)).isoformat()
        running={'state':'reachable','models':[{'name':'another-alias:latest','digest':'sha256:'+'a'*64,
                                                'expires_at':deadline,'context_length':8192}]}
        with patch('runtime_diagnostics.running_probe',return_value=running), \
                patch('routing.installed_model_records', return_value=[{'name':'fixture:latest','digest':'a'*64}]), \
                patch('agent_core._http_stream',return_value=iter([{'message':{'content':'5'},'done':True}])) as stream:
            result=measure_local_reply('fixture',threading.Event(),Mock(),port=32145,keep_alive='5m')
        payload=stream.call_args.args[1]
        self.assertGreater(payload['keep_alive'],10790)
        self.assertEqual(payload['options']['num_ctx'],8192)
        self.assertTrue(result['was_loaded'])

    def test_invalid_inventory(self):
        for raw in [b'<html>no</html>', b'[]', b'{"models":[{}]}', b'{"models":null}', b'x'*(MAX_BYTES+1)]:
            with self.subTest(raw=raw[:30]), patch('runtime_diagnostics._loopback.open', return_value=io.BytesIO(raw)):
                self.assertEqual(probe(11435)['state'], 'invalid')

    def test_offline_has_no_raw_error(self):
        with patch('runtime_diagnostics._loopback.open', side_effect=OSError('private detail')):
            self.assertEqual(probe(11434), {'state':'unreachable','models':[]})

    def test_shared_local_probe_and_latest_alias(self):
        with patch('runtime_diagnostics.probe', return_value={'state':'reachable','models':['a:latest']}) as p:
            rows=diagnose({'local_model':'a','local_large_model':'missing','server_model':'a'})
        self.assertEqual(p.call_count,2)
        self.assertEqual([r['installed'] for r in rows],[True,False,True])
        self.assertIn('Settings', rows[1]['action'])

    def test_offline_actions_and_custom_label(self):
        with patch('runtime_diagnostics.probe', return_value={'state':'unreachable','models':[]}),patch('runtime_diagnostics.running_probe',return_value={'state':'unreachable','models':[]}):
            text=report({'server_label':'AMD','preferred_route':'server'})
        self.assertIn('AMD',text);self.assertIn('SSH host alias',text);self.assertIn('Check Ollama connection',text)
        self.assertIn('does not load or download',text)

    def test_empty_model_requires_setting(self):
        with patch('runtime_diagnostics.probe', return_value={'state':'reachable','models':[]}):
            self.assertIn('Set this model ID',diagnose({})[0]['action'])

    def test_truncated_and_deep_json_are_invalid(self):
        from http.client import IncompleteRead
        for error in [IncompleteRead(b'partial'),RecursionError()]:
            with patch('runtime_diagnostics._loopback.open',side_effect=error):
                self.assertEqual(probe(11434)['state'],'invalid')

    def test_deadline_stops_trickling_response(self):
        with patch('runtime_diagnostics._loopback.open',return_value=io.BytesIO(b' ' * 20000)), patch('runtime_diagnostics.time.monotonic',side_effect=[0,0,4]):
                self.assertEqual(probe(11434)['state'],'unreachable')

    def test_residency_cpu_gpu_missing_and_not_loaded_are_distinct(self):
        state={'state':'reachable','models':[{'name':'coder:latest','size':100,'size_vram':0,'context_length':8192}]}
        self.assertIn('CPU/system-memory',residency_summary(state,'coder'))
        state['models'][0]['size_vram']=100
        self.assertIn('GPU-resident',residency_summary(state,'coder'))
        state['models'][0]['size_vram']=50
        self.assertIn('GPU offload',residency_summary(state,'coder'))
        state['models'][0]['size_vram']=None
        self.assertIn('unknown',residency_summary(state,'coder'))
        self.assertIn('not loaded',residency_summary(state,'other'))

    def test_running_probe_bounds_and_validates_numbers(self):
        raw=json.dumps({'models':[{'name':'a','size':100,'size_vram':False,'context_length':8192}]}).encode()
        with patch('runtime_diagnostics._loopback.open',return_value=io.BytesIO(raw)):
            state=running_probe(11435)
        self.assertIsNone(state['models'][0]['size_vram'])
        for raw in (b'[]',b'{"models":[null]}',b'x'*(MAX_BYTES+1)):
            with patch('runtime_diagnostics._loopback.open',return_value=io.BytesIO(raw)):
                self.assertEqual(running_probe(11435)['state'],'invalid')

    def test_residency_does_not_assume_gpu_absent_when_unloaded(self):
        with patch('runtime_diagnostics._loopback.open',return_value=io.BytesIO(b'{"models":[]}')) as request:
            state=running_probe(11435)
        self.assertEqual(request.call_args.args[0],'http://127.0.0.1:11435/api/ps')
        self.assertIn('GPU availability is not established',residency_summary(state,'a'))
