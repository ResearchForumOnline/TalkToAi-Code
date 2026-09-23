import copy
import json
import tempfile
import threading
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_core as core
from agent_workflow import ToolCatalog, check_evidence, normalize_plan, previous_plan, validate_calls


def call(name, **args):
    return {'function':{'name':name,'arguments':args}}


def response(content='', calls=None, reason='stop'):
    message={'content':content}
    if calls is not None:message['tool_calls']=calls
    return {'message':message,'done':True,'done_reason':reason}


class WorkflowHelperTests(unittest.TestCase):
    def test_automatic_continuation_retains_original_request_and_tool_chain(self):
        history=[{'role':'system','content':'System'}, {'role':'user','content':'Only inspect this project. Never deploy.'},
                 {'role':'assistant','content':'Observed '*200,'tool_calls':[call('read_file',path='a')]},
                 {'role':'tool','tool_name':'read_file','content':'File evidence'},
                 {'role':'user','_automation_nudge':True,'content':'Continue the unfinished task.'}]
        compact=core.context_window(history,budget=200)
        self.assertIn('Never deploy',compact[1]['content'])
        self.assertEqual(compact[-2]['role'],'tool')
        self.assertTrue(all('_automation_nudge' not in m for m in compact))

    def test_plan_validation_and_history_recovery(self):
        plan=normalize_plan([{'step':'Inspect','status':'completed'},{'step':'Fix','status':'in_progress'}],'Started the fix')
        history=[{'role':'tool','tool_name':'update_plan','content':json.dumps(plan)}]
        self.assertEqual(previous_plan(history),plan)
        with self.assertRaises(ValueError):normalize_plan([{'step':'A','status':'in_progress'},{'step':'B','status':'in_progress'}])
        with self.assertRaises(ValueError):normalize_plan([{'step':'A','status':'pending'},{'step':'a','status':'pending'}])
        with self.assertRaises(ValueError):normalize_plan([])

    def test_plan_rejects_malformed_saved_results(self):
        self.assertIsNone(previous_plan([{'role':'tool','tool_name':'update_plan','content':'not json'}]))
        for entry in (None,{}, {'step':'', 'status':'pending'}, {'step':'A','status':'guessed'}):
            with self.assertRaises(ValueError):normalize_plan([entry])

    def test_batch_validation_accepts_json_arguments_and_rejects_non_objects(self):
        validated=validate_calls([{'id':'one','function':{'name':'read_file','arguments':'{"path":"a.py"}'}}])
        self.assertEqual(validated[0]['function']['arguments'],{'path':'a.py'})
        for batch in ([None],[{'function':{'name':'read_file','arguments':[]}}],[call('x')]*17):
            with self.assertRaises(ValueError):validate_calls(batch)

    def test_catalog_is_idempotent_and_cannot_expand_authority(self):
        tool=core.schema('browser','example',{});active=[]
        catalog=ToolCatalog({'browser':[tool]}, {'ssh':'Not authorized'})
        catalog.enable('browser',active);catalog.enable('browser',active)
        self.assertEqual(len(active),1)
        self.assertIn('browser',json.loads(catalog.enable('',active))['available'])
        with self.assertRaises(PermissionError):catalog.enable('ssh',active)
        with self.assertRaises(PermissionError):catalog.enable('unknown',active)

    def test_check_evidence_requires_actual_successful_commands(self):
        self.assertEqual(check_evidence('Exit 0\nRan 3 tests\nOK')['status'],'passed')
        self.assertEqual(check_evidence('npm run test\nExit 1\nfailed')['status'],'failed')
        self.assertEqual(check_evidence('Exit 0\nSCRIPT ERROR: invalid')['status'],'failed')
        self.assertEqual(check_evidence('Exit 0\nRan 0 tests\nOK')['status'],'unverified')
        self.assertEqual(check_evidence('ValueError: no checks found')['status'],'unverified')


class AgentAutomationTests(unittest.TestCase):
    def test_jobs_and_outputs_can_be_discovered_and_executed_without_keywords(self):
        events=[];count=[0]
        def stream(url,payload,cancel):
            count[0]+=1
            if count[0]==1:return iter([response(calls=[call('enable_tools',group='jobs'),call('enable_tools',group='outputs')])])
            if count[0]==2:return iter([response(calls=[call('start_process',executable=sys.executable,arguments=json.dumps(['-c','print("fixture complete")']),cwd='',timeout_seconds='10')])])
            if count[0]==3:
                result=json.loads(payload['messages'][-1]['content'])
                return iter([response(calls=[call('poll_process',job_id=result['id'],cursor='0',wait_seconds='5'),call('register_output',path='report.txt',title='Report')])])
            return iter([response('Inspected process output and registered report; no claim of test coverage.')])
        with tempfile.TemporaryDirectory() as root,patch.object(core,'stream_chat',side_effect=stream),patch.object(core,'model_supports_vision',return_value=False):
            Path(root,'report.txt').write_text('fixture evidence',encoding='utf-8')
            core.run_agent('fixture','fixture',[{'role':'user','content':'Do the requested project work'}],root,True,threading.Event(),lambda k,v:events.append((k,v)))
        self.assertTrue(any(k=='job' and v['state']=='completed' for k,v in events))
        self.assertTrue(any(k=='artifact' and v.get('title')=='Report' for k,v in events))
        self.assertFalse(any(k=='verification' and v['status']=='passed' for k,v in events))

    def run_sequence(self,root,sequence,act=True,text='Improve this project',tools=None,rounds=16,history=None):
        payloads=[];events=[]
        def stream(url,payload,cancel):
            payloads.append(copy.deepcopy(payload))
            if not sequence:raise AssertionError('Unexpected additional model request')
            return iter([sequence.pop(0)])
        tools=tools or core.ProjectTools(root,act)
        with patch.object(core,'stream_chat',side_effect=stream),patch.object(core,'model_supports_vision',return_value=False),patch.object(core,'DESKTOP_ACCESS',False),patch.object(core,'ACTIVE_REMOTE_ALLOWED',False),patch.object(core,'REMOTE_PILOT',False):
            core._run_agent('fixture','fixture',history or [{'role':'user','content':text}],root,act,threading.Event(),lambda k,v:events.append((k,v)),rounds,None,tools)
        return payloads,events

    def test_agent_loads_browser_tools_without_request_keywords(self):
        with tempfile.TemporaryDirectory() as root:
            tools=core.ProjectTools(root,True)
            original=tools.execute
            with patch.object(tools,'execute',side_effect=lambda name,args:'Observed page' if name=='browser' else original(name,args)):
                payloads,events=self.run_sequence(root,[response(calls=[call('enable_tools',group='browser')]),response(calls=[call('browser',action='inspect',target='',value='')]),response('Inspected the page.')],text='Improve this landing screen',tools=tools)
        names=lambda p:{t['function']['name'] for t in p['tools']}
        self.assertNotIn('browser',names(payloads[0]));self.assertIn('browser',names(payloads[1]))
        self.assertTrue(any(k=='result' and v=='Observed page' for k,v in events))

    def test_plan_and_disabled_access_cannot_be_bypassed_with_catalog(self):
        with tempfile.TemporaryDirectory() as root:
            payloads,events=self.run_sequence(root,[response(calls=[call('enable_tools',group='browser'),call('enable_tools',group='desktop'),call('enable_tools',group='ssh')]),response('These operations are unavailable in this mode.')],act=False)
        names={t['function']['name'] for t in payloads[-1]['tools']}
        self.assertFalse(names&{'browser','run_checks','write_file','connect_remote','computer'})
        self.assertEqual(sum(k=='result' and 'PermissionError' in v for k,v in events),3)

    def test_project_overview_does_not_require_an_extra_model_turn(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root)/'package.json').write_text('{"scripts":{"build":"vite build"}}')
            payloads,events=self.run_sequence(root,[response('Project inspected.')],act=False)
        self.assertEqual(len(payloads),1)
        self.assertIn('npm run build',payloads[0]['messages'][0]['content'])
        self.assertTrue(any(k=='project_context' for k,v in events))

    def test_invalid_batch_does_not_execute_the_first_valid_call(self):
        bad=call('read_file',path='a');bad['function']['arguments']='{invalid'
        with tempfile.TemporaryDirectory() as root:
            _,events=self.run_sequence(root,[response(calls=[call('write_file',path='must-not-exist.txt',content='bad'),bad]),response('No action executed.')])
            self.assertFalse((Path(root)/'must-not-exist.txt').exists())
        self.assertFalse(any(k=='tool' for k,v in events))
        self.assertTrue(any(k=='status' and 'Repairing invalid' in v for k,v in events))

    def test_output_limit_continues_without_manual_prompt(self):
        with tempfile.TemporaryDirectory() as root:
            payloads,events=self.run_sequence(root,[response('Part one',reason='length'),response('Completed response.')],act=False)
        self.assertEqual(len(payloads),2)
        self.assertIn('Do not repeat actions',payloads[1]['messages'][-1]['content'])
        self.assertIn(('status','Ready'),events)

    def test_output_continuation_is_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            payloads,events=self.run_sequence(root,[response('partial',reason='length') for _ in range(3)],act=False)
        self.assertEqual(len(payloads),3)
        self.assertTrue(any(k=='status' and v.startswith('Paused at the output') for k,v in events))

    def test_incomplete_tool_batch_is_never_executed(self):
        with tempfile.TemporaryDirectory() as root:
            _,events=self.run_sequence(root,[response(calls=[call('write_file',path='bad.txt',content='partial')],reason='length'),response('No action executed.')])
            self.assertFalse((Path(root)/'bad.txt').exists())
        self.assertFalse(any(k=='tool' for k,v in events))

    def test_unfinished_plan_gets_a_follow_through_turn(self):
        pending=[{'step':'Inspect','status':'in_progress'}];done=[{'step':'Inspect','status':'completed'}]
        with tempfile.TemporaryDirectory() as root:
            payloads,events=self.run_sequence(root,[response(calls=[call('update_plan',steps=pending,explanation='Inspecting')]),response('Summary'),response(calls=[call('update_plan',steps=done,explanation='Inspection complete')]),response('Inspection finished.')],act=False)
        self.assertEqual(len(payloads),4)
        self.assertIn(('status','Reviewing unfinished task steps'),events)
        self.assertEqual([v for k,v in events if k=='plan'][-1]['steps'],done)

    def test_existing_plan_is_loaded_from_saved_history(self):
        plan=normalize_plan([{'step':'Finish menu','status':'blocked'}],'Need the missing source file')
        history=[{'role':'user','content':'Build menu'},{'role':'tool','tool_name':'update_plan','content':json.dumps(plan)},{'role':'user','content':'Continue'}]
        with tempfile.TemporaryDirectory() as root:
            payloads,_=self.run_sequence(root,[response('The source file is still missing.')],act=False,history=history)
        self.assertIn('Finish menu',payloads[0]['messages'][0]['content'])

    def test_repeated_identical_failure_stops_reexecuting_it(self):
        with tempfile.TemporaryDirectory() as root:
            tools=core.ProjectTools(root,True);original=tools.execute
            with patch.object(tools,'execute',wraps=original) as execute:
                _,events=self.run_sequence(root,[response(calls=[call('read_file',path='missing.py')]) for _ in range(3)]+[response('File is missing.')],tools=tools)
                self.assertEqual(execute.call_count,2)
        self.assertTrue(any(k=='result' and 'already failed twice' in v for k,v in events))

    def test_shell_inspection_does_not_count_as_verifying_an_edit(self):
        with tempfile.TemporaryDirectory() as root:
            tools=core.ProjectTools(root,True);original=tools.execute
            def execute(name,args):return 'Exit 0\nDirectory listing' if name=='run_command' else original(name,args)
            with patch.object(tools,'execute',side_effect=execute):
                _,events=self.run_sequence(root,[response(calls=[call('write_file',path='a.txt',content='test'),call('run_command',command='Get-ChildItem')]),response('Created file.'),response('No supported tests exist; not verified.')],tools=tools)
        self.assertIn(('status','Verifying changes before finishing'),events)

    def test_check_result_status_comes_from_tools_not_model_claims(self):
        with tempfile.TemporaryDirectory() as root:
            tools=core.ProjectTools(root,True)
            with patch.object(tools,'execute',return_value='Exit 1\nTest failed'):
                _,events=self.run_sequence(root,[response(calls=[call('run_checks')]),response('Tests passed!')],tools=tools)
        self.assertEqual([v for k,v in events if k=='verification'][-1]['status'],'failed')
        self.assertIn(('status','Response finished · checks failed'),events)


if __name__=='__main__':unittest.main()
