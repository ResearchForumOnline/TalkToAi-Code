"""Isolated desktop regression checks: no real profiles, model calls or SSH."""
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QPushButton, QComboBox, QDialog, QLineEdit, QLabel
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
        self.escape_hook=self.stack.enter_context(patch('studio.EscapeCancel')).return_value
        self.escape_hook.arm.return_value=False
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

    def test_source_changes_are_distinct_from_restorable_editor_changes(self):
        w=self.window
        before=list(w.task['changes'])
        w.handle_event('workspace_changes',{'count':2,'paths':['main.gd','player.gd'],'complete':False,'reason':'Partial project source scan'})
        self.assertEqual(w.task['changes'],before)
        self.assertEqual(w.task['workspace_changes']['count'],2)
        self.assertIn('partial scan',w.observed_changes.text())
        self.assertIn('Git or manual review',w.observed_changes.text())

    def test_active_run_has_immediate_receipt_and_readable_tool_output(self):
        w=self.window
        self.assertEqual(w.output.lineWrapMode(),QPlainTextEdit.NoWrap)
        self.assertFalse(w.run_card.isVisible())
        w.set_busy(True)
        self.assertFalse(w.run_card.isHidden())
        self.assertEqual(w.run_card_title.text(),'Request received')
        self.assertIn('model has not replied yet',w.run_card_detail.text())
        w.handle_event('model_wait',{'step':2,'elapsed_seconds':16})
        self.assertEqual(w.run_card_title.text(),'Waiting for model')
        self.assertIn('step 2',w.status.text())
        w.set_busy(False)
        self.assertTrue(w.run_card.isHidden())

    def test_model_activity_requires_opt_in_and_provider_output(self):
        w=self.window
        activity={'source':'provider','active':True,'characters':16,'excerpt':'Reviewing files.'}
        w.handle_event('reasoning',activity)
        self.assertEqual(w.model_activity.toPlainText(),'')
        with patch.object(w,'write_config'):
            w.model_activity_action.trigger()
        self.assertTrue(w.config['show_model_activity'])
        w.handle_event('reasoning',activity)
        self.assertIn('Reviewing files.',w.model_activity.toPlainText())
        self.assertEqual(w.task.get('messages',[]),[])

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
        self.assertEqual(w.model_performance(),{'num_ctx':8192,'show_thinking':False,'work_session_minutes':120})
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
        self.assertEqual(w.model_performance(),{'num_ctx':16384,'show_thinking':False,'work_session_minutes':120})
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
        self.assertEqual(w.model_performance(),{'num_ctx':16384,'show_thinking':False,'work_session_minutes':120})
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

    def test_keep_going_defaults_on_and_preserves_conversation_choice(self):
        w=self.window;first=w.task['id']
        self.assertTrue(w.keep_going.isChecked())
        w.keep_going.setChecked(False)
        saved=next(task for task in load_tasks(studio.SESSION)[0] if task['id']==first)
        self.assertFalse(saved['keep_going'])
        w.new_task();self.assertTrue(w.keep_going.isChecked())
        w.select_task_by_id(first);self.assertFalse(w.keep_going.isChecked())
        w.config['keep_going']=False;w.new_task();self.assertFalse(w.keep_going.isChecked())

    def test_keep_going_flag_and_budget_reach_agent(self):
        w=self.window;w.task['project']=str(self.root);w.route.setCurrentIndex(1)
        w.keep_going.setChecked(True);w.step_budget.setCurrentIndex(2)
        w.handle_event('task_goal',{'objective':'Improve this project','criteria':[{'text':'Run project checks'}],'next_action':'Inspect files'})
        w.prompt.setPlainText('Improve this project')
        selected={'route':'local','url':'http://127.0.0.1:11434','model':'fixture','reason':'test'}
        with patch('studio.threading.Thread') as worker,patch('studio.ensure_local_model'),patch('studio.choose_route',return_value=selected),patch('studio.run_agent') as run:
            w.send();worker.call_args.kwargs['target']()
            self.assertTrue(run.call_args.kwargs['keep_going'])
            self.assertEqual(run.call_args.kwargs['rounds'],64)
            self.assertEqual(run.call_args.kwargs['task_goal']['objective'],'Improve this project')
            self.assertIsNot(run.call_args.kwargs['task_goal'],w.task['task_goal'])

    def test_task_goal_event_is_scoped_saved_and_exported_as_self_reported(self):
        w=self.window;w.task['project']=str(self.root);first=w.task['id']
        goal={'objective':'Playable pause menu','criteria':[{'text':'Menu opens','status':'met','evidence':'playtest.log: PASS'}],'next_action':'Test keyboard navigation'}
        w.handle_event('task_goal',goal)
        self.assertIn('self-reported',w.goal_summary.text());self.assertIn('playtest.log',w.goal_list.item(0).text())
        self.assertEqual(load_tasks(studio.SESSION)[0][0]['task_goal']['objective'],goal['objective'])
        w.export_task();report=self.root/'.talktoai-code'/'reports'/(first+'.md')
        self.assertIn('Saved task goal (self-reported)',report.read_text())
        w.new_task();self.assertIsNone(w.current_task_goal());self.assertEqual(w.goal_list.count(),0)
        w.select_task_by_id(first);self.assertEqual(w.current_task_goal()['objective'],goal['objective'])

    def test_goal_editor_preserves_unchanged_criteria_and_resets_changed_objective(self):
        w=self.window
        w.handle_event('task_goal',{'objective':'Playable pause menu','criteria':[{'text':'Menu opens','status':'met','evidence':'playtest.log'}]})
        def unchanged(dialog):
            dialog.findChild(QLineEdit,'goal_next_action').setText('Check navigation')
            next(button for button in dialog.findChildren(QPushButton) if button.text()=='Save goal').click()
        with patch.object(studio.QDialog,'exec',unchanged):w.task_goal_dialog()
        self.assertEqual(w.current_task_goal()['criteria'][0]['status'],'met')
        def changed(dialog):
            dialog.findChild(QPlainTextEdit,'goal_objective').setPlainText('Playable inventory')
            next(button for button in dialog.findChildren(QPushButton) if button.text()=='Save goal').click()
        with patch.object(studio.QDialog,'exec',changed):w.task_goal_dialog()
        self.assertEqual(w.current_task_goal()['criteria'][0]['status'],'pending')
        self.assertEqual(w.current_task_goal()['criteria'][0]['evidence'],'')

    def test_goal_editor_cancel_and_invalid_event_preserve_goal(self):
        w=self.window;w.handle_event('task_goal',{'objective':'Pause menu','criteria':[{'text':'Menu opens'}]})
        previous=dict(w.current_task_goal())
        def cancel(dialog):
            dialog.findChild(QPlainTextEdit,'goal_objective').setPlainText('Discarded edit');dialog.reject()
        with patch.object(studio.QDialog,'exec',cancel):w.task_goal_dialog()
        w.handle_event('task_goal',{'objective':'Bad','criteria':[]})
        self.assertEqual(w.current_task_goal(),previous)

    def test_manual_goal_change_and_clear_remove_stale_checkpoint_but_identical_save_keeps_it(self):
        w=self.window
        w.handle_event('task_goal',{'objective':'Pause menu','criteria':[{'text':'Menu opens'}]})
        checkpoint={'pass':1,'total_passes':3,'steps':12,'state':'completed'}
        w.handle_event('goal_checkpoint',checkpoint)
        def identical(dialog):
            next(button for button in dialog.findChildren(QPushButton) if button.text()=='Save goal').click()
        with patch.object(studio.QDialog,'exec',identical):w.task_goal_dialog()
        self.assertEqual(w.task['goal_checkpoint'],checkpoint)
        def change(dialog):
            dialog.findChild(QPlainTextEdit,'goal_objective').setPlainText('Inventory menu')
            next(button for button in dialog.findChildren(QPushButton) if button.text()=='Save goal').click()
        with patch.object(studio.QDialog,'exec',change):w.task_goal_dialog()
        self.assertNotIn('goal_checkpoint',w.task)
        self.assertNotIn('Work pass',w.plan_summary.text())
        w.handle_event('goal_checkpoint',checkpoint)
        def clear(dialog):
            next(button for button in dialog.findChildren(QPushButton) if button.text()=='Clear goal').click()
        with patch.object(studio.QDialog,'exec',clear):w.task_goal_dialog()
        self.assertNotIn('goal_checkpoint',w.task);self.assertIsNone(w.current_task_goal())
        self.assertNotIn('goal_checkpoint',load_tasks(studio.SESSION)[0][0])

    def test_empty_tool_call_messages_do_not_create_blank_chat_rows(self):
        w=self.window
        w.task['messages']=[{'role':'user','content':'Inspect game'},
            {'role':'assistant','content':'   \n ','tool_calls':[{'function':{'name':'project_info','arguments':{}}}]},
            {'role':'tool','content':'result'}, {'role':'assistant','content':'Game located.'}]
        with patch.object(w.transcript,'setMarkdown') as display:w.render()
        markdown=display.call_args.args[0]
        self.assertEqual(markdown.count('## TalkToAi Code'),1)
        self.assertEqual(markdown.count('\n\n---\n\n'),1)

    def test_historical_and_streamed_protocol_is_hidden_without_destroying_evidence(self):
        w=self.window;raw='<tool_call>\n<function=read_file>\n<parameter=path>main.gd</parameter>\n</function>\n</tool_call>'
        w.task['messages']=[{'role':'assistant','content':raw}, {'role':'assistant','content':'Task error: Model failed'}]
        w.partial='Inspecting the game.\n'+raw
        with patch.object(w.transcript,'setMarkdown') as display:w.render()
        markdown=display.call_args.args[0]
        self.assertNotIn('<function=',markdown);self.assertNotIn('<tool_call>',markdown)
        self.assertIn('Inspecting the game.',markdown);self.assertIn('Task error: Model failed',markdown)
        self.assertIn('does not establish execution',markdown)
        self.assertEqual(w.task['messages'][0]['content'],raw)
        example='Example:\n```xml\n<tool_call>example</tool_call>\n```'
        self.assertEqual(studio.conversation_prose(example),(example,False))

    def test_new_run_clears_stale_tools_but_retains_history_and_new_failures(self):
        w=self.window;w.task['activity']=['Old failure'];w.output.setPlainText('Old failure')
        w.status.setText('Request failed');w.task['last_metrics']={'seconds':9}
        w.set_busy(True)
        self.assertEqual(w.output.toPlainText(),'');self.assertEqual(w.task['activity'],['Old failure'])
        self.assertEqual(w.task['activity_run_start'],1);self.assertNotIn('last_metrics',w.task)
        self.assertNotIn('failed',w.status.text())
        w.handle_event('status','Retrying model tool response · no actions executed')
        self.assertIn('Retrying',w.status.text())
        w.handle_event('error','Current run genuinely failed')
        self.assertIn('Current run genuinely failed',w.output.toPlainText())
        self.assertIn('Current run genuinely failed',w.task['activity'][-1])
        w.set_busy(False)
        w.select_task_by_id(w.task['id'])
        self.assertNotIn('Old failure',w.output.toPlainText())
        self.assertIn('Current run genuinely failed',w.output.toPlainText())

    def test_control_overlay_starts_only_for_control_and_never_exposes_arguments(self):
        w=self.window;w.set_busy(True)
        w.handle_event('tool',{'name':'read_file','args':{'path':'private-user-text'}})
        self.assertFalse(w.control_active);self.assertIsNone(w.control_overlay)
        w.handle_event('tool',{'name':'browser','args':{'action':'fill','target':'private-user-url','value':'secret-typed-text'}})
        overlay=w.control_overlay
        self.assertTrue(w.control_active);self.assertTrue(overlay.isVisible())
        self.assertTrue(overlay.windowFlags() & Qt.WindowDoesNotAcceptFocus)
        self.assertTrue(overlay.windowFlags() & Qt.WindowStaysOnTopHint)
        self.assertTrue(overlay.testAttribute(Qt.WA_ShowWithoutActivating))
        shown=overlay.headline.text()+overlay.detail.text()+w.control_banner.text()
        self.assertNotIn('private-user',shown);self.assertNotIn('secret-typed',shown)
        self.assertIn('working in its browser',shown)
        self.assertIn('Esc in TalkToAi',shown);self.assertTrue(w.escape_shortcut.isEnabled())
        with patch.object(overlay,'show') as show:
            w.handle_event('tool',{'name':'computer','args':{'action':'click'}});show.assert_not_called()
        self.assertIn('using your computer',overlay.headline.text())
        overlay.stop_button.click()
        self.assertTrue(w.cancel.is_set());self.assertIn('stopping',overlay.headline.text())
        self.assertTrue(overlay.isVisible());self.assertFalse(overlay.stop_button.isEnabled())
        w.set_busy(False);self.assertFalse(overlay.isVisible());self.assertFalse(w.escape_shortcut.isEnabled())

    def test_global_escape_is_task_bound_and_does_not_restart_queued_steering(self):
        import time
        w=self.window;self.escape_hook.arm.return_value=True
        w.started_at=time.monotonic();w.route_description='Control fixture';w.set_busy(True)
        token=w.control_cancel_token
        w.handle_event('tool',{'name':'computer','args':{'action':'inspect'}})
        self.assertIn('Esc to cancel',w.control_overlay.detail.text())
        self.assertFalse(w.escape_shortcut.isEnabled())
        w.pending_prompt='Continue with another action'
        w.handle_event('control_cancel',object());self.assertFalse(w.cancel.is_set())
        w.handle_event('control_cancel',token)
        self.assertEqual(w.pending_prompt,'');self.assertIn('Continue with another action',w.prompt.toPlainText())
        with patch.object(w,'_send_queued') as resume:w.handle_event('finished',None);resume.assert_not_called()
        self.assertFalse(w.control_overlay.isVisible());self.escape_hook.disarm.assert_called()

    def test_overlay_close_requests_stop_and_errors_hide_indicator(self):
        w=self.window;w.set_busy(True);w.handle_event('tool',{'name':'capture_screenshot','args':{}})
        overlay=w.control_overlay;overlay.close()
        self.assertTrue(w.cancel.is_set());self.assertTrue(overlay.isVisible())
        w.handle_event('error','Actual control failure')
        self.assertFalse(overlay.isVisible());self.assertIn('Request failed',w.status.text())
        w.set_busy(False)

    def test_finished_before_global_escape_ui_event_cannot_resume_steering(self):
        import time
        from types import SimpleNamespace
        w=self.window;self.escape_hook.arm.return_value=True
        w.started_at=time.monotonic();w.route_description='Control fixture';w.set_busy(True)
        w.pending_prompt='Queued steering to preserve'
        pending=[];callback=self.escape_hook.arm.call_args.args[0]
        with patch.object(w,'bus',SimpleNamespace(event=SimpleNamespace(emit=lambda *args:pending.append(args)))):callback()
        self.assertTrue(w.explicit_stop.is_set());self.assertTrue(w.cancel.is_set())
        with patch.object(w,'_send_queued') as resume:w.handle_event('finished',None);resume.assert_not_called()
        w.handle_event(*pending[0])
        self.assertEqual(w.pending_prompt,'');self.assertIn('Queued steering',w.prompt.toPlainText())

    def test_run_progress_distinguishes_model_tool_and_no_output_without_claiming_stall(self):
        w=self.window
        with patch('studio.time.monotonic',return_value=100):
            w.started_at=100;w.set_busy(True)
            w.handle_event('status','Working - step 7')
        with patch('studio.time.monotonic',return_value=200):w.tick()
        text=w.performance_label.text()
        self.assertIn('Waiting for model',text);self.assertIn('step 7',text)
        self.assertIn('No new output for 100s',text);self.assertIn('may still be running',text)
        self.assertFalse(w.cancel.is_set())
        with patch('studio.time.monotonic',return_value=201):
            w.handle_event('tool',{'name':'run_command','args':{'command':'fixture'}});w.tick()
        self.assertIn('Running project command',w.performance_label.text())
        self.assertIn('1 tools',w.performance_label.text());self.assertIn('0 editor-tool edits',w.performance_label.text())
        self.assertNotIn('No new output',w.performance_label.text())
        w.handle_event('delta','Planning a change');self.assertEqual(w.run_phase,'Model responding')
        w.set_busy(False)

    def test_completed_response_timing_details_use_only_reported_values(self):
        w=self.window
        w.handle_event('metrics',{'seconds':50,'tokens_per_second':8.29,'step':4,'load_seconds':12.5,
                                 'prompt_seconds':30,'generation_seconds':7.5,'prompt_tokens':2000})
        tooltip=w.performance_label.toolTip()
        self.assertIn('Last completed response',tooltip);self.assertIn('Model loading: 12.5s',tooltip)
        self.assertIn('Prompt processing: 30s',tooltip);self.assertIn('Token generation: 7.5s',tooltip)
        self.assertIn('Prompt tokens: 2000',tooltip);self.assertIn('not overall task throughput',tooltip)
        w.handle_event('metrics',{'seconds':5,'tokens_per_second':2,'step':5})
        self.assertNotIn('Model loading:',w.performance_label.toolTip())
        w.set_busy(True);self.assertEqual(w.performance_label.toolTip(),'');w.set_busy(False)

    def test_operating_policy_dialog_is_read_only_and_shows_verified_digest(self):
        from ethics_policy import EXPECTED_POLICY_SHA256
        def inspect(dialog):
            body=dialog.findChild(QPlainTextEdit,'operating_policy_text')
            status=dialog.findChild(QLabel,'operating_policy_status')
            self.assertTrue(body.isReadOnly());self.assertIn('TalkToAi operating policy',body.toPlainText())
            self.assertIn('verified',status.text());self.assertIn(EXPECTED_POLICY_SHA256,status.text())
            self.assertEqual([button.text() for button in dialog.findChildren(QPushButton)],['Close'])
            self.assertFalse(dialog.findChildren(QLineEdit));dialog.accept()
        with patch.object(studio.QDialog,'exec',inspect):self.window.operating_policy_dialog()

    def test_operating_policy_integrity_error_requires_trusted_restore(self):
        from ethics_policy import PolicyIntegrityError
        def inspect(dialog):
            body=dialog.findChild(QPlainTextEdit,'operating_policy_text')
            status=dialog.findChild(QLabel,'operating_policy_status')
            self.assertIn('Restore the trusted release',status.text())
            self.assertIn('cannot modify or reseal',body.toPlainText());self.assertTrue(body.isReadOnly())
            self.assertEqual([button.text() for button in dialog.findChildren(QPushButton)],['Close']);dialog.accept()
        with patch('ethics_policy.verify_release_policy',side_effect=PolicyIntegrityError('Fixture mismatch')),patch('ethics_policy.policy_prompt') as prompt,patch.object(studio.QDialog,'exec',inspect):
            self.window.operating_policy_dialog();prompt.assert_not_called()

    def test_pause_report_is_evidence_bound_persisted_and_not_duplicated(self):
        w=self.window;w.task['verification']={'status':'passed','summary':'Old run'}
        checkpoint={'pass':1,'total_passes':3,'steps':32,'changes':0,'verification':None,
                    'state':'paused','progress_observations':10,'blockers':['PowerShell command syntax failed']}
        w.handle_event('goal_checkpoint',checkpoint)
        summary=w.task['pause_summary'];before=len(w.task['messages'])
        self.assertIn('0 tracked file edits',summary);self.assertIn('no check result recorded this run',summary)
        self.assertIn('Shell commands can change files',summary);self.assertIn('PowerShell command syntax failed',summary)
        self.assertIn('Review the original objective',summary);self.assertNotIn('recorded check status: passed',summary)
        w.handle_event('goal_checkpoint',checkpoint);self.assertEqual(len(w.task['messages']),before)
        self.assertEqual(load_tasks(studio.SESSION)[0][0]['pause_summary'],summary)

    def test_old_paused_conversation_gets_visible_summary_without_history_rewrite(self):
        w=self.window;w.task['goal_checkpoint']={'state':'paused','steps':32,'changes':0,'verification':None}
        before=list(w.task['messages'])
        with patch.object(w.transcript,'setMarkdown') as display:w.render()
        self.assertIn('Task pause report',display.call_args.args[0])
        self.assertIn('0 tracked file edits',display.call_args.args[0])
        self.assertEqual(w.task['messages'],before)
        w.set_busy(True);self.assertNotIn('goal_checkpoint',w.task);w.set_busy(False)

    def test_step_cap_report_is_app_status_and_following_checkpoint_does_not_duplicate(self):
        w=self.window;w.set_busy(True)
        report={'state':'paused','reason':'step_budget','steps':8,'editor_changes':1,
                'verification':{'status':'passed'},'workspace_changes':{'count':1,'complete':True},
                'last_tool_error':'','next_action':'Review the tool result and continue the final report.'}
        w.handle_event('run_summary',report)
        summary=w.task['pause_summary'];count=len(w.task['messages'])
        self.assertIn('App pause report',summary);self.assertIn('unfinished',summary)
        self.assertIn('recorded check status: passed',summary);self.assertIn('Observed source changes: 1',summary)
        self.assertEqual(w.task['messages'][-1]['source'],'app')
        w.handle_event('goal_checkpoint',{'state':'paused','steps':8,'changes':1,'verification':{'status':'passed'}})
        self.assertEqual(len(w.task['messages']),count);self.assertEqual(w.task['pause_summary'],summary)
        w.set_busy(False);w.set_busy(True)
        self.assertNotIn('run_summary',w.task);w.set_busy(False)

    def test_pause_report_uses_saved_next_action_and_finish_keeps_summary_visible(self):
        import time
        w=self.window;w.started_at=time.monotonic();w.route_description='Fixture';w.set_busy(True)
        w.handle_event('task_goal',{'objective':'Pause menu','criteria':[{'text':'Menu opens'}],'next_action':'Edit input handler and run its test'})
        w.handle_event('goal_checkpoint',{'state':'paused','steps':8,'changes':2,'verification':{'status':'failed'},'blockers':[]})
        self.assertIn('Edit input handler and run its test',w.task['pause_summary'])
        self.assertIn('recorded check status: failed',w.task['pause_summary'])
        w.handle_event('finished',None)
        self.assertIn('Paused',w.performance_label.text());self.assertIn('2 tracked edits',w.performance_label.text())
        w.set_busy(True);self.assertNotIn('pause_summary',w.task);self.assertEqual(w.run_step,0)
        w.set_busy(False)

    def test_continue_prefers_explicit_saved_goal_but_new_steering_wins_and_clears_goal(self):
        w=self.window;w.task['project']=str(self.root)
        old=self.root/'old';old.mkdir();new=self.root/'new';new.mkdir();steered=self.root/'steered';steered.mkdir()
        w.task['messages']=[{'role':'user','content':f'Improve "{old}"'}]
        w.handle_event('task_goal',{'objective':f'Improve "{new}"','criteria':[{'text':'Checks pass'}]})
        self.assertTrue(w.prepare_task_target('continue'))
        self.assertEqual(Path(w.task['project']),new.resolve());self.assertIsNone(w.current_task_goal())
        w.handle_event('task_goal',{'objective':f'Improve "{new}"','criteria':[{'text':'Checks pass'}]})
        self.assertTrue(w.prepare_task_target(f'Improve "{steered}"'))
        self.assertEqual(Path(w.task['project']),steered.resolve());self.assertIsNone(w.current_task_goal())

    def test_continue_prompt_includes_saved_goal_without_old_history(self):
        w=self.window;w.task['messages']=[]
        w.handle_event('task_goal',{'objective':'Add keyboard pause menu','criteria':[{'text':'Escape toggles menu'}],'next_action':'Inspect input handler'})
        with patch.object(w,'quick_command') as send:w.continue_task()
        self.assertIn('Saved task goal: Add keyboard pause menu',send.call_args.args[0])
        self.assertIn('Next action: Inspect input handler',send.call_args.args[0])

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
        self.assertEqual(self.window.model_performance(),{'num_ctx':8192,'show_thinking':False,'work_session_minutes':120})

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
