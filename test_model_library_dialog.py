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

    def test_test_reply_checks_only_the_selected_model(self):
        self.dialog._show_installed(['first:latest', 'second:latest'])
        self.dialog.installed.setCurrentRow(1)
        response={'model':'second:latest','outcome':'verified','digest':'a'*64,'elapsed_seconds':1.0,
                  'saved':True,'preview':'5','note':''}
        with patch('runtime_diagnostics.measure_local_reply', return_value=response) as check:
            self.dialog.test_reply.click()
            self.assertTrue(self.dialog.stop.isEnabled())
            self.assertFalse(self.dialog.test_reply.isEnabled())
            self.run_worker()
        self.assertEqual(check.call_args.args[0], 'second:latest')
        self.assertEqual(check.call_args.kwargs['history_dir'],self.root/'.reply-checks-v1')
        self.assertIn('second:latest', self.dialog.status.text())
        self.assertIn('Verified the expected answer (5)',self.dialog.status.text())
        self.assertIn('Reply: 5',self.dialog.status.text())
        self.selected.assert_not_called()

    def test_reopened_installed_list_shows_check_for_exact_digest_and_aliases_only(self):
        digest='a'*64
        records=[{'name':'mine:latest','digest':digest}]
        self.dialog._finished(('inventory',records))
        self.assertIn('No saved reply check',self.dialog.reply_history.text())
        with patch('routing.require_local_runtime'), patch('routing.installed_model_records',return_value=records), patch('agent_core._http_stream',return_value=iter([{'message':{'content':'five, actually'},'done':True}])):
            self.dialog.test_reply.click()
            self.run_worker()
        self.assertIn('Answered differently',self.dialog.reply_history.text())
        self.assertIn('five, actually',self.dialog.status.text())
        reopened=ui.ModelLibraryDialog(None,self.root,self.selected,self.removed,Mock(),Mock(),Mock())
        try:
            reopened._finished(('inventory',[{'name':'alternate:latest','digest':'sha256:'+digest},
                                              {'name':'mine:latest','digest':'b'*64}]))
            self.assertEqual(reopened._installed_name(),'alternate:latest')
            self.assertIn('Last reply check',reopened.reply_history.text())
            self.assertIn('Answered differently',reopened.reply_history.text())
            self.assertRegex(reopened.reply_history.text(),r'\d{4}-\d\d-\d\d \d\d:\d\d')
            self.assertNotIn('five, actually',reopened.reply_history.text())
            reopened.installed.setCurrentRow(1)
            self.assertIn('No saved reply check',reopened.reply_history.text())
        finally:
            reopened.close()
            reopened.deleteLater()

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

    def test_storage_review_removes_only_confirmed_export(self):
        folder = self.root / 'owner' / 'model' / ('a' * 40)
        folder.mkdir(parents=True)
        selected = folder / 'qwen.gguf'
        retained = folder / 'metadata.json'
        selected.write_bytes(b'GGUFsample')
        retained.write_bytes(b'{}')
        self.dialog.tabs.setCurrentIndex(3)
        self.run_worker()
        self.assertEqual(self.dialog.exports.count(), 2)
        self.dialog.exports.setCurrentRow(0)
        with patch.object(QMessageBox, 'exec', return_value=QMessageBox.Cancel):
            self.dialog.remove_saved.click()
        self.assertTrue(selected.is_file())
        with patch.object(QMessageBox, 'exec', return_value=QMessageBox.Yes):
            self.dialog.remove_saved.click()
            self.run_worker()
        self.assertFalse(selected.exists())
        self.assertTrue(retained.is_file())
        self.assertEqual(self.dialog.exports.count(), 1)

    def test_ollama_aliases_and_configured_names_are_labelled(self):
        self.dialog.configured_models = {'Local': 'small'}
        self.dialog._finished(('inventory', [{'name':'small:latest','size':1024,'digest':'a'*64},
                                             {'name':'copy:latest','size':1024,'digest':'a'*64}]))
        self.assertIn('Local', self.dialog.installed.item(0).text())
        self.assertIn('same model as copy:latest', self.dialog.installed.item(0).text())
        self.assertIn('Not selected in Studio', self.dialog.installed.item(1).text())

    def test_local_gguf_import_uses_user_selected_file_and_persists_local_model(self):
        model = 'talktoai-small:123'
        with patch.object(ui.QFileDialog, 'getOpenFileNames', return_value=(['local.gguf'], '')), \
                patch.object(ui, 'require_model_runtime'), patch.object(ui, 'import_gguf', return_value=model) as importer, \
                patch.object(ui, 'model_present', return_value=True):
            self.dialog._choose_local_gguf()
            self.run_worker()
        self.assertEqual(importer.call_args.args[0], ['local.gguf'])
        self.selected.assert_called_once_with(model, 'local')

    def test_use_saved_split_download_imports_its_siblings_without_redownloading(self):
        folder = self.root / 'owner' / 'model' / ('a' * 40)
        folder.mkdir(parents=True)
        names = ['q4-00001-of-00002.gguf', 'q4-00002-of-00002.gguf']
        for name in names:
            (folder / name).write_bytes(b'GGUFfixture')
        self.dialog.tabs.setCurrentIndex(3)
        self.run_worker()
        self.dialog.exports.setCurrentRow(1)
        self.assertTrue(self.dialog.use_saved.isEnabled())
        with patch.object(ui, 'require_model_runtime'), patch.object(ui, 'model_present', return_value=True), \
                patch.object(ui, 'import_gguf', return_value='talktoai-shards:fixture') as importer, \
                patch.object(ui, 'download_files', side_effect=AssertionError('Unexpected download')):
            self.dialog.use_saved.click()
            self.run_worker()
        self.assertEqual([path.name for path in importer.call_args.args[0]], names)
        self.selected.assert_called_once_with('talktoai-shards:fixture', 'local')

    def test_manual_clear_shows_only_current_review_and_requires_confirmation(self):
        folder = self.root / '.partial'
        folder.mkdir()
        partial = folder / ('c' * 32 + '.part')
        partial.write_bytes(b'fixture')
        unrelated = folder / 'mine.gguf'
        unrelated.write_bytes(b'GGUF')
        self.dialog.tabs.setCurrentIndex(3)
        self.run_worker()
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Cancel):
            self.dialog.clear_cache.click()
        self.assertTrue(partial.is_file())
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            self.dialog.clear_cache.click()
            self.run_worker()
        self.assertFalse(partial.exists())
        self.assertTrue(unrelated.is_file())
        self.assertFalse(self.dialog.clear_cache.isEnabled())


if __name__ == '__main__':
    unittest.main()
