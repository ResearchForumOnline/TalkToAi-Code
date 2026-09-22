import unittest
from pathlib import Path
from PySide6.QtWidgets import QApplication
from update_dialog import UpdateDialog

class DialogTests(unittest.TestCase):
    def test_version_release_error_and_download_states(self):
        app=QApplication.instance() or QApplication([])
        dialog=UpdateDialog(None,Path('.'))
        self.assertFalse(dialog.install.isEnabled())
        dialog.completed(dict(version='0.3.0',newer=True,notes='Changes',page='https://github.com/ResearchForumOnline/TalkToAi-Code/releases'))
        self.assertTrue(dialog.download.isEnabled())
        self.assertEqual(dialog.notes.toPlainText(),'Changes')
        dialog.completed(RuntimeError('offline'))
        self.assertIn('offline',dialog.status.text())
        dialog.completed('installer.exe')
        self.assertTrue(dialog.install.isEnabled())
        dialog.close()
