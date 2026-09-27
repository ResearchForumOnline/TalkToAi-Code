import tempfile
import threading
import unittest
from unittest.mock import patch

import agent_core as core
from progress_guard import DiscoveryProgressGuard


class ProgressGuardTests(unittest.TestCase):
    def test_identical_discovery_warns_then_bounds_repetition(self):
        guard=DiscoveryProgressGuard('selected/NIGHTFALL')
        for i in range(3):
            self.assertIsNone(guard.before('list_files',{}))
            notice=guard.observe('list_files',{},'project.godot\nscene.gd')
            self.assertEqual(notice is not None,i==2)
        self.assertIn('selected/NIGHTFALL',notice)
        self.assertIn('not task completion',notice)
        self.assertIn('not executed',guard.before('list_files',{}))
        self.assertFalse(guard.paused)
        guard.before('list_files',{})
        self.assertTrue(guard.paused)

    def test_different_results_arguments_and_mutation_reset_counts(self):
        guard=DiscoveryProgressGuard('project')
        for output in ('first','second','third'):
            self.assertIsNone(guard.observe('search_code',{'query':'player'},output))
        self.assertIsNone(guard.observe('search_code',{'query':'enemy'},'third'))
        for _ in range(3):guard.observe('list_files',{},'same')
        self.assertIsNotNone(guard.before('list_files',{}))
        guard.reset()
        self.assertIsNone(guard.before('list_files',{}))
        self.assertIsNone(guard.observe('list_files',{},'same'))

    def test_dynamic_browser_polling_and_source_reads_are_not_blocked(self):
        guard=DiscoveryProgressGuard('project')
        for name in ('browser','poll_process','read_file','read_project_files'):
            for _ in range(10):
                self.assertIsNone(guard.before(name,{}))
                self.assertIsNone(guard.observe(name,{},'same'))
        self.assertFalse(guard.entries)

    def test_signature_cache_is_bounded(self):
        guard=DiscoveryProgressGuard('project')
        for i in range(200):guard.observe('search_code',{'query':str(i)},'none')
        self.assertEqual(len(guard.entries),128)

    def run_fixture(self,commands,cancel=None,command_result=None):
        payloads=[];events=[];executed=[]
        def stream(_url,payload,_cancel):
            payloads.append(payload)
            name,args=commands[min(len(payloads)-1,len(commands)-1)]
            return iter([{'message':{'tool_calls':[{'function':{'name':name,'arguments':args}}]},'done':True}])
        original=core.ProjectTools.execute
        def execute(tools,name,args):
            executed.append(name)
            if name=='list_files':return 'project.godot\nscene.gd'
            if name=='run_command' and command_result is not None:return command_result
            return original(tools,name,args)
        with tempfile.TemporaryDirectory() as folder,patch.object(core,'stream_chat',side_effect=stream), \
                patch.object(core,'model_supports_vision',return_value=False),patch.object(core.ProjectTools,'execute',execute), \
                patch.object(core,'AUTO_CONTEXT',False):
            core.run_agent('http://fixture','fixture',[{'role':'user','content':'Improve this game'}],folder,True,
                           cancel or threading.Event(),lambda k,v:events.append((k,v)),rounds=9)
        return payloads,events,executed

    def test_agent_pauses_unproductive_loop_without_marking_complete(self):
        payloads,events,executed=self.run_fixture([('list_files',{})])
        self.assertEqual(len(payloads),5)
        self.assertEqual(executed,['list_files']*3)
        self.assertTrue(any(k=='status' and 'task remains unfinished' in v for k,v in events))
        self.assertFalse(any(k=='status' and v=='Ready' for k,v in events))
        self.assertEqual(sum(k=='message' and v.get('role')=='tool' for k,v in events),5)

    def test_agent_file_change_allows_fresh_discovery(self):
        commands=[('list_files',{})]*3+[('write_file',{'path':'new.txt','content':'new'})]+[('list_files',{})]*5
        _,events,executed=self.run_fixture(commands)
        self.assertEqual(executed.count('list_files'),6)
        self.assertIn('write_file',executed)
        self.assertTrue(any(k=='change' for k,v in events))

    def test_shell_results_cannot_reset_unchanged_discovery_loop(self):
        commands=[('list_files',{})]*3+[('run_command',{'command':'dir /s'}),('list_files',{}),('run_command',{'command':'dir /s /b'}),('list_files',{})]
        for outcome in ('Exit 1\nPowerShell syntax error','Exit 0\nSame source listing'):
            payloads,events,executed=self.run_fixture(commands,command_result=outcome)
            self.assertEqual(len(payloads),7)
            self.assertEqual(executed.count('list_files'),3)
            self.assertTrue(any(k=='status' and 'task remains unfinished' in v for k,v in events))

    def test_cancelled_agent_never_executes_discovery(self):
        cancel=threading.Event();cancel.set()
        payloads,_,executed=self.run_fixture([('list_files',{})],cancel)
        self.assertFalse(payloads)
        self.assertFalse(executed)


if __name__=='__main__':unittest.main()
