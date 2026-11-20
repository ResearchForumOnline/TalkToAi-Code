import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile

from asset_library import PACKS, install_pack


class GameAssetTests(unittest.TestCase):
    def test_install_verifies_archive_and_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'source.zip'
            with zipfile.ZipFile(archive, 'w') as source:
                source.writestr('Assets/icon.png', b'fixture')
                source.writestr('License.txt', b'CC0')
            payload = archive.read_bytes()
            pack = {'slug': 'test-pack', 'title': 'Test Pack', 'bytes': len(payload),
                    'sha256': hashlib.sha256(payload).hexdigest()}
            count = install_pack(root, pack, threading.Event(), local_archive=archive)
            self.assertEqual(count, 2)
            target = root / 'assets/talktoai-library/test-pack'
            self.assertEqual((target / 'Assets/icon.png').read_bytes(), b'fixture')
            self.assertTrue((target / 'talktoai-asset-source.json').is_file())
            with self.assertRaises(FileExistsError):
                install_pack(root, pack, threading.Event(), local_archive=archive)
            with self.assertRaisesRegex(ValueError, 'checksum'):
                install_pack(root, dict(pack, slug='bad', sha256='0'*64),
                             threading.Event(), local_archive=archive)
            self.assertFalse((target.parent / 'bad').exists())

    def test_dialog_opens_and_enables_install_only_with_selection(self):
        from PySide6.QtWidgets import QApplication
        from asset_library_dialog import AssetLibraryDialog
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            dialog = AssetLibraryDialog(None, directory)
            self.assertEqual(dialog.listing.count(), len(PACKS))
            self.assertFalse(dialog.install.isEnabled())
            dialog.listing.setCurrentRow(0)
            self.assertTrue(dialog.install.isEnabled())
            self.assertTrue(dialog.import_zip.isEnabled())
            dialog.reject()
            app.processEvents()
