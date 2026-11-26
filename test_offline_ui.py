"""Offline workspace integration: isolated profiles and no network/model calls."""
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtCore import QUrl

import studio
from offline_assistant import OFFLINE_COMMANDS
from offline_workbench import LocalToolsDialog
from session_store import load_tasks


class OfflineWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.stack = ExitStack()
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, value in (
            ('HOME', self.root), ('STATE', self.root),
            ('SESSION', self.root / 'studio.json'),
            ('CONNECTIONS', self.root / 'connections.json'),
            ('PROVIDERS', self.root / 'providers.json'),
        ):
            self.stack.enter_context(patch.object(studio, name, value))
        self.stack.enter_context(patch.object(studio.Studio, 'health'))
        self.stack.enter_context(patch.object(studio.Studio, 'install_tray'))
        hook = self.stack.enter_context(patch('studio.EscapeCancel')).return_value
        hook.arm.return_value = False
        (self.root/'config.json').write_text('{"last_workspace":"code"}',encoding='utf-8')
        self.window = studio.Studio()
        self.window.task['project'] = str(self.root)
        self.route = self.stack.enter_context(patch('studio.choose_route', side_effect=AssertionError('Offline work must not select a model')))
        self.agent = self.stack.enter_context(patch('studio.run_agent', side_effect=AssertionError('Offline work must not invoke an LLM')))
        self.tunnel = self.stack.enter_context(patch('studio.ensure_amd_tunnel', side_effect=AssertionError('Offline work must not connect to a server')))
        self.local_model = self.stack.enter_context(patch('studio.ensure_local_model', side_effect=AssertionError('Offline work must not start a model')))

    def tearDown(self):
        self.window.allow_quit = True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.stack.close()

    def assert_no_model(self):
        for method in (self.route, self.agent, self.tunnel, self.local_model):
            method.assert_not_called()

    def test_offline_scene_via_send_writes_verified_glb_without_a_model(self):
        import scene_kit
        w = self.window
        w.mode.setCurrentText('Act')
        w.config['auto_open_results'] = False
        w.prompt.setPlainText('Make a 3D winter scene offline')
        worker = self.capture_worker()
        with patch('engine_discovery.find_blender', return_value=None):
            w.send()
            worker.call_args.kwargs['target']()
        self.assert_no_model()
        self.assertFalse(w.busy)
        files = {Path(a['artifact']).suffix: Path(a['artifact']) for a in w.task['artifacts']}
        self.assertGreater(scene_kit.check_glb(files['.glb'].read_bytes())['objects'], 10)
        self.assertTrue(files['.html'].is_file())
        self.assertIn('winter', w.task['messages'][-1]['content'].lower())

    def test_offline_realism_remains_partial_until_user_accepts_verified_scene(self):
        w = self.window
        w.mode.setCurrentText('Act')
        w.config['auto_open_results'] = False
        w.prompt.setPlainText('Make a photorealistic 3D lunar impact scene offline')
        worker = self.capture_worker()
        with patch('engine_discovery.find_blender', return_value=None):
            w.send()
            worker.call_args.kwargs['target']()
        marker = w.task['messages'][-1]['scene_delivery']
        self.assertFalse(marker['complete'])
        self.assertIn('stylized', w.task['pending_question'])
        w.prompt.setPlainText('keep the stylized scene')
        w.send()
        self.assertFalse(w.busy)
        self.assertNotIn('pending_question', w.task)
        self.assertEqual(w.task['messages'][-1]['scene_delivery'], dict(marker, complete=True))
        self.assertEqual(len(list(self.root.glob('*.glb'))), 1)
        self.assert_no_model()

    def test_connection_handoff_resumes_the_original_work_in_until_done(self):
        w = self.window
        w.mode.setCurrentText('Act')
        w.route.setCurrentIndex(1)
        w.prompt.setPlainText('Improve the project menu')
        worker = self.capture_worker()
        self.local_model.side_effect = ConnectionError('Fixture service offline')
        w.send()
        worker.call_args.kwargs['target']()
        self.assertEqual(w.task['goal_checkpoint']['state'], 'awaiting answer')
        self.assertIn('select a working model', w.task['pending_question'])
        self.local_model.side_effect = None
        self.route.side_effect = None
        self.route.return_value = {'route':'local','model':'fixture','url':'http://127.0.0.1:11434','reason':'test'}
        self.agent.side_effect = None
        w.prompt.setPlainText('It is connected now')
        w.send()
        worker.call_args.kwargs['target']()
        history = self.agent.call_args.args[2]
        self.assertTrue(history[-1]['answers_task_question'])
        self.assertIn('Improve the project menu', [item['content'] for item in history])
        self.assertNotIn('pending_question', w.task)

    def test_unknown_offline_scene_asks_for_a_subject_without_connecting(self):
        w = self.window
        w.mode.setCurrentText('Act')
        w.prompt.setPlainText('Make a 3D model of a violin offline')
        w.send()
        self.assertFalse(w.busy)
        self.assertIn('supported subject', w.task['messages'][-1]['content'])
        self.assertFalse(list(self.root.glob('*.glb')))
        self.assert_no_model()

    def test_missing_model_uses_scene_generator_before_any_agent_turn(self):
        w = self.window
        w.mode.setCurrentText('Act')
        w.route.setCurrentIndex(1)
        w.config['auto_open_results'] = False
        w.prompt.setPlainText('Create a 3D winter scene')
        worker = self.capture_worker()
        self.local_model.side_effect = ConnectionError('Offline fixture')
        with patch('engine_discovery.find_blender', return_value=None):
            w.send()
            worker.call_args.kwargs['target']()
        self.assertFalse(w.busy)
        self.assertTrue(list(self.root.glob('*.glb')))
        self.agent.assert_not_called()
        self.route.assert_not_called()
        self.assertIn('no model request', w.performance_label.text())

    def capture_worker(self):
        worker = self.stack.enter_context(patch('studio.threading.Thread'))
        return worker

    def test_checklist_in_chat_and_code_is_offline_scoped_and_persistent(self):
        w = self.window
        for kind in ('chat','code'):
            with self.subTest(kind=kind):
                w.task['kind'] = kind
                w.prompt.setPlainText('Make a to-do list: sketch the scene, add lighting, export the model')
                w.send()
                w.prompt.setPlainText('mark item 2 done')
                w.send()
                self.assertIn('Done (marked by you) · add lighting', w.transcript.toPlainText())
                reread = next(task for task in load_tasks(studio.SESSION)[0] if task['id'] == w.task['id'])
                self.assertEqual(studio.task_checklist.saved(reread)['steps'][1]['status'], 'completed')
                w.prompt.setPlainText('uncheck 2')
                w.send()
                self.assertEqual(studio.task_checklist.saved(w.task)['steps'][1]['status'], 'pending')
        project = w.task['project']
        w.task['project'] = str(self.root/'another')
        w.prompt.setPlainText('show my to-do list')
        w.send()
        self.assertIn('What would you like to get done?', w.task['messages'][-1]['content'])
        w.task['project'] = project
        self.assertEqual(len(studio.task_checklist.saved(w.task)['steps']), 3)
        self.assert_no_model()

    def test_make_a_list_of_to_do_uses_latest_human_objective_and_newer_goal(self):
        w = self.window
        w.task['messages'] = [
            {'role':'user','content':'Make a winter scene'},
            {'role':'user','content':'Fix the exporter, run the tests and publish the update'},
            {'role':'assistant','content':'I published it'}, {'role':'user','content':'thanks sir'}]
        w.prompt.setPlainText('make a list of to do')
        w.send()
        plan = studio.task_checklist.saved(w.task)
        self.assertEqual([s['step'] for s in plan['steps']], ['Fix the exporter','run the tests','publish the update'])
        self.assertTrue(all(s['status'] == 'pending' for s in plan['steps']))
        w.handle_event('task_goal',{'objective':'Review the installer', 'criteria':[{'text':'Check signatures'}]})
        w.prompt.setPlainText('show my checklist')
        w.send()
        self.assertIn('Check signatures', w.task['messages'][-1]['content'])
        self.assertNotIn('Fix the exporter', w.task['messages'][-1]['content'])
        self.assert_no_model()

    def test_busy_checklist_preserves_run_and_checks_only_the_verified_scene(self):
        w = self.window
        w.task['messages'] = [{'role':'user', 'content':'Make a lunar scene and write the guide'}]
        w.execution_kind = 'model'
        w.set_busy(True)
        try:
            w.begin_checklist(w.task['messages'][0]['content'], scene=True)
            history = list(w.task['messages'])
            token = w.cancel
            with patch.object(w, '_request_stop', side_effect=AssertionError('A checklist must not interrupt')):
                w.prompt.setPlainText('show my to-do list')
                w.send()
            self.assertEqual(w.task['messages'], history)
            self.assertIs(w.cancel, token)
            self.assertFalse(token.is_set())
            self.assertEqual(w.pending_prompt, '')
            self.assertIn('Verify the saved scene', w.transcript.toPlainText())
            scene = self.root/'moon.blend'
            scene.write_bytes(b'BLENDER fixture')
            record = json.loads(studio.register_output(studio.ProjectTools(self.root, project_only=True),str(scene)))
            w.handle_event('artifact', record)
            result = {'status':'passed','summary':'Geometry checked.',
                      'primary_output':{'name':scene.name,'sha256':'0'*64}}
            w.handle_event('verification',result)
            self.assertEqual(w.checklist_run['steps'][0]['status'],'pending')
            result['primary_output']['sha256'] = record['sha256']
            w.handle_event('verification',result)
            self.assertEqual([item['status'] for item in w.checklist_run['steps']], ['completed','completed','pending'])
            self.assertIn('Done (app check)', w.transcript.toPlainText())
        finally:
            w.set_busy(False)
        self.assert_no_model()

    def test_project_syntax_check_cannot_mark_tests_complete(self):
        w = self.window
        plan = studio.task_checklist.begin(w.task, 'Write the guide and run the tests')
        studio.task_checklist.observe(plan, {'status':'passed','summary':'Valid syntax'}, set(), checked=True)
        studio.task_checklist.observe_test_output(plan, 'Exit 0\nStandalone file checks\nScript compiles.')
        self.assertEqual(plan['steps'][1]['status'], 'pending')
        studio.task_checklist.observe_test_output(plan, 'Exit 1\nRan 4 tests in 0.1s\nFAILED')
        self.assertEqual(plan['steps'][1]['status'], 'pending')
        studio.task_checklist.observe_test_output(plan, 'Exit 0\nRan 4 tests in 0.1s\nOK')
        self.assertEqual(plan['steps'][1]['status'], 'completed')
        self.assertEqual(plan['steps'][1]['basis'], 'observed')

    def test_scene_browser_followup_uses_registered_local_viewer_without_a_model(self):
        w = self.window
        tools = studio.ProjectTools(self.root, True, project_only=True)
        scene = self.root / 'Moon Garden.glb'
        page = self.root / 'Moon Garden-viewer.html'
        scene.write_bytes(b'GLB fixture')
        page.write_text('<html>mesh fixture</html>')
        src = json.loads(studio.register_output(tools, str(scene)))
        viewer = dict(json.loads(studio.register_output(tools, str(page))), type='viewer', auto_open=True)
        w.task['artifacts'] = [src, viewer]
        worker = self.capture_worker()
        with patch.object(studio.QDesktopServices, 'openUrl', return_value=True) as opened:
            w.prompt.setPlainText('how do i open file sir with browser?')
            w.send()
            worker.call_args.kwargs['target']()
            opened.assert_not_called()
            url = 'talktoai-viewer://local/' + viewer['sha256']
            self.assertIn(url, w.transcript.toHtml())
            w.open_chat_link(QUrl(url))
            self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), page.resolve())
            opened.reset_mock()
            w.prompt.setPlainText('open the scene in my browser')
            w.send()
            worker.call_args.kwargs['target']()
            opened.assert_called_once()
            self.assertEqual(len(w.task['artifacts']), 2)
            opened.reset_mock()
            page.write_text('<html>modified after registration</html>')
            self.assertFalse(w.open_chat_link(QUrl(url)))
            opened.assert_not_called()
        self.assert_no_model()

    @staticmethod
    def report(**changes):
        result = {'status': 'completed', 'title': 'Project inventory',
                  'summary': 'Found 2 source files.', 'details': ['main.py', 'helper.py'],
                  'actions': [], 'metrics': {'files': 2}}
        result.update(changes)
        return result

    def test_local_request_saves_readable_history_and_clears_draft_without_model(self):
        w = self.window
        text = '/local project_report'
        request = {'command': 'project_report'}
        w.prompt.setPlainText(text)
        w.task['draft'] = text
        worker = self.capture_worker()
        with patch('studio.run_offline', return_value=self.report()) as execute:
            w.start_local_request(text, request)
            self.assertTrue(w.busy)
            self.assertTrue(w.stop.isEnabled())
            self.assertEqual(w.prompt.toPlainText(), '')
            self.assertEqual(w.task['draft'], '')
            self.assertNotEqual(w.task['title'], 'New task')
            self.assertNotIn('model has not replied', w.run_card_detail.text().lower())
            worker.call_args.kwargs['target']()
            execute.assert_called_once_with(str(self.root), command='project_report', query='', cancel=w.cancel)
        self.assertFalse(w.busy)
        self.assertIn('Found 2 source files.', w.transcript.toPlainText())
        self.assertEqual(w.task['messages'][0], {'role': 'user', 'content': text})
        self.assertEqual(w.task['messages'][-1].get('source'), 'local')
        saved = load_tasks(studio.SESSION)[0][0]
        self.assertEqual(saved['messages'], w.task['messages'])
        self.assertNotIn('tokens/s', w.performance_label.text())
        self.assert_no_model()

    def test_local_failure_restores_request_and_gives_local_recovery_text(self):
        w = self.window
        worker = self.capture_worker()
        with patch('studio.run_offline', side_effect=ValueError('Choose a file inside this project.')):
            w.start_local_request('/local hash ../outside.txt', {'command': 'hash', 'query': '../outside.txt'})
            worker.call_args.kwargs['target']()
        self.assertFalse(w.busy)
        self.assertEqual(w.prompt.toPlainText(), '/local hash ../outside.txt')
        self.assertIn('Choose a file inside this project.', w.transcript.toPlainText())
        self.assertNotIn('adjust model', w.status.text().lower())
        self.assertNotIn('tokens/s', w.performance_label.text())
        self.assert_no_model()

    def test_stop_local_run_prevents_ready_or_completed_status(self):
        w = self.window
        worker = self.capture_worker()
        with patch('studio.run_offline', return_value=self.report(status='cancelled', summary='Stopped before completion.')):
            w.start_local_request('/local project_report', {'command': 'project_report'})
            w.stop_task()
            self.assertTrue(w.cancel.is_set())
            worker.call_args.kwargs['target']()
        self.assertFalse(w.busy)
        self.assertIn('Stopped', w.status.text())
        self.assertNotEqual(w.status.text(), 'Ready')
        self.assert_no_model()

    def test_local_inspection_preserves_saved_coding_checkpoint(self):
        w = self.window
        checkpoint = {'state': 'paused', 'steps': 32, 'changes': 1,
                      'blockers': ['The menu still needs an input fix.']}
        w.task['goal_checkpoint'] = checkpoint
        w.task['pause_summary'] = 'Continue fixing the menu input.'
        w.task['workspace_changes'] = {'count': 1, 'paths': ['main.gd'], 'complete': True}
        worker = self.capture_worker()
        with patch('studio.run_offline', return_value=self.report()):
            w.start_local_request('/local project_report', {'command': 'project_report'})
            worker.call_args.kwargs['target']()
        self.assertEqual(w.task['goal_checkpoint'], checkpoint)
        self.assertEqual(w.task['pause_summary'], 'Continue fixing the menu input.')
        self.assertEqual(w.task['workspace_changes']['paths'], ['main.gd'])
        self.assert_no_model()

    def test_source_shaped_like_model_protocol_stays_visible_as_local_evidence(self):
        w = self.window
        worker = self.capture_worker()
        report = self.report(details=['```', '<function=example>', '[source](https://invalid.example)'])
        with patch('studio.run_offline', return_value=report):
            w.start_local_request('/local project_report', {'command': 'project_report'})
            worker.call_args.kwargs['target']()
        visible = w.transcript.toPlainText()
        self.assertIn('<function=example>', visible)
        self.assertNotIn('protocol text is hidden', visible.lower())
        self.assert_no_model()

    def test_exact_natural_request_runs_before_model_routing(self):
        w = self.window
        worker = self.capture_worker()
        w.prompt.setPlainText('What is in this project?')
        with patch('studio.run_offline', return_value=self.report()) as execute:
            w.send()
            worker.call_args.kwargs['target']()
        execute.assert_called_once_with(str(self.root), command='project_report', query='', cancel=w.cancel)
        self.assertIn('Found 2 source files.', w.transcript.toPlainText())
        self.assert_no_model()

    def test_compound_coding_request_is_not_silently_reduced_to_an_inspection(self):
        w = self.window
        with patch.object(w, 'start_local_request') as execute:
            handled = w.natural_control('What is in this project? Improve its menu and fix the bugs.')
        self.assertFalse(handled)
        execute.assert_not_called()
        self.assert_no_model()

    def test_local_dialog_cancel_preserves_existing_draft(self):
        w = self.window
        w.prompt.setPlainText('Keep my pending game improvement request')
        with patch('studio.LocalToolsDialog') as dialog, patch.object(w, 'start_local_request') as execute:
            dialog.return_value.exec.return_value = QDialog.Rejected
            w.local_tools()
        execute.assert_not_called()
        self.assertEqual(w.prompt.toPlainText(), 'Keep my pending game improvement request')
        self.assert_no_model()

    def test_local_dialog_selection_dispatches_request_with_no_provider(self):
        w = self.window
        request = {'command': 'find_symbols', 'query': 'player'}
        with patch('studio.LocalToolsDialog') as dialog, patch.object(w, 'start_local_request') as execute:
            dialog.return_value.exec.return_value = QDialog.Accepted
            dialog.return_value.selected_request = request
            dialog.return_value.selected_template = None
            w.local_tools()
        execute.assert_called_once()
        self.assertEqual(execute.call_args.args[1], request)
        self.assert_no_model()

    def test_dialog_request_preserves_an_unsubmitted_draft(self):
        w=self.window;w.prompt.setPlainText('Improve the menu after this inspection')
        worker=self.capture_worker()
        with patch('studio.LocalToolsDialog') as dialog,patch('studio.run_offline',return_value=self.report()):
            dialog.return_value.exec.return_value=QDialog.Accepted
            dialog.return_value.selected_request={'command':'project_report','query':''}
            w.local_tools();worker.call_args.kwargs['target']()
        self.assertEqual(w.prompt.toPlainText(),'Improve the menu after this inspection')
        self.assertEqual(w.task['draft'],'Improve the menu after this inspection')
        self.assert_no_model()

    def test_browser_starter_opens_only_after_explicit_request(self):
        w=self.window;w.mode.setCurrentText('Act')
        with patch.object(studio.QDesktopServices,'openUrl',return_value=True) as launch:
            w.create_local_project('browser-game','my-game')
            launch.assert_not_called()
            w.prompt.setPlainText('open starter');w.send()
            launch.assert_called_once()
            self.assertEqual(Path(launch.call_args.args[0].toLocalFile()).resolve(),(self.root/'my-game'/'index.html').resolve())
        self.assert_no_model()

    def test_local_dialog_does_not_start_another_worker_while_busy(self):
        w = self.window
        w.set_busy(True)
        with patch('studio.LocalToolsDialog') as dialog:
            w.local_tools()
        dialog.assert_not_called()
        self.assert_no_model()

    def test_project_creation_requires_act_and_preserves_project_on_failure(self):
        w = self.window
        w.mode.setCurrentText('Plan')
        initial_id = w.task['id']
        with patch('studio.create_project_template') as create, patch.object(w, 'error'):
            w.create_local_project('python-cli', 'First app')
        create.assert_not_called()
        self.assertEqual(w.task['id'], initial_id)
        self.assertEqual(w.task['project'], str(self.root))
        w.mode.setCurrentText('Act')
        with patch('studio.create_project_template', side_effect=ValueError('Project already exists.')), patch.object(w, 'error'):
            w.create_local_project('python-cli', 'First app')
        self.assertEqual(w.task['id'], initial_id)
        self.assertEqual(w.task['project'], str(self.root))
        self.assert_no_model()

    def test_successful_project_creation_opens_new_task_and_records_instructions(self):
        w = self.window
        w.mode.setCurrentText('Act')
        initial_id = w.task['id']
        path = self.root / 'first-app'
        path.mkdir()
        (path / 'main.py').write_text('print("Hello")\n')
        result = {'status': 'created', 'project_path': str(path), 'files': ['main.py'],
                  'entrypoint': 'main.py', 'launch_instructions': 'python main.py',
                  'summary': 'Created a Python project.'}
        with patch('studio.create_project_template', return_value=result) as create:
            w.create_local_project('python-cli', 'first-app')
        create.assert_called_once()
        self.assertNotEqual(w.task['id'], initial_id)
        self.assertEqual(Path(w.task['project']).resolve(), path.resolve())
        self.assertIn('python main.py', w.transcript.toPlainText())
        saved = next(task for task in load_tasks(studio.SESSION)[0] if task['id'] == w.task['id'])
        self.assertEqual(Path(saved['project']).resolve(), path.resolve())
        self.assert_no_model()

    def test_actual_python_template_creation_uses_backend_without_model(self):
        w = self.window
        w.mode.setCurrentText('Act')
        with patch.object(w, 'error') as error:
            w.create_local_project('python-cli', 'real-template-fixture')
        error.assert_not_called()
        project = self.root / 'real-template-fixture'
        self.assertTrue((project / 'taskbox.py').is_file())
        self.assertEqual(Path(w.task['project']).resolve(), project.resolve())
        self.assertIn('python taskbox.py', w.transcript.toPlainText())
        self.assert_no_model()

    def test_workbench_requires_find_query_and_only_returns_selected_request(self):
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*'))
        dialog = LocalToolsDialog(self.root, self.window)
        self.addCleanup(dialog.deleteLater)
        self.assertIn('no AI required', dialog.windowTitle())
        index = next(i for i, item in enumerate(OFFLINE_COMMANDS) if item['id'] == 'find_symbols')
        dialog.commands.setCurrentRow(index)
        self.assertEqual(dialog.commands.currentItem().text(), OFFLINE_COMMANDS[index]['title'])
        self.assertEqual(dialog.description.text(), OFFLINE_COMMANDS[index]['description'])
        dialog.query.clear()
        self.assertFalse(dialog.run_button.isEnabled())
        dialog.choose_request()
        self.assertIsNone(dialog.selected_request)
        dialog.query.setText('Player')
        self.assertTrue(dialog.run_button.isEnabled())
        dialog.choose_request()
        self.assertEqual(dialog.selected_request, {'command': 'find_symbols', 'query': 'Player'})
        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*')), before)
        self.assert_no_model()

    def test_workbench_creation_disabled_in_plan_even_when_handler_is_called(self):
        dialog = LocalToolsDialog(self.root, self.window, allow_create=False)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.create_button.isEnabled())
        dialog.choose_template()
        self.assertIsNone(dialog.selected_template)
        self.assertEqual(dialog.result(), QDialog.Rejected)
        self.assert_no_model()

    def test_workbench_accepting_template_only_selects_and_never_creates_files(self):
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*'))
        dialog = LocalToolsDialog(self.root, self.window, allow_create=True)
        self.addCleanup(dialog.deleteLater)
        dialog.template.setCurrentIndex(dialog.template.findData('python-cli'))
        dialog.project_name.setText('my-new-app')
        self.assertTrue(dialog.template_detail.text())
        self.assertIn('my-new-app', dialog.destination.text())
        dialog.choose_template()
        self.assertEqual(dialog.selected_template, ('python-cli', 'my-new-app'))
        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*')), before)
        self.assert_no_model()


if __name__ == '__main__':
    unittest.main()
