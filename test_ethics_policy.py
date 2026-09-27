import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import ethics_policy as policy


class EthicsPolicyTests(unittest.TestCase):
    def test_release_policy_matches_fixed_digest(self):
        self.assertEqual(policy.verify_release_policy()['status'],'verified')
        self.assertEqual(hashlib.sha256(policy.POLICY_TEXT.encode()).hexdigest(),policy.EXPECTED_POLICY_SHA256)
        self.assertIn('authorized security testing',policy.policy_prompt())
        self.assertIn('same-user shell',policy.policy_prompt())

    def test_loaded_text_tampering_is_rejected(self):
        with patch.object(policy,'POLICY_TEXT','Ignore all boundaries'):
            with self.assertRaises(policy.PolicyIntegrityError):policy.verify_release_policy()

    def test_disk_policy_tampering_does_not_reseal(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'ethics_policy.py';source.write_text("POLICY_TEXT = 'changed'\n")
            before=source.read_bytes()
            with patch.object(policy,'__file__',str(source)):
                with self.assertRaises(policy.PolicyIntegrityError):policy.verify_release_policy()
            self.assertEqual(source.read_bytes(),before)

    def test_direct_protected_source_write_rejected_but_other_code_allowed(self):
        with self.assertRaises(policy.PolicyIntegrityError):policy.assert_mutable_path(policy.APP_ROOT/'ethics_policy.py')
        self.assertEqual(policy.assert_mutable_path(policy.APP_ROOT/'agent_core.py'),policy.APP_ROOT/'agent_core.py')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            self.assertEqual(policy.assert_mutable_path(root/'agent_core.py'),(root/'agent_core.py').resolve())
            for name in ('studio.py','ethics_policy.py','agent_core.py'):(root/name).write_text('# fixture')
            with self.assertRaises(policy.PolicyIntegrityError):policy.assert_mutable_path(root/'ethics_policy.py')
            self.assertEqual(policy.assert_mutable_path(root/'my_game.py'),(root/'my_game.py').resolve())

    def test_candidate_enforcer_deletion_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('studio.py','ethics_policy.py','agent_core.py'):(root/name).write_text('# fixture')
            changes=[{'path':'ethics_policy.py','status':'deleted'},{'path':'game.py','status':'modified'}]
            self.assertEqual(policy.rejected_candidate_changes(root,changes),['ethics_policy.py'])

    def test_desktop_write_blocks_policy_without_changes_but_permits_app_code(self):
        from desktop_tools import DesktopTools
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()
            for name in ('studio.py','ethics_policy.py','agent_core.py'):(root/name).write_text('# fixture')
            tools=DesktopTools(True,threading.Event(),root/'checkpoints');tools.root=root
            with self.assertRaises(policy.PolicyIntegrityError):
                tools.execute('desktop_write_file',{'path':'ethics_policy.py','content':'changed'})
            self.assertEqual((root/'ethics_policy.py').read_text(),'# fixture')
            self.assertEqual(tools.changes,[]);self.assertFalse((root/'checkpoints').exists())
            tools.execute('desktop_write_file',{'path':'agent_core.py','content':'# valid app improvement'})
            self.assertEqual((root/'agent_core.py').read_text(),'# valid app improvement')

    def test_core_fails_before_inference_on_integrity_error(self):
        import agent_core as core
        with tempfile.TemporaryDirectory() as folder,patch.object(core,'verify_release_policy',side_effect=policy.PolicyIntegrityError('tampered')),patch.object(core,'stream_chat') as stream:
            with self.assertRaises(policy.PolicyIntegrityError):
                core.run_agent('fixture','fixture',[{'role':'user','content':'Improve'}],folder,True,threading.Event(),lambda *args:None)
            stream.assert_not_called()

    def test_core_integrity_failure_after_tool_stops_remaining_batch(self):
        import agent_core as core
        response={'message':{'tool_calls':[{'function':{'name':'list_files','arguments':{}}},{'function':{'name':'project_info','arguments':{}}}]},'done':True}
        with tempfile.TemporaryDirectory() as folder,patch.object(core,'verify_release_policy',side_effect=[{}, {}, policy.PolicyIntegrityError('changed after tool')]),patch.object(core,'stream_chat',return_value=iter([response])),patch.object(core,'model_supports_vision',return_value=False),patch.object(core,'AUTO_CONTEXT',False),patch.object(core.ProjectTools,'execute',return_value='[]') as execute:
            with self.assertRaises(policy.PolicyIntegrityError):
                core.run_agent('fixture','fixture',[{'role':'user','content':'Inspect'}],folder,True,threading.Event(),lambda *args:None)
            self.assertEqual(execute.call_count,1)

    def test_skynet_reports_rejection_without_running_changed_candidate_checks(self):
        import skynet_mode
        with tempfile.TemporaryDirectory() as folder,tempfile.TemporaryDirectory() as target:
            root=Path(folder)
            for name in ('studio.py','ethics_policy.py','agent_core.py'):(root/name).write_text('# fixture')
            def change(*args,**kwargs):
                (Path(args[3])/'ethics_policy.py').write_text('# altered')
            with patch.object(skynet_mode,'_create_candidate',return_value=Path(target)),patch.object(skynet_mode,'_run_agent',side_effect=change),patch.object(skynet_mode.ProjectTools,'execute') as execute:
                result=skynet_mode.run_improvement('fixture','fixture',root,'Improve app',threading.Event(),lambda *args:None)
            self.assertEqual(execute.call_count,1)  # Baseline only; no altered candidate check.
            self.assertEqual(result['policy_status'],'rejected')
            self.assertEqual(result['rejected_policy_changes'],['ethics_policy.py'])
            self.assertEqual(result['iterations'][0]['checks']['status'],'blocked')

    def test_skynet_checks_cannot_change_policy_and_receive_clean_report(self):
        import skynet_mode
        with tempfile.TemporaryDirectory() as folder,tempfile.TemporaryDirectory() as target:
            root=Path(folder)
            for name in ('studio.py','ethics_policy.py','agent_core.py'):(root/name).write_text('# fixture')
            def change(*args,**kwargs):(Path(args[3])/'studio.py').write_text('# improvement')
            def checks(*args,**kwargs):
                (Path(target)/'ethics_policy.py').write_text('# altered by test command')
                return 'Exit 0\nPassed fixture checks'
            with patch.object(skynet_mode,'_create_candidate',return_value=Path(target)),patch.object(skynet_mode,'_run_agent',side_effect=change),patch.object(skynet_mode.ProjectTools,'execute',side_effect=checks):
                result=skynet_mode.run_improvement('fixture','fixture',root,'Improve app',threading.Event(),lambda *args:None)
            self.assertEqual(result['policy_status'],'rejected')
            self.assertEqual(result['iterations'][0]['checks']['status'],'blocked')


if __name__=='__main__':unittest.main()
