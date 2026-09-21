import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import agent_core as core


class WorkerTests(unittest.TestCase):
    def test_worker_reads_evidence_and_cannot_write_or_delegate(self):
        payloads=[]
        def stream(url,payload,cancel):
            payloads.append(payload)
            if len(payloads)==1:
                calls=[{'function':{'name':'read_file','arguments':{'path':'game.py'}}},
                       {'function':{'name':'write_file','arguments':{'path':'game.py','content':'bad'}}},
                       {'function':{'name':'delegate_review','arguments':{'task':'nested','role':'reviewer'}}}]
                return iter([{'message':{'tool_calls':calls},'done':True}])
            return iter([{'message':{'content':'game.py returns 1; investigate scoring.'},'done':True}])
        with tempfile.TemporaryDirectory() as folder,patch.object(core,'stream_chat',side_effect=stream),patch.object(core,'model_supports_vision',return_value=False):
            file=Path(folder)/'game.py';file.write_text('return 1')
            report=json.loads(core.run_subagent('fixture','fixture',folder,'Review game.py','reviewer',threading.Event(),lambda *_:None))
            self.assertEqual(file.read_text(),'return 1')
        names={t['function']['name'] for t in payloads[0]['tools']}
        self.assertFalse(names & {'write_file','run_command','computer','delegate_review','remote_run_command'})
        results=[m['content'] for m in payloads[1]['messages'] if m['role']=='tool']
        self.assertIn('return 1',results)
        self.assertEqual(sum('PermissionError' in r for r in results),2)
        self.assertEqual(report['status'],'completed')

    def test_cancelled_worker_never_calls_model(self):
        cancel=threading.Event();cancel.set()
        with patch.object(core,'stream_chat') as stream:
            with self.assertRaises(InterruptedError):core.run_subagent('fixture','fixture','.','Review','reviewer',cancel,lambda *_:None)
            stream.assert_not_called()

    def test_parent_can_use_worker_report(self):
        def stream(url,payload,cancel):
            if payload['messages'][-1]['role']=='tool':
                return iter([{'message':{'content':'Reviewed worker findings.'},'done':True}])
            return iter([{'message':{'tool_calls':[{'function':{'name':'delegate_review','arguments':{'role':'test_planner','task':'Review test gaps'}}}]},'done':True}])
        events=[]
        with tempfile.TemporaryDirectory() as folder,patch.object(core,'stream_chat',side_effect=stream),patch.object(core,'model_supports_vision',return_value=False),patch.object(core,'run_subagent',return_value='{"report":"Missing score test"}') as worker:
            core.run_agent('fixture','fixture',[{'role':'user','content':'Use a subagent to review tests'}],folder,False,threading.Event(),lambda k,v:events.append((k,v)))
            worker.assert_called_once()
        self.assertTrue(any(k=='result' and 'Missing score test' in v for k,v in events))
