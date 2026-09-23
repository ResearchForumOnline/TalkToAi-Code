"""Isolated desktop regression checks: no real profiles, model calls or SSH."""
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
import studio
from session_store import load_tasks


class WorkspaceUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.stack=ExitStack();folder=self.stack.enter_context(tempfile.TemporaryDirectory());self.root=Path(folder)
        for name,value in [('HOME',self.root),('STATE',self.root),('SESSION',self.root/'studio.json'),('CONNECTIONS',self.root/'connections.json'),('PROVIDERS',self.root/'providers.json')]:
            self.stack.enter_context(patch.object(studio,name,value))
        self.stack.enter_context(patch.object(studio.Studio,'health'))
        self.stack.enter_context(patch.object(studio.Studio,'install_tray'))
        self.window=studio.Studio()

    def tearDown(self):
        self.window.allow_quit=True;self.window.close();self.window.deleteLater();self.app.processEvents();self.stack.close()

    def test_new_and_branch_select_correct_identity_with_pinned_chat(self):
        w=self.window;old=w.task['id'];w.toggle_pin_task()
        w.new_task();new=w.task['id'];self.assertNotEqual(new,old)
        self.assertEqual(w.task_list.item(0).data(Qt.UserRole),old)
        w.prompt.setPlainText('unfinished task');w.fork_task()
        self.assertNotEqual(w.task['id'],new);self.assertNotEqual(w.task['id'],old)
        self.assertEqual(w.task['draft'],'unfinished task');self.assertFalse(w.task['pinned'])

    def test_pin_refresh_preserves_unsaved_file_editor(self):
        w=self.window;w.current_file='player.gd';w.editor.setPlainText('unsaved editor text')
        w.toggle_pin_task()
        self.assertEqual(w.current_file,'player.gd');self.assertEqual(w.editor.toPlainText(),'unsaved editor text')

    def test_archive_restore_and_search_content(self):
        w=self.window;chat=w.task['id'];w.task['messages'].append({'role':'assistant','content':'Unique dragon inventory'})
        w.task_search.setText('dragon');self.assertFalse(w.task_list.item(0).isHidden())
        w.task_search.setText('absent');self.assertTrue(w.task_list.item(0).isHidden())
        w.task_search.clear();w.toggle_archive_task();self.assertEqual(w.task_list.count(),0)
        self.assertEqual(len(w.tasks),1)
        w.task_view.setCurrentIndex(1);self.assertEqual(w.task_list.count(),1)
        w.select_task_by_id(chat);w.toggle_archive_task();self.assertEqual(w.task_list.count(),0)
        w.task_view.setCurrentIndex(0);self.assertEqual(w.task_list.count(),1)
        self.assertFalse(load_tasks(studio.SESSION)[0][0]['archived'])

    def test_new_task_leaves_archive_and_clears_search(self):
        w=self.window;w.toggle_archive_task();w.task_view.setCurrentIndex(1);w.task_search.setText('no match')
        w.new_task()
        self.assertFalse(w.task['archived']);self.assertEqual(w.task_view.currentIndex(),0)
        self.assertEqual(w.task_search.text(),'');self.assertEqual(w.task_list.count(),1)

    def test_starters_do_not_execute_or_overwrite_drafts(self):
        w=self.window
        with patch.object(w,'send') as send,patch.object(studio,'run_agent') as agent:
            w.use_starter('Build a game feature');first=w.prompt.toPlainText()
            self.assertIn('[describe the feature]',first)
            w.use_starter('Inspect an SSH project');self.assertEqual(w.prompt.toPlainText(),first)
            send.assert_not_called();agent.assert_not_called()
        w.autosave_draft();self.assertEqual(load_tasks(studio.SESSION)[0][0]['draft'],first)

    def test_busy_blocks_archive_and_starters(self):
        w=self.window;w.set_busy(True);w.toggle_archive_task();w.use_starter('Improve this project')
        self.assertFalse(w.task['archived']);self.assertEqual(w.prompt.toPlainText(),'')
        self.assertFalse(w.task_view.isEnabled());w.set_busy(False)

    def test_model_choice_uses_user_settings_location(self):
        w=self.window
        with patch('routing.inventory',return_value=['fixture']),patch.object(studio.QInputDialog,'getItem',return_value=('fixture',True)),patch.object(w,'write_config') as save:
            w.select_installed_model();save.assert_called_once();self.assertEqual(w.config['local_model'],'fixture')

    def test_example_game_opens_writable_copy_without_a_model(self):
        w=self.window
        with patch.object(studio,'run_agent') as agent:
            w.prompt.setPlainText('open score arena');w.send();agent.assert_not_called()
        self.assertEqual(Path(w.task['project']).resolve(),(self.root/'projects/score-arena').resolve())
        self.assertTrue((Path(w.task['project'])/'project.godot').is_file())

    def test_plan_and_check_evidence_persist_and_render(self):
        w=self.window
        plan={'steps':[{'step':'Inspect project','status':'completed'},{'step':'Fix the menu','status':'in_progress'}],'explanation':'Working on the menu'}
        w.handle_event('plan',plan)
        w.handle_event('verification',{'status':'failed','summary':'The test command failed.'})
        self.assertEqual(w.plan_list.count(),2)
        self.assertIn('Done (reported)',w.plan_list.item(0).text())
        self.assertIn('failed',w.verification_summary.text())
        saved=load_tasks(studio.SESSION)[0][0]
        self.assertEqual(saved['plan'],plan);self.assertEqual(saved['verification']['status'],'failed')

    def test_continue_button_preserves_an_existing_draft(self):
        w=self.window;w.prompt.setPlainText('Keep this draft')
        with patch.object(w,'quick_command') as execute:
            w.continue_task();execute.assert_not_called()
        self.assertEqual(w.prompt.toPlainText(),'Keep this draft')

    def test_job_events_update_one_row_and_persist_terminal_status(self):
        w=self.window
        record={'id':'example','state':'running','command':['python','-m','unittest'],'output':'Starting','seconds':1,'exit_code':None}
        w.handle_event('job',record);w.handle_event('job',dict(record,output='Progress'))
        self.assertEqual(w.jobs_list.count(),1);self.assertEqual(w.job_output.toPlainText(),'Progress')
        w.handle_event('job',dict(record,state='failed',exit_code=2,output='Build failed'))
        self.assertIn('failed',w.job_summary.text());self.assertFalse(w.cancel_job_button.isEnabled())
        self.assertEqual(load_tasks(studio.SESSION)[0][0]['jobs'][0]['exit_code'],2)

    def test_registered_output_deduplicates_and_reveals_without_execution(self):
        w=self.window;path=self.root/'build.exe';path.write_bytes(b'not executable')
        record={'artifact':str(path),'type':'file','title':'Build','bytes':14,'sha256':'a'*64}
        w.handle_event('artifact',record);w.handle_event('artifact',dict(record,title='Updated build'))
        self.assertEqual(w.artifacts.count(),1)
        item=w.artifacts.item(0);w.artifacts.setCurrentItem(item)
        self.assertIn('SHA-256',w.artifact_details.text())
        with patch.object(studio.QDesktopServices,'openUrl') as opened:
            w.open_artifact(item)
            self.assertEqual(Path(opened.call_args.args[0].toLocalFile()).resolve(),self.root.resolve())

    def test_job_records_remain_scoped_to_their_chat(self):
        w=self.window;old=w.task['id']
        w.handle_event('job',{'id':'old-job','state':'completed','command':['python'],'exit_code':0})
        w.new_task();self.assertEqual(w.jobs_list.count(),0)
        w.select_task_by_id(old);self.assertEqual(w.jobs_list.count(),1)

    def test_late_job_event_does_not_attach_to_new_conversation(self):
        w=self.window;old=w.task['id'];w.new_task()
        w.handle_event('job',{'task_id':old,'id':'late-job','state':'completed','command':['python'],'exit_code':0})
        self.assertEqual(w.jobs_list.count(),0)
        w.select_task_by_id(old);self.assertEqual(w.jobs_list.count(),1)

    def test_openai_profile_is_explicit_and_does_not_change_default_route(self):
        from provider_dialog import ProviderDialog
        w=self.window;dialog=ProviderDialog(w,self.root/'provider-fixture.json')
        try:
            dialog.preset.setCurrentIndex(1)
            self.assertEqual(dialog.url.text(),'https://api.openai.com/v1')
            self.assertEqual(dialog.env.text(),'OPENAI_API_KEY')
            dialog.model.setEditText('user-chosen-model')
            dialog.save_profile()
            self.assertEqual(w.route.currentIndex(),0);self.assertEqual(w.config['preferred_route'],'auto')
            self.assertTrue(w.active_provider().is_openai)
            dialog.use_profile();self.assertEqual(w.route.currentIndex(),4)
        finally:dialog.close();dialog.deleteLater()

    def test_api_token_usage_is_reported_without_claiming_billing(self):
        w=self.window
        w.handle_event('metrics',{'api_usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}})
        w.handle_event('metrics',{'api_usage':{'prompt_tokens':4,'completion_tokens':6,'total_tokens':10}})
        self.assertEqual(w.task['api_usage']['total_tokens'],25)
        self.assertIn('not a billing total',w.performance_label.text())


if __name__=='__main__':unittest.main()
