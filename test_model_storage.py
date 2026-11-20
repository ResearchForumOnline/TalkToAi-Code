"""Preservation and concurrency regressions for Studio model storage maintenance."""
import os
from pathlib import Path
import tempfile
import time
import unittest

import model_storage as storage


class ModelStorageTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.root = self.home / 'models'
        self.root.mkdir()
        self.work = self.root / '.partial'
        self.work.mkdir()
        self.old = time.time() - 31 * 86400

    def partial(self, name, old=True):
        path = self.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'abc')
        if old:
            os.utime(path, (self.old, self.old))
        return path

    def test_auto_cleanup_respects_active_lock_retention_and_unknown_names(self):
        legacy = self.partial('a' * 64 + '/' + 'c' * 64)
        abandoned = self.partial('d'*32 + '.part')
        recent = self.partial('e'*32 + '.part', False)
        unrelated = self.partial('my-custom-model.gguf')
        export = self.root / 'keep.gguf'
        export.write_bytes(b'GGUF')
        with storage.storage_lock(storage.native_path(self.root)):
            self.assertEqual(storage.clean_stale_partials(self.root), 0)
        self.assertEqual(storage.clean_stale_partials(self.root), 6)
        self.assertFalse(legacy.exists())
        self.assertFalse(abandoned.exists())
        self.assertTrue(recent.is_file())
        self.assertTrue(unrelated.is_file())
        self.assertTrue(export.is_file())

    def test_manual_cache_removal_ignores_new_or_changed_downloads(self):
        old = self.partial('a'*32 + '.part')
        changed = self.partial('b'*32 + '.part')
        reviewed = storage.partial_inventory(self.root)
        changed.write_bytes(b'new bytes')
        later = self.partial('c'*32 + '.part')
        self.assertEqual(storage.clear_partials(self.root, reviewed), 1)
        self.assertFalse(old.exists())
        self.assertTrue(changed.is_file())
        self.assertTrue(later.is_file())

    def test_cleanup_skips_external_linked_directory(self):
        external = self.home / 'external'
        external.mkdir()
        other = external / ('c'*64)
        other.write_bytes(b'preserve')
        os.utime(other, (self.old, self.old))
        try:
            (self.work / ('a'*64)).symlink_to(external, target_is_directory=True)
        except OSError as exc:
            self.skipTest(str(exc))
        self.assertEqual(storage.clean_stale_partials(self.root), 0)
        self.assertTrue(other.is_file())

    def test_export_review_detects_changes_and_confines_scan_to_revision_folders(self):
        a = self.root / 'owner' / 'repo' / ('d'*40) / 'weights'
        a.mkdir(parents=True)
        b = self.root / 'model' / ('a'*40)
        b.mkdir(parents=True)
        (self.root / 'other.gguf').write_bytes(b'keep')
        weight = a / 'q4.gguf'
        weight.write_bytes(b'GGUFfixture')
        (b / 'config.json').write_bytes(b'{}')
        files, capped = storage.saved_exports(self.root)
        self.assertFalse(capped)
        self.assertEqual(len(files), 2)
        self.assertTrue(files[0].relative.endswith('q4.gguf'))
        weight.write_bytes(b'GGUFchanged!')
        with self.assertRaisesRegex(ValueError, 'changed'):
            storage.remove_export(self.root, files[0])
        self.assertTrue(weight.is_file())
        storage.remove_export(self.root, files[1])
        self.assertFalse((b / 'config.json').exists())

    def test_cleanup_rejects_redirected_model_library_root(self):
        other = self.partial('b'*32 + '.part')
        redirected = self.home / 'other-library'
        try:
            redirected.symlink_to(self.root, target_is_directory=True)
        except OSError as exc:
            self.skipTest(str(exc))
        self.assertEqual(storage.clean_stale_partials(redirected), 0)
        self.assertTrue(other.is_file())
        with self.assertRaisesRegex(ValueError, 'redirected'):
            storage.saved_exports(redirected)
