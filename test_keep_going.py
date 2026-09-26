import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import agent_core as core


def response(name=None,args=None,text=''):
    message={'content':text}
    if name:message['tool_calls']=[{'id':'call_'+str(args),'function':{'name':name,'arguments':args or {}}}]
    return {'message':message,'done':True}


class KeepGoingTests(unittest.TestCase):
    def fixture(self,sequence,rounds=2,keep=True,cancel_at=None,worker=False,improvement=False,check_results=None,running_jobs=None):
        events=[];payloads=[];cancel=threading.Event()
        jobs=Mock() if running_jobs is not None else None
        if jobs is not None:jobs.running.return_value=running_jobs
        checks=iter(check_results) if check_results is not None else None
        original_execute=core.ProjectTools.execute
        def execute(tools,name,args):
            if name=='run_checks' and checks is not None:return next(checks)
            return original_execute(tools,name,args)
        def stream(_url,payload,_cancel):
            payloads.append(copy.deepcopy(payload))
            item=sequence[min(len(payloads)-1,len(sequence)-1)]
            if cancel_at==len(payloads):cancel.set()
            return iter([item])
        with tempfile.TemporaryDirectory() as folder:
            for i in range(8):Path(folder,f'{i}.txt').write_text(f'Observed source {i}')
            with patch.object(core,'stream_chat',side_effect=stream),patch.object(core,'model_supports_vision',return_value=False),patch.object(core,'AUTO_CONTEXT',False),patch.object(core.ProjectTools,'execute',execute):
                if worker or improvement:
                    tools=core.ProjectTools(folder,True,cancel)
                    try:core._run_agent('fixture','fixture',[{'role':'user','content':'Inspect the requested source files'}],folder,True,cancel,
                                       lambda k,v:events.append((k,v)),rounds,None,tools,worker_mode=worker,improvement_mode=improvement,keep_going=keep)
                    finally:
                        if tools.jobs:tools.jobs.close()
                else:
                    core.run_agent('fixture','fixture',[{'role':'user','content':'Inspect the requested source files'}],folder,True,cancel,
                                   lambda k,v:events.append((k,v)),rounds=rounds,keep_going=keep,jobs=jobs)
        return payloads,events

    def test_progress_continues_same_history_until_third_pass(self):
        sequence=[response('read_file',{'path':f'{i}.txt'}) for i in range(5)]+[response(text='Inspected the five requested files.')]
        payloads,events=self.fixture(sequence)
        self.assertEqual(len(payloads),6)
        checkpoints=[v for k,v in events if k=='goal_checkpoint']
        self.assertEqual([v['state'] for v in checkpoints],['continuing','continuing','completed'])
        self.assertEqual([v['pass'] for v in checkpoints],[1,2,3])
        self.assertEqual([v['steps'] for v in checkpoints],[2,4,6])
        for payload in payloads:
            pending=set()
            for message in core.provider_messages(payload['messages']):
                if message['role']=='tool':
                    self.assertIn(message['tool_call_id'],pending);pending.remove(message['tool_call_id'])
                else:
                    self.assertFalse(pending)
                    pending.update(c['id'] for c in message.get('tool_calls',[]))
            self.assertFalse(pending)
        self.assertTrue(any('Observed source 0' in m.get('content','') for m in payloads[-1]['messages']))

    def test_unchanged_source_reads_do_not_fuel_all_three_passes(self):
        payloads,events=self.fixture([response('read_file',{'path':'0.txt'})])
        self.assertEqual(len(payloads),4)
        checkpoint=[v for k,v in events if k=='goal_checkpoint'][-1]
        self.assertEqual(checkpoint['state'],'paused')
        self.assertEqual(checkpoint['progress_observations'],0)

    def test_administrative_tools_alone_do_not_count_as_progress(self):
        payloads,events=self.fixture([response('enable_tools',{'group':''})])
        self.assertEqual(len(payloads),2)
        self.assertEqual([v['state'] for k,v in events if k=='goal_checkpoint'],['paused'])

    def test_error_prevents_automatic_next_pass(self):
        payloads,events=self.fixture([response('read_file',{'path':'0.txt'}),response('read_file',{'path':'missing.txt'})])
        self.assertEqual(len(payloads),2)
        self.assertEqual([v['state'] for k,v in events if k=='goal_checkpoint'],['paused'])

    def test_failed_check_repaired_then_passed_check_allows_next_pass(self):
        sequence=[response('run_checks',{}),response('edit_file',{'path':'0.txt','old_text':'Observed source 0','new_text':'Repaired source'}),
                  response('run_checks',{}),response(text='Repaired and checked the requested source.')]
        payloads,events=self.fixture(sequence,rounds=3,check_results=['Exit 1\nTest failed','Exit 0\nTests passed'])
        self.assertEqual(len(payloads),4)
        checkpoints=[v for k,v in events if k=='goal_checkpoint']
        self.assertEqual([v['state'] for v in checkpoints],['continuing','completed'])
        self.assertEqual(checkpoints[0]['verification']['status'],'passed')
        self.assertEqual(checkpoints[0]['changes'],1)

    def test_successful_read_does_not_clear_a_failed_check(self):
        sequence=[response('run_checks',{}),response('read_file',{'path':'0.txt'})]
        payloads,events=self.fixture(sequence,check_results=['Exit 1\nTest failed'])
        self.assertEqual(len(payloads),2)
        self.assertEqual([v['state'] for k,v in events if k=='goal_checkpoint'],['paused'])

    def test_edits_after_recovery_require_fresh_pass_before_extension(self):
        sequence=[response('run_checks',{}),response('run_checks',{}),
                  response('edit_file',{'path':'0.txt','old_text':'Observed source 0','new_text':'Later edit'})]
        payloads,events=self.fixture(sequence,rounds=3,check_results=['Exit 1\nTest failed','Exit 0\nTests passed'])
        self.assertEqual(len(payloads),3)
        checkpoint=[v for k,v in events if k=='goal_checkpoint'][-1]
        self.assertEqual(checkpoint['state'],'paused')
        self.assertEqual(checkpoint['verification']['status'],'stale')

    def test_research_evidence_check_is_optional_in_provider_schema(self):
        parameters=core.RESEARCH_TOOLS[0]['function']['parameters']
        self.assertIn('verify_evidence',parameters['properties'])
        self.assertNotIn('verify_evidence',parameters['required'])

    def test_final_prose_cannot_hide_failed_tool_action(self):
        sequence=[response('read_file',{'path':'missing.txt'}),response(text='Everything is complete.')]
        payloads,events=self.fixture(sequence,rounds=8)
        self.assertEqual(len(payloads),3)
        checkpoint=[v for k,v in events if k=='goal_checkpoint'][-1]
        self.assertEqual(checkpoint['state'],'paused')
        self.assertIn('Tool failures remain without verified recovery',checkpoint['blockers'])
        self.assertFalse(any(k=='status' and v=='Ready' for k,v in events))
        self.assertIn('recovery is not verified',payloads[-1]['messages'][-1]['content'])

    def test_completion_review_can_recover_failed_check(self):
        sequence=[response('run_checks',{}),response(text='Done.'),response('run_checks',{}),response(text='Checks pass now.')]
        payloads,events=self.fixture(sequence,rounds=8,check_results=['Exit 1\nFailed','Exit 0\nPassed'])
        self.assertEqual(len(payloads),4)
        self.assertEqual([v['state'] for k,v in events if k=='goal_checkpoint'],['completed'])
        self.assertEqual([v for k,v in events if k=='status'][-1],'Ready')

    def test_final_response_with_owned_jobs_is_unfinished_after_one_review(self):
        payloads,events=self.fixture([response(text='The build is complete.')],rounds=8,running_jobs=['job-123'])
        self.assertEqual(len(payloads),2)
        checkpoint=[v for k,v in events if k=='goal_checkpoint'][-1]
        self.assertEqual(checkpoint['state'],'paused')
        self.assertIn('Owned processes still running: job-123',checkpoint['blockers'])
        self.assertFalse(any(k=='status' and v=='Ready' for k,v in events))

    def test_last_available_step_still_cannot_claim_failed_task_completed(self):
        payloads,events=self.fixture([response('read_file',{'path':'missing.txt'}),response(text='Done.')],rounds=2,keep=False)
        self.assertEqual(len(payloads),2)
        self.assertIn('task unfinished',[v for k,v in events if k=='status'][-1])

    def test_complete_response_finishes_without_spending_remaining_passes(self):
        payloads,events=self.fixture([response(text='Finished the requested explanation.')])
        self.assertEqual(len(payloads),1)
        self.assertEqual([v['state'] for k,v in events if k=='goal_checkpoint'],['completed'])

    def test_cancel_stops_before_executing_returned_tool(self):
        payloads,events=self.fixture([response('read_file',{'path':'0.txt'})],cancel_at=1)
        self.assertEqual(len(payloads),1)
        self.assertFalse(any(k=='tool' for k,v in events))
        self.assertEqual([v['state'] for k,v in events if k=='goal_checkpoint'],['stopped'])

    def test_worker_improvement_and_default_stay_single_pass(self):
        sequence=[response('read_file',{'path':f'{i}.txt'}) for i in range(6)]
        for options in ({'keep':False},{'worker':True},{'improvement':True}):
            payloads,events=self.fixture(sequence,**options)
            self.assertEqual(len(payloads),2)
            self.assertFalse(any(k=='goal_checkpoint' for k,v in events))

    def test_even_continuous_new_observations_stop_at_selected_three_pass_limit(self):
        payloads,events=self.fixture([response('read_file',{'path':f'{i}.txt'}) for i in range(8)])
        self.assertEqual(len(payloads),6)
        self.assertEqual([v['state'] for k,v in events if k=='goal_checkpoint'][-1],'paused')


if __name__=='__main__':unittest.main()
