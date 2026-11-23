"""Check that the native focus selector follows each conversation and send path."""
import json
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import studio
from agent_core import OllamaCapacityError


class AgentFocusUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for key, name in [('HOME', ''), ('STATE', ''), ('SESSION', 'studio.json'),
                          ('CONNECTIONS', 'connections.json'), ('PROVIDERS', 'providers.json')]:
            self.stack.enter_context(patch.object(studio, key, self.root / name))
        for method in ('install_tray', 'health', 'refresh_mail_status', '_poll_local_models'):
            self.stack.enter_context(patch.object(studio.Studio, method))
        for name in ('EscapeCancel', 'ensure_local_runtime', 'ensure_local_model', 'ensure_amd_tunnel'):
            self.stack.enter_context(patch.object(studio, name))
        self.stack.enter_context(patch('runtime_diagnostics.running_probe', return_value={'state':'reachable','models':[]}))
        self.route = self.stack.enter_context(patch.object(studio, 'choose_route', return_value={
            'route':'local', 'url':'http://127.0.0.1:11434', 'model':'small', 'reason':'fixture'}))
        self.chat = self.stack.enter_context(patch.object(studio, 'run_conversation', side_effect=self.reply))
        self.agent = self.stack.enter_context(patch.object(studio, 'run_agent'))

    @staticmethod
    def reply(url, model, history, cancel, emit, **options):
        emit('message', {'role':'assistant','content':'A completed answer.'})

    def window(self):
        w = studio.Studio()
        w.show()
        self.app.processEvents()
        w.task['project'] = str(self.root)
        def close():
            w.allow_quit = True
            w.close()
            w.deleteLater()
            self.app.processEvents()
        self.stack.callback(close)
        return w

    def wait(self, w):
        deadline = time.monotonic() + 4
        while w.busy and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(w.busy, w.status.text())

    def send(self, w, text):
        w.prompt.setPlainText(text)
        QTest.keyClick(w.prompt, Qt.Key.Key_Return)
        self.wait(w)

    def choose(self, w, key):
        index = w.agent_focus.findData(key)
        self.assertGreaterEqual(index, 0)
        w.agent_focus.setCurrentIndex(index)

    def test_legacy_and_unknown_preferences_default_to_auto_and_choices_survive_restart(self):
        old = {'id':'old','kind':'chat','title':'Earlier chat','project':str(self.root),
               'messages':[{'role':'user','content':'An earlier message.'}]}
        unknown = dict(old, id='unknown', agent_focus={'retired':'key'})
        studio.SESSION.write_text(json.dumps([old, unknown]), encoding='utf-8')
        w = self.window()
        self.assertEqual(w.agent_focus.accessibleName(), 'Agent Zero specialist')
        self.assertTrue(w.agent_focus.isVisible())
        self.assertEqual(w.agent_focus.currentData(), 'auto')
        self.assertEqual([w.agent_focus.itemText(i) for i in range(w.agent_focus.count())],
                         ['Auto','Games','Coding','Research','Writing','Shopping','Everyday'])
        self.choose(w, 'game')
        w.select_task_by_id('unknown')
        self.assertEqual(w.agent_focus.currentData(), 'auto')
        w.new_task()
        new_id = w.task['id']
        self.assertEqual(w.agent_focus.currentData(), 'auto')
        self.choose(w, 'research')
        w.select_task_by_id('old')
        self.assertEqual(w.agent_focus.currentData(), 'game')
        restored = self.window()
        restored.select_task_by_id(new_id)
        self.assertEqual(restored.agent_focus.currentData(), 'research')
        restored.select_task_by_id('old')
        self.assertEqual(restored.agent_focus.currentData(), 'game')
        restored.switch_workspace('code')
        self.assertTrue(restored.agent_focus.isVisible())
        self.assertEqual(restored.agent_focus.currentData(), 'auto')

    def test_auto_selects_for_each_turn_and_manual_override_needs_no_extra_action(self):
        w = self.window()
        route = w.route.currentIndex()
        self.send(w, 'Please write a short story about a train.')
        self.assertEqual(self.chat.call_args.kwargs['focus'], 'writing')
        self.assertEqual(w.task['agent_focus'], 'auto')
        self.send(w, 'Hello!')
        self.assertNotIn('focus', self.chat.call_args.kwargs)
        self.choose(w, 'research')
        self.send(w, 'Hello!')
        self.assertEqual(self.chat.call_args.kwargs['focus'], 'research')
        self.choose(w, 'everyday')
        self.send(w, 'Please write a short story about a train.')
        self.assertNotIn('focus', self.chat.call_args.kwargs)
        self.agent.assert_not_called()
        self.assertEqual(w.route.currentIndex(), route)
        self.assertEqual(self.chat.call_count, 4)

    def test_retry_uses_current_choice_and_the_original_message(self):
        w = self.window()
        self.chat.side_effect = RuntimeError('Temporary fixture failure.')
        self.send(w, 'Please write a short story.')
        self.assertEqual(self.chat.call_args.kwargs['focus'], 'writing')
        self.choose(w,'game')
        w.prompt.setPlainText('Another unsent draft.')
        self.chat.side_effect = self.reply
        self.assertTrue(w.retry_button.isVisible())
        QTest.mouseClick(w.retry_button, Qt.MouseButton.LeftButton)
        self.wait(w)
        self.assertEqual(self.chat.call_args.kwargs['focus'], 'game')
        self.assertEqual(self.chat.call_args.args[2][-1]['content'], 'Please write a short story.')
        self.assertEqual(w.prompt.toPlainText(), 'Another unsent draft.')
        self.assertEqual([m['role'] for m in w.task['messages']], ['user','assistant'])

    def test_local_memory_recovery_preserves_the_same_specialist(self):
        w = self.window()
        small = w.config['local_model']
        self.route.side_effect = [
            {'route':'local_large','url':'http://127.0.0.1:11434','model':'large','reason':'fixture'},
            {'route':'local','url':'http://127.0.0.1:11434','model':small,'reason':'fixture'}]
        def candidate(url, model, history, cancel, emit, **options):
            if model == 'large':
                raise OllamaCapacityError('Model needs more memory.')
            self.reply(url,model,history,cancel,emit,**options)
        self.chat.side_effect = candidate
        self.send(w, 'Please write a short story.')
        self.assertEqual([call.kwargs.get('focus') for call in self.chat.call_args_list], ['writing','writing'])
        self.assertEqual([m['role'] for m in w.task['messages']], ['user','assistant'])

    def test_agent_receives_effective_role_from_chat_and_code_with_existing_plan_permissions(self):
        w = self.window()
        self.choose(w, 'game')
        w.mode.setCurrentText('Plan')
        self.send(w, 'Review the code implementation, tests and README in this project.')
        self.assertEqual(self.agent.call_args.kwargs['task_kind'], 'chat')
        self.assertEqual(self.agent.call_args.kwargs['focus'], 'game')
        self.assertFalse(self.agent.call_args.args[4])
        w.switch_workspace('code')
        self.assertEqual(w.agent_focus.currentData(), 'auto')
        self.send(w, 'Debug the Python code that builds the index.')
        self.assertEqual(self.agent.call_args.kwargs['task_kind'], 'code')
        self.assertEqual(self.agent.call_args.kwargs['focus'], 'coding')
        self.chat.assert_not_called()

    def test_shopping_comparison_uses_chat_agent(self):
        w = self.window()
        self.choose(w, 'shopping')
        self.send(w, 'Compare prices and delivery for this monitor.')
        self.assertEqual(self.agent.call_args.kwargs['task_kind'], 'chat')
        self.assertEqual(self.agent.call_args.kwargs['focus'], 'shopping')
        self.chat.assert_not_called()

    def test_create_custom_agent_from_chat_restore_use_edit_and_remove(self):
        w = self.window()
        self.send(w, 'Please create an agent named Atlas to cite primary sources in short answers.')
        self.assertEqual(w.agent_focus.currentData(), 'custom:atlas')
        self.assertIn('Created Atlas', w.transcript.toPlainText())
        self.chat.assert_not_called()
        self.agent.assert_not_called()
        self.send(w, 'Please create an agent named Atlas to replace the earlier instructions.')
        self.assertIn('already exists', w.status.text())
        self.assertIn('cite primary sources', w.specialists['custom:atlas'].instructions)
        restored = self.window()
        restored.select_task_by_id(w.task['id'])
        self.assertEqual(restored.agent_focus.currentData(), 'custom:atlas')
        self.send(restored, 'Hello!')
        from agent_focus import CustomSpecialist, focus_guidance
        self.assertIn('cite primary sources', focus_guidance(self.chat.call_args.kwargs['focus']))
        restored.switch_workspace('code')
        self.choose(restored, 'custom:atlas')
        self.send(restored, 'Debug the Python code that builds the index.')
        self.assertEqual(self.agent.call_args.kwargs['focus'].name, 'Atlas')
        revised = CustomSpecialist.create('Atlas', 'Check release notes.', 'research')
        restored.store_specialist(revised)
        self.assertIn('Work as a researcher', focus_guidance(restored.specialists[revised.key]))
        restored.store_specialist(revised, remove=True)
        self.assertEqual(restored.agent_focus.findData(revised.key), -1)
        self.assertTrue(all(t.get('agent_focus') != revised.key for t in restored.tasks))
        self.assertNotIn('Atlas', (self.root/'config.json').read_text(encoding='utf-8'))

    def test_auto_uses_existing_game_markers_only_for_an_ambiguous_project_task(self):
        w = self.window()
        with patch('asset_library.asset_engine') as probe:
            self.send(w, 'Hello!')
            probe.assert_not_called()
        w.switch_workspace('code')
        (self.root/'project.godot').write_text('config_version=5', encoding='utf-8')
        self.send(w, 'Improve this scene.')
        self.assertEqual(self.agent.call_args.kwargs['focus'], 'game')
