"""Isolated desktop regression checks: no real profiles, model calls or SSH."""
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QPushButton, QComboBox, QDialog, QLineEdit
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

    def test_export_includes_saved_work_checkpoint(self):
        w=self.window;w.task['project']=str(self.root)
        w.task['goal_checkpoint']={'state':'paused','steps':32,'verification':{'status':'failed'}}
        w.export_task()
        text=(self.root/'.talktoai-code/reports'/(w.task['id']+'.md')).read_text(encoding='utf-8')
        self.assertIn('Work checkpoint',text)
        self.assertIn('"state": "paused"',text)
        self.assertIn('"status": "failed"',text)

    def test_diagnostics_event_displays_plain_text(self):
        w=self.window;w.handle_event('runtime_diagnostics','AMD: missing model <literal>')
        self.assertEqual(w.output.toPlainText(),'AMD: missing model <literal>')
        self.assertEqual(w.right.currentIndex(),2)

    def test_targeting_selects_native_game_and_persists_amd_without_escalating_plan(self):
        w=self.window;w.task['project']=str(self.root)
        native=self.root/'blacksite_nightfall'/'native';native.mkdir(parents=True)
        (native/'project.godot').write_text('[application]\nconfig/name="NIGHTFALL"\n')
        w.mode.setCurrentText('Plan')
        with patch.object(w,'write_config') as save:
            self.assertTrue(w.prepare_task_target('use AMD always, game: NIGHTFALL. improve it'))
            save.assert_called_once()
        self.assertEqual(Path(w.task['project']),native.resolve())
        self.assertEqual(w.route.currentIndex(),2);self.assertEqual(w.config['preferred_route'],'server')
        self.assertEqual(w.mode.currentText(),'Plan');self.assertIn('read-only',w.mode_hint.text())
        self.assertEqual(w.step_budget.currentData(),32)

    def test_context_setting_persists_and_cancel_keeps_saved_value(self):
        import json
        w=self.window
        godot=self.root/'godot';godot.write_text('fixture')
        self.assertEqual(w.model_performance(),{'num_ctx':8192})
        def save(dialog):
            choice=dialog.findChild(QComboBox,'model_context_window')
            self.assertEqual(choice.currentData(),8192)
            choice.setCurrentIndex(choice.findData(16384))
            dialog.findChild(QLineEdit,'godot_executable').setText(str(godot.resolve()))
            dialog.findChild(QLineEdit,'server_label').setText('My GPU server')
            dialog.findChild(QLineEdit,'server_model').setText('team/server-coder:custom')
            dialog.findChild(QLineEdit,'local_model').setText('local-coder:small')
            dialog.findChild(QLineEdit,'local_large_model').setText('local-coder:large')
            return QDialog.Accepted
        with patch.object(studio.QDialog,'exec',save),patch.object(w,'set_start_with_windows'),patch('search_provider.configured',return_value=False):
            w.settings()
        self.assertEqual(w.model_performance(),{'num_ctx':16384})
        self.assertEqual(json.loads((self.root/'config.json').read_text())['num_ctx'],16384)
        self.assertEqual(json.loads((self.root/'config.json').read_text())['godot_executable'],str(godot.resolve()))
        self.assertEqual(json.loads((self.root/'config.json').read_text())['server_label'],'My GPU server')
        self.assertTrue(w.route.itemText(2).startswith('My GPU server · '))
        self.assertEqual(w.route.itemText(2),'My GPU server · team/server-coder:custom')
        self.assertEqual(w.route.itemText(1),'Local · local-coder:small')
        def cancel(dialog):
            choice=dialog.findChild(QComboBox,'model_context_window')
            self.assertEqual(choice.currentData(),16384)
            choice.setCurrentIndex(choice.findData(32768))
            self.assertEqual(dialog.findChild(QLineEdit,'godot_executable').text(),str(godot.resolve()))
            self.assertEqual(dialog.findChild(QLineEdit,'server_label').text(),'My GPU server')
            dialog.findChild(QLineEdit,'server_label').setText('Cancelled label')
            return QDialog.Rejected
        with patch.object(studio.QDialog,'exec',cancel),patch('search_provider.configured',return_value=False):w.settings()
        self.assertEqual(w.model_performance(),{'num_ctx':16384})
        self.assertEqual(w.server_name(),'My GPU server')

    def test_generic_server_labels_and_custom_model_names(self):
        w=self.window
        self.assertEqual(w.server_name(),'Server')
        self.assertTrue(w.route.itemText(2).startswith('Server · '))
        self.assertNotIn('AMD',w.route.itemText(0))
        w.config.update(server_label='AMD',server_model='custom-coder:latest',local_model='small:custom',local_large_model='large:custom')
        w.refresh_route_labels()
        self.assertEqual(w.route.itemText(2),'AMD · custom-coder:latest')
        self.assertEqual(w.route.itemText(1),'Local · small:custom')
        self.assertEqual(w.route.itemText(3),'Local large · large:custom')
        self.assertEqual(w.reconnect_button.text(),'Reconnect AMD model')
        w.handle_event('route',{'route':'server','model':'custom-coder:latest','reason':'explicit'})
        self.assertTrue(w.route_description.startswith('AMD · '))

    def test_use_server_always_selects_only_server_inference(self):
        w=self.window;w.task['project']=str(self.root)
        with patch.object(w,'write_config') as save:
            self.assertTrue(w.prepare_task_target('use server always, improve this project'))
            save.assert_called_once()
        self.assertEqual(w.route.currentIndex(),2)
        self.assertEqual(w.config['preferred_route'],'server')

    def test_model_comparison_report_preserves_route_and_reports_missing_fixtures(self):
        w=self.window;w.route.setCurrentIndex(2)
        data={'cancelled':False,'note':'Two short fixtures; route unchanged.','results':[
            {'route':'server','model':'coder:test','elapsed_seconds':3,'fixtures':[
                {'kind':'code','passed':True,'elapsed_seconds':1},
                {'kind':'tools','passed':False,'elapsed_seconds':2}]},
            {'route':'local_large','model':'local:test','fixtures':[],'error':'Not installed'}]}
        w.handle_event('model_comparison',data)
        self.assertEqual(w.route.currentIndex(),2)
        self.assertEqual(w.task['model_comparison'],data)
        text=w.task['messages'][-1]['content']
        self.assertIn('Pass · 1s',text);self.assertIn('Fail · 2s',text)
        self.assertIn('Not completed',text);self.assertIn('Not installed',text)

    def test_model_comparison_is_background_cancelable_and_does_not_choose_route(self):
        w=self.window;w.route.setCurrentIndex(1)
        with patch('studio.threading.Thread') as worker:
            w.compare_installed_models()
            worker.assert_called_once();worker.return_value.start.assert_called_once()
        self.assertTrue(w.busy);self.assertTrue(w.stop.isEnabled());self.assertFalse(w.cancel.is_set())
        w.stop_task();self.assertTrue(w.cancel.is_set());self.assertEqual(w.route.currentIndex(),1)
        w.set_busy(False)

    def test_keep_going_is_opt_in_and_persisted_per_conversation(self):
        w=self.window;first=w.task['id']
        self.assertFalse(w.keep_going.isChecked())
        w.keep_going.setChecked(True)
        saved=next(task for task in load_tasks(studio.SESSION)[0] if task['id']==first)
        self.assertTrue(saved['keep_going'])
        w.new_task();self.assertFalse(w.keep_going.isChecked())
        w.select_task_by_id(first);self.assertTrue(w.keep_going.isChecked())
        w.config['keep_going']=True;w.new_task();self.assertTrue(w.keep_going.isChecked())

    def test_keep_going_flag_and_budget_reach_agent(self):
        w=self.window;w.task['project']=str(self.root);w.route.setCurrentIndex(1)
        w.keep_going.setChecked(True);w.step_budget.setCurrentIndex(2)
        w.prompt.setPlainText('Improve this project')
        selected={'route':'local','url':'http://127.0.0.1:11434','model':'fixture','reason':'test'}
        with patch('studio.threading.Thread') as worker,patch('studio.ensure_local_model'),patch('studio.choose_route',return_value=selected),patch('studio.run_agent') as run:
            w.send();worker.call_args.kwargs['target']()
            self.assertTrue(run.call_args.kwargs['keep_going'])
            self.assertEqual(run.call_args.kwargs['rounds'],64)

    def test_goal_checkpoint_persists_progress_in_steps(self):
        w=self.window;data={'pass':2,'total_passes':3,'steps':32,'changes':4,'state':'continuing'}
        w.handle_event('goal_checkpoint',data)
        self.assertEqual(w.task['goal_checkpoint'],data)
        self.assertIn('Work pass 2/3',w.plan_summary.text())
        self.assertIn('32 steps',w.status.text())
        self.assertEqual(load_tasks(studio.SESSION)[0][0]['goal_checkpoint'],data)

    def test_continue_recovers_original_explicit_project_from_user_history(self):
        w=self.window
        wrong=self.root/'airr';wrong.mkdir();w.task['project']=str(wrong)
        native=self.root/'blacksite_nightfall'/'native';native.mkdir(parents=True)
        (native/'project.godot').write_text('[application]\nconfig/name="NIGHTFALL"\n')
        w.task['messages']=[{'role':'user','content':f'{self.root} game: NIGHTFALL. Improve the game.'},
                            {'role':'assistant','content':'I found AIRR cloud'},
                            {'role':'user','content':'continue'}]
        self.assertTrue(w.prepare_task_target('keep going'))
        self.assertEqual(Path(w.task['project']),native.resolve())

    def test_invalid_context_setting_falls_back_to_8k(self):
        self.window.config['num_ctx']='999999'
        self.assertEqual(self.window.model_performance(),{'num_ctx':8192})

    def test_invalid_model_id_does_not_save_settings(self):
        w=self.window;original=dict(w.config)
        def save(dialog):
            dialog.findChild(QLineEdit,'server_model').setText('')
            return QDialog.Accepted
        with patch.object(studio.QDialog,'exec',save),patch('search_provider.configured',return_value=False),patch.object(w,'error') as error:
            w.settings();error.assert_called_once()
        self.assertEqual(w.config,original)

    def test_project_switch_preserves_unsaved_editor_by_stopping(self):
        w=self.window;w.task['project']=str(self.root)
        new=self.root/'other';new.mkdir()
        w.current_file='main.py';w.editor.setPlainText('original');w.editor.insertPlainText('unsaved')
        with patch.object(w,'error') as error:
            self.assertFalse(w.prepare_task_target(f'Improve "{new}"'))
            error.assert_called_once()
        self.assertEqual(w.task['project'],str(self.root));self.assertIn('unsaved',w.editor.toPlainText())

    def test_project_instructions_cancel_preserves_existing_bytes(self):
        w=self.window;w.task['project']=str(self.root)
        path=self.root/'AGENTS.md';original=b'# Existing\r\nUse Godot.\r\n';path.write_bytes(original)
        def cancel(dialog):
            editor=dialog.findChild(QPlainTextEdit)
            self.assertIn('Use Godot.',editor.toPlainText());editor.setPlainText('changed');dialog.reject()
        with patch.object(studio.QDialog,'exec',cancel):w.instructions_dialog()
        self.assertEqual(path.read_bytes(),original)

    def test_project_instructions_save_is_checkpointed(self):
        w=self.window;w.task['project']=str(self.root)
        path=self.root/'AGENTS.md';path.write_text('# Old\n')
        def save(dialog):
            dialog.findChild(QPlainTextEdit).setPlainText('# New instructions\nUse Godot checks.\n')
            next(button for button in dialog.findChildren(QPushButton) if button.text()=='Save instructions').click()
        with patch.object(studio.QDialog,'exec',save):w.instructions_dialog()
        self.assertIn('Use Godot checks.',path.read_text())
        self.assertTrue(w.task['changes'])

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
