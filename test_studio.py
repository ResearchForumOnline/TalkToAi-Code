import tempfile
import time
from pathlib import Path
from unittest.mock import patch
import unittest
from PySide6.QtWidgets import QApplication
import studio
from PySide6.QtGui import QCloseEvent

class StudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_controls_and_agent_events_persist(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(studio,'SESSION',Path(folder)/'studio.json'):
            window=studio.Studio();window.task['project']=folder
            window.prompt.setPlainText('use AMD');window.send();self.assertEqual(window.route.currentIndex(),2)
            window.prompt.setPlainText('switch to plan mode');window.send();self.assertEqual(window.mode.currentText(),'Plan')
            def agent(url,model,history,project,act,cancel,emit):
                self.assertFalse(act)
                emit('delta','Checked project.')
                emit('message',{'role':'assistant','content':'Checked project.'})
                emit('metrics',{'tokens_per_second':9,'seconds':1,'step':1})
                emit('status','Ready')
            with patch.object(studio,'choose_route',return_value={'url':'http://127.0.0.1:11435','model':'fixture','route':'server','reason':'test'}),patch.object(studio,'run_agent',side_effect=agent):
                window.prompt.setPlainText('Inspect the project');window.send()
                deadline=time.monotonic()+3
                while window.busy and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
            self.assertFalse(window.busy)
            self.assertEqual(window.task['messages'][-1]['content'],'Checked project.')
            self.assertTrue(studio.SESSION.exists())
            window.close();window.deleteLater();self.app.processEvents()

    def test_close_keeps_background_task_and_draft(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(studio,'SESSION',Path(folder)/'studio.json'):
            window=studio.Studio()
            if window.tray is None:
                window.deleteLater();self.skipTest('No Windows tray available')
            window.prompt.setPlainText('Continue this later')
            event=QCloseEvent();window.closeEvent(event)
            self.assertFalse(event.isAccepted())
            self.assertFalse(window.cancel.is_set())
            self.assertEqual(window.task['draft'],'Continue this later')
            window.allow_quit=True;window.close();window.deleteLater();self.app.processEvents()

    def test_direct_tool_does_not_invoke_model(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(studio,'SESSION',Path(folder)/'studio.json'):
            window=studio.Studio();window.task['project']=folder
            with patch.object(studio,'run_agent') as model:
                window.prompt.setPlainText('inspect project');window.send()
                deadline=time.monotonic()+3
                while window.busy and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
                model.assert_not_called()
            self.assertFalse(window.busy)
            self.assertIn('General',window.task['messages'][-1]['content'])
            window.allow_quit=True;window.close();window.deleteLater();self.app.processEvents()

if __name__=='__main__':unittest.main()
