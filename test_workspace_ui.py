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


if __name__=='__main__':unittest.main()
