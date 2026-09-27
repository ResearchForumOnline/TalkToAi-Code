import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import agent_core as core
from task_goals import normalize_goal, goal_context
from agent_workflow import validate_calls


def goal(status='pending'):
    return {'objective':'Repair game score','criteria':[{'text':'Score calculation works','status':status,'evidence':'Exit 0 from score checks' if status=='met' else ''}],'next_action':'Inspect score.py'}


def response(name=None,args=None,text='Done.'):
    message={'content':text}
    if name:message['tool_calls']=[{'id':'call-'+name,'function':{'name':name,'arguments':args or {}}}]
    return {'message':message,'done':True}


class GoalTests(unittest.TestCase):
    def test_native_goal_schema_accepts_both_supported_criteria_forms(self):
        entries=[{'text':'Score works','status':'pending'}]
        for criteria in (entries,json.dumps(entries)):
            with self.subTest(criteria_type=type(criteria).__name__):
                args={'objective':'Repair score','criteria':criteria}
                call={'function':{'name':'update_task_goal','arguments':args}}
                self.assertEqual(validate_calls([call],core.GOAL_TOOLS)[0]['function']['arguments'],args)
                self.assertEqual(normalize_goal(args)['next_action'],'')

    def run_sequence(self,sequence,saved=None,rounds=12,keep=True):
        events=[];payloads=[]
        def stream(_url,payload,_cancel):
            payloads.append(copy.deepcopy(payload))
            return iter([sequence[min(len(payloads)-1,len(sequence)-1)]])
        with tempfile.TemporaryDirectory() as folder,patch.object(core,'stream_chat',side_effect=stream),patch.object(core,'model_supports_vision',return_value=False),patch.object(core,'AUTO_CONTEXT',False):
            Path(folder,'score.py').write_text('return 1')
            core.run_agent('fixture','fixture',[{'role':'user','content':'Continue the current goal; prioritize the latest request.'}],folder,True,threading.Event(),lambda k,v:events.append((k,v)),rounds=rounds,keep_going=keep,task_goal=saved)
        return payloads,events

    def test_ids_survive_reordering_and_new_items(self):
        first=normalize_goal({'objective':'Build','criteria':[{'text':'A'},{'text':'B'}]})
        second=normalize_goal({'objective':'Build','criteria':[{'text':'B'},{'text':'C'},{'text':'A'}]},previous=first)
        self.assertEqual([c['id'] for c in second['criteria']],['c2','c3','c1'])

    def test_reject_invalid_claims_and_oversized_input(self):
        for invalid in ({'objective':'x','criteria':[]},{'objective':'x'*2001,'criteria':[{'text':'A'}]},
                        {'objective':'x','criteria':[{'text':'A','status':'met'}]},
                        {'objective':'x','criteria':[{'text':'A','id':[]}]},
                        {'objective':'x','criteria':[{'text':'A','id':0}]},
                        {'objective':'x','criteria':[{'text':'A','id':'c1'},{'text':'B','id':'c1'}]}):
            with self.assertRaises(ValueError):normalize_goal(invalid)

    def test_resume_contains_saved_objective_and_user_priority(self):
        payloads,events=self.run_sequence([response()],saved=goal())
        self.assertEqual(len(payloads),2)
        system=payloads[0]['messages'][0]['content']
        self.assertIn('Repair game score',system)
        self.assertIn('current user request and steering take priority',system)
        checkpoint=[v for k,v in events if k=='goal_checkpoint'][-1]
        self.assertEqual(checkpoint['state'],'paused')
        self.assertIn('Goal c1 pending',checkpoint['blockers'][0])

    def test_blocked_criteria_remain_unfinished(self):
        _,events=self.run_sequence([response()],saved=goal('blocked'))
        self.assertEqual([v for k,v in events if k=='goal_checkpoint'][-1]['state'],'paused')

    def test_lazy_update_emits_persistent_goal_and_replaces_old_context(self):
        update=goal('met');update['criteria']=json.dumps(update['criteria'])
        payloads,events=self.run_sequence([response('enable_tools',{'group':'goals'}),response('update_task_goal',update),response()],saved=goal())
        emitted=[v for k,v in events if k=='task_goal']
        self.assertEqual(emitted[0]['criteria'][0]['status'],'met')
        system=payloads[-1]['messages'][0]['content']
        self.assertEqual(system.count('Saved task goal ('),1)
        self.assertIn('"status":"met"',system)
        self.assertEqual([v for k,v in events if k=='goal_checkpoint'][-1]['state'],'completed')
        self.assertFalse(any(k=='verification' for k,v in events))

    def test_resumed_goal_update_is_available_without_discovery(self):
        payloads,events=self.run_sequence([response('update_task_goal',goal('met')),response()],saved=goal())
        self.assertIn('update_task_goal',{tool['function']['name'] for tool in payloads[0]['tools']})
        self.assertEqual(len(payloads),2)
        self.assertEqual([v for k,v in events if k=='goal_checkpoint'][-1]['state'],'completed')

    def test_corrected_goal_validation_error_can_complete(self):
        invalid=goal('met');invalid['criteria'][0]['status']='completed'
        valid=goal('met')
        _,events=self.run_sequence([response('enable_tools',{'group':'goals'}),response('update_task_goal',invalid),response('update_task_goal',valid),response()])
        self.assertEqual([v for k,v in events if k=='goal_checkpoint'][-1]['state'],'completed')
        self.assertTrue(any(k=='result' and 'ValueError' in v for k,v in events))

    def test_uncorrected_goal_validation_blocks_completion_and_extra_pass(self):
        invalid=goal('met');invalid['criteria'][0]['status']='completed'
        _,events=self.run_sequence([response('enable_tools',{'group':'goals'}),response('update_task_goal',invalid),response()])
        checkpoint=[v for k,v in events if k=='goal_checkpoint'][-1]
        self.assertEqual(checkpoint['state'],'paused')
        self.assertIn('Task goal update remains invalid; correct its metadata',checkpoint['blockers'])
        payloads,events=self.run_sequence([response('enable_tools',{'group':'goals'}),response('read_file',{'path':'score.py'}),response('update_task_goal',invalid)],rounds=3)
        self.assertEqual(len(payloads),3)
        self.assertEqual([v for k,v in events if k=='goal_checkpoint'][-1]['state'],'paused')

    def test_valid_goal_cannot_clear_unrelated_file_failure(self):
        invalid=goal('met');invalid['criteria'][0]['status']='completed'
        _,events=self.run_sequence([response('enable_tools',{'group':'goals'}),response('read_file',{'path':'missing.py'}),response('update_task_goal',invalid),response('update_task_goal',goal('met')),response()])
        checkpoint=[v for k,v in events if k=='goal_checkpoint'][-1]
        self.assertEqual(checkpoint['state'],'paused')
        self.assertIn('Tool failures remain without verified recovery',checkpoint['blockers'])
        self.assertFalse(any('goal update remains invalid' in blocker for blocker in checkpoint['blockers']))

    def test_goal_metadata_alone_does_not_extend_pass(self):
        update=goal();update['criteria']=json.dumps(update['criteria'])
        payloads,events=self.run_sequence([response('enable_tools',{'group':'goals'}),response('update_task_goal',update)],rounds=2)
        self.assertEqual(len(payloads),2)
        self.assertEqual([v for k,v in events if k=='goal_checkpoint'][-1]['progress_observations'],0)

    def test_no_goal_preserves_single_final_response(self):
        payloads,events=self.run_sequence([response()])
        self.assertEqual(len(payloads),1)
        self.assertEqual([v for k,v in events if k=='goal_checkpoint'][-1]['state'],'completed')

    def test_context_survives_history_compaction(self):
        normalized=normalize_goal(goal())
        history=[{'role':'system','content':'System'+goal_context(normalized)}]
        for i in range(40):history.extend([{'role':'user','content':'old '*400},{'role':'assistant','content':'reply '*400}])
        history.append({'role':'user','content':'Continue'})
        window=core.context_window(history,12000)
        self.assertIn('Repair game score',window[0]['content'])
        self.assertTrue(any(message['role']=='user' and message['content']=='Continue' for message in window))


if __name__=='__main__':unittest.main()
