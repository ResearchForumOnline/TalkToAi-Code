"""Model setup feedback, Hugging Face link selection and Ollama removal in Qt."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMessageBox

import model_library_dialog as ui
from model_library import ModelFile, QWEN_LOCAL_MODEL


class ModelDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.selected = Mock()
        self.removed = Mock()
        self.dialog = ui.ModelLibraryDialog(None, self.root, self.selected, self.removed, Mock(), Mock(), Mock())
        self.threads = self.enterContext(patch('model_library_dialog.threading.Thread'))
        self.addCleanup(self.close_dialog)

    def close_dialog(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()

    def run_worker(self):
        self.threads.call_args.kwargs['target']()

    def test_install_click_immediately_shows_status_then_explains_error(self):
        with patch.object(ui, 'require_model_runtime', side_effect=ConnectionError('Install Ollama using the official installer.')):
            self.dialog.presets[1].click()
            self.assertIn('Preparing Qwen', self.dialog.status.text())
            self.assertEqual(self.dialog.progress.maximum(), 0)
            self.assertTrue(self.dialog.stop.isEnabled())
            self.assertFalse(self.dialog.presets[0].isEnabled())
            self.run_worker()
        self.assertIn('Error · Install Ollama', self.dialog.status.text())
        self.assertTrue(self.dialog.presets[1].isEnabled())
        self.selected.assert_not_called()

    def test_successful_preset_uses_exact_installed_model_for_large_route(self):
        with patch.object(ui, 'require_model_runtime') as setup, patch.object(
            ui, 'install_model', return_value=QWEN_LOCAL_MODEL,
        ) as install:
            self.dialog.presets[1].click()
            self.run_worker()
        setup.assert_called_once()
        self.assertEqual(install.call_args.args[0], QWEN_LOCAL_MODEL)
        self.selected.assert_called_once_with(QWEN_LOCAL_MODEL, 'local_large')
        self.assertEqual(self.dialog.progress.value(), 100)

    def test_file_link_selects_exact_pasted_file(self):
        link = 'https://huggingface.co/owner/model/resolve/abc/nested/Model%20Q4.gguf?download=true'
        target = ModelFile('nested/Model Q4.gguf', 4096)
        others = [ModelFile('README.md', 10), target]
        with patch.object(ui, 'resolve_hf_selection', return_value=('owner/model', 'a'*40, others, target.name)) as resolve:
            self.dialog.repo.setText(link)
            self.dialog.find.click()
            self.assertIn('Reading Hugging Face', self.dialog.status.text())
            self.run_worker()
        resolve.assert_called_once_with(link, 'main', '')
        self.assertEqual(self.dialog._selection(), [target])
        self.assertTrue(self.dialog.download.isEnabled())
        self.assertTrue(self.dialog.install.isEnabled())
        self.dialog.install.setChecked(True)
        self.assertIn('Download and import', self.dialog.download.text())

    def test_failed_import_leaves_download_folder_accessible(self):
        file = ModelFile('model.gguf', 20)
        folder = self.root / 'downloads'
        folder.mkdir()
        with patch.object(ui, 'download_files', return_value=(folder, [folder / file.name])), patch.object(
            ui, 'require_model_runtime', side_effect=ConnectionError('No compatible Ollama runtime.'),
        ):
            self.dialog._save_files('owner/model', 'a'*40, [file], '', True)
            self.run_worker()
        self.assertIn('Ollama import failed', self.dialog.status.text())
        self.assertTrue(self.dialog.folder.isEnabled())
        self.selected.assert_not_called()

    def test_delete_targets_selected_entry_only_after_confirmation(self):
        self.dialog._show_installed(['fixture:latest', 'keep:latest'])
        self.dialog.installed.setCurrentRow(0)
        with patch.object(QMessageBox, 'exec', return_value=QMessageBox.Cancel), patch.object(ui, 'delete_local_model') as remove:
            self.dialog.delete_installed.click()
        self.threads.assert_not_called()
        remove.assert_not_called()
        with patch.object(QMessageBox, 'exec', return_value=QMessageBox.Yes), patch.object(
            ui, 'delete_local_model', return_value='fixture:latest',
        ) as remove:
            self.dialog.delete_installed.click()
            self.assertIn('Removing fixture:latest', self.dialog.status.text())
            self.run_worker()
        self.assertEqual(remove.call_args.args[0], 'fixture:latest')
        self.removed.assert_called_once_with('fixture:latest')
        self.assertEqual(self.dialog.installed.count(), 1)
        self.assertEqual(self.dialog.installed.item(0).text(), 'keep:latest')


if __name__ == '__main__':
    unittest.main()
