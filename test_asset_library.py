import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from asset_library import PACKS, find_game_assets, install_pack


class GameAssetTests(unittest.TestCase):
    def test_assets_are_discoverable_through_each_engines_resource_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'source.zip'
            with zipfile.ZipFile(archive, 'w') as source:
                source.writestr('Models/Bridge piece.glb', b'fixture')
                source.writestr('Models/Bridge piece.fbx', b'fixture')
                source.writestr('License.txt', b'CC0')
            payload = archive.read_bytes()
            pack = {'slug': 'test-pack', 'title': 'Test Pack', 'bytes': len(payload),
                    'sha256': hashlib.sha256(payload).hexdigest()}
            unity = root / 'unity'
            (unity / 'ProjectSettings').mkdir(parents=True)
            (unity / 'ProjectSettings/ProjectVersion.txt').write_text('m_EditorVersion: 6000.3.1f1\n')
            godot = root / 'godot'
            godot.mkdir()
            (godot / 'project.godot').touch()
            with patch('asset_library.PACKS', (pack,)):
                for project in (godot, unity):
                    install_pack(project, pack, threading.Event(), local_archive=archive)
                godot_item = find_game_assets(godot,'.glb')['assets'][0]
                self.assertEqual(godot_item['engine_path'], 'res://assets/talktoai-library/test-pack/Models/Bridge piece.glb')
                blender_item = find_game_assets(godot,'.glb','blender')['assets'][0]
                self.assertTrue((godot / blender_item['engine_path']).is_file())
                unity_result = find_game_assets(unity,'bridge')
                self.assertEqual(len(unity_result['assets']),1)
                unity_item = unity_result['assets'][0]
                self.assertEqual(unity_item['engine_path'], 'Assets/talktoai-library/test-pack/Models/Bridge piece.fbx')
                self.assertIn('Assets', [file.name for file in unity.iterdir()])
                self.assertTrue((unity / unity_item['engine_path']).is_file())

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
