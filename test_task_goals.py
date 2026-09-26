import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import agent_core as core
from task_goals import normalize_goal, goal_context


def goal(status='pending'):
    return {'objective':'Repair game score','criteria':[{'text':'Score calculation works','status':status,'evidence':'Exit 0 from score checks' if status=='met' else ''}],'next_action':'Inspect score.py'}


def response(name=None,args=None,text='Done.'):
    message={'content':text}
    if name:message['tool_calls']=[{'id':'call-'+name,'function':{'name':name,'arguments':args or {}}}]
    return {'message':message,'done':True}


class GoalTests(unittest.TestCase):
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
