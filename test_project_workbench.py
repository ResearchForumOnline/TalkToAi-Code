import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QDialog, QComboBox, QPlainTextEdit
from project_workbench import ProjectWorkbench, choose_improvement
from project_playbooks import save_playbook
from research_journal import record_experiment


class WorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.dialog = None

    def tearDown(self):
        if self.dialog:
            self.dialog.close(); self.dialog.deleteLater(); self.app.processEvents()

    def open(self):
        self.dialog = ProjectWorkbench(self.root, {'title':'Fixture task'})
        return self.dialog

    def test_playbook_prepares_prompt_without_running_its_steps(self):
        marker = self.root / 'must-not-exist'
        save_playbook(self.root, 'Build fixture', 'Build after changes', [f'Create {marker}'], 'Unverified', [])
        d = self.open(); d.use_playbook()
        self.assertIn('Build fixture', d.selected_prompt)
        self.assertFalse(marker.exists())

    def test_experiment_comparison_keeps_missing_protocol_warnings_visible(self):
        record_experiment(self.root, 'Baseline', 'fixture', 'measured', {'latency_ms':10})
        record_experiment(self.root, 'Candidate', 'fixture', 'measured', {'latency_ms':8})
        d = self.open(); d.compare()
        text = d.research_detail.toPlainText()
        self.assertIn('Candidate minus baseline: -2', text)
        self.assertIn('dataset missing', text)
        self.assertIn('do not prove measurement validity', text)

    def test_malformed_journal_keeps_dialog_usable(self):
        folder=self.root/'.talktoai-code'; folder.mkdir()
        (folder/'EXPERIMENTS.jsonl').write_text('{broken')
        d=self.open()
        self.assertIn('damaged',d.research_detail.toPlainText())
        self.assertFalse(d.baseline.signalsBlocked()); self.assertFalse(d.candidate.signalsBlocked())

    def test_workbench_can_show_and_render_its_tabs(self):
        d=self.open(); d.show(); self.app.processEvents()
        for index in range(d.tabs.count()):
            d.tabs.setCurrentIndex(index); self.app.processEvents()
            self.assertFalse(d.grab().isNull())

    def test_improvement_dialog_cancel_and_selected_iteration(self):
        with patch.object(QDialog,'exec',return_value=QDialog.Rejected):
            self.assertIsNone(choose_improvement(None,'goal',2))
        def accept(dialog):
            dialog.findChild(QComboBox,'improvement_iterations').setCurrentIndex(4)
            dialog.findChild(QPlainTextEdit,'improvement_goal').setPlainText('Reduce parse failures')
            return QDialog.Accepted
        with patch.object(QDialog,'exec',accept):
            self.assertEqual(choose_improvement(None,'goal',2),('Reduce parse failures',5))


if __name__ == '__main__': unittest.main()
