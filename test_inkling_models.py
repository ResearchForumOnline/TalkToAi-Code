import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import inkling_models as models
from model_library import DownloadCancelled, ModelFile
from model_storage import library_root, remove_export, saved_exports


def _sha(data):
    return hashlib.sha256(data).hexdigest()


_HEADER = b'GGUF compatible header'
_TRAILER = b'new queries'
_LEGACY = b'GGUFold+A+queries+B'
_MAINLINE = _HEADER + b'A' + b'B' + _TRAILER
_PRESET = models.InklingPreset(
    'Inkling Test',
    ModelFile('Test-Inkling.gguf', len(_LEGACY), _sha(_LEGACY)),
    ModelFile('Mainline_Support/Test-Inkling.gguf_file', len(_MAINLINE), _sha(_MAINLINE)),
    _sha(_HEADER),
    ((_LEGACY.index(b'A'), 1), (_LEGACY.index(b'B'), 1)),
)


class InklingModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'models'
        self.cancel = threading.Event()

    @staticmethod
    def _notify(*args):
        pass

    @staticmethod
    def _ranges(file, start, size, digest, cancel):
        block = _MAINLINE[start:start + size]
        if _sha(block) != digest:
            raise AssertionError('The fixture range does not match its publisher checksum.')
        return block

    def _fixture(self):
        settings = patch.multiple(models, INKLING_PRESETS={'q8': _PRESET},
                                  _HEADER_SIZE=len(_HEADER), _TRAILER_SIZE=len(_TRAILER),
                                  _TRAILER_SHA=_sha(_TRAILER))
        settings.start()
        self.addCleanup(settings.stop)

    def test_built_in_files_and_transfer_spans_match_pinned_formats(self):
        for key, preset in models.INKLING_PRESETS.items():
            with self.subTest(key=key):
                total = models._HEADER_SIZE + models._TRAILER_SIZE
                for offset, length in preset.legacy_spans:
                    total += length
                    self.assertLessEqual(offset + length, preset.legacy.size)
                self.assertEqual(total, preset.mainline.size)
                self.assertEqual(models.preset_for_hub(models.INKLING_REPO, [preset.legacy]), key)
                self.assertEqual(models.preset_for_hub(models.INKLING_REPO, [preset.mainline]), key)
                self.assertEqual(models.preset_for_path(preset.legacy.name), key)
                self.assertEqual(models.preset_for_path(preset.mainline.name), key)
                self.assertIsNone(models.preset_for_hub('another/repo', [preset.legacy]))

    def test_original_is_reused_and_compatible_copy_is_verified(self):
        self._fixture()
        source = Path(self.temp.name) / _PRESET.legacy.name
        source.write_bytes(_LEGACY)
        with patch.object(models, '_read_range', side_effect=self._ranges) as ranges, \
                patch.object(models, 'download_files') as download:
            target = models.prepare_inkling(self.root, 'q8', self.cancel, self._notify, source=source)
            self.assertEqual(target.read_bytes(), _MAINLINE)
            self.assertEqual(target.suffix, '.gguf')
            self.assertEqual(source.read_bytes(), _LEGACY)
            self.assertEqual(models.prepared_path(self.root, 'q8'), target)
            self.assertEqual(models.prepare_inkling(self.root, 'q8', self.cancel, self._notify), target)
            self.assertEqual(ranges.call_count, 2)
            download.assert_not_called()
        target.write_bytes(_MAINLINE[:-1] + b'?')
        self.assertIsNone(models.prepared_path(self.root, 'q8'))
        saved, _ = saved_exports(self.root)
        remove_export(self.root, next(item for item in saved if item.relative.endswith(target.name)))
        self.assertFalse(target.exists())
        self.assertEqual(source.read_bytes(), _LEGACY)

    def test_copy_with_bad_tensor_is_not_published(self):
        self._fixture()
        source = Path(self.temp.name) / _PRESET.legacy.name
        source.write_bytes(_LEGACY.replace(b'A', b'X'))
        with patch.object(models, '_read_range', side_effect=self._ranges):
            with self.assertRaisesRegex(ValueError, 'publisher checksum'):
                models.prepare_inkling(self.root, 'q8', self.cancel, self._notify, source=source)
        self.assertEqual(source.read_bytes(), _LEGACY.replace(b'A', b'X'))
        self.assertIsNone(models.prepared_path(self.root, 'q8'))
        self.assertEqual(list((self.root / '.partial').glob('*.part')), [])
        self.assertFalse(list(self.root.rglob('*.gguf')))

    def test_conversion_cancelled_midcopy_keeps_original_and_removes_stage(self):
        self._fixture()
        source = Path(self.temp.name) / _PRESET.legacy.name
        source.write_bytes(_LEGACY)

        def stop_on_copy(message, received, total):
            if message.startswith('Preparing'):
                self.cancel.set()

        with patch.object(models, '_read_range', side_effect=self._ranges):
            with self.assertRaises(DownloadCancelled):
                models.prepare_inkling(self.root, 'q8', self.cancel, stop_on_copy, source=source)
        self.assertEqual(source.read_bytes(), _LEGACY)
        self.assertIsNone(models.prepared_path(self.root, 'q8'))
        self.assertEqual(list((self.root / '.partial').glob('*.part')), [])

    def test_first_download_uses_verified_mainline_manifest(self):
        self._fixture()
        root = library_root(self.root)
        _, remote, _ = models._paths(root, _PRESET)

        def download(repo, revision, files, folder, token, cancel, notify):
            self.assertEqual((repo, revision, files, folder, token),
                             (models.INKLING_REPO, models.INKLING_REVISION, [_PRESET.mainline], root, ''))
            remote.parent.mkdir(parents=True)
            remote.write_bytes(_MAINLINE)
            return remote.parent, [remote]

        with patch.object(models, 'download_files', side_effect=download) as shared_downloader, \
                patch.object(models, '_read_range') as ranges:
            target = models.prepare_inkling(self.root, 'q8', self.cancel, self._notify)
            shared_downloader.assert_called_once()
            ranges.assert_not_called()
        self.assertEqual(target.read_bytes(), _MAINLINE)
        self.assertFalse(remote.exists())
        self.assertEqual(models.prepared_path(self.root, 'q8'), target)

    def test_range_requires_exact_public_blobs(self):
        size = len(_HEADER)
        reply = BytesIO(_HEADER)
        reply.status = 206
        reply.headers = {'Content-Range': f'bytes 0-{size - 1}/{_PRESET.mainline.size}'}
        with patch.object(models, 'urlopen', return_value=reply) as opener:
            self.assertEqual(models._read_range(_PRESET.mainline, 0, size,
                                               _sha(_HEADER), self.cancel), _HEADER)
        request, = opener.call_args.args
        self.assertIn(models.INKLING_REVISION, request.full_url)
        self.assertEqual(request.get_header('Range'), f'bytes=0-{size - 1}')
        reply = BytesIO(_HEADER)
        reply.status = 200
        reply.headers = {}
        with patch.object(models, 'urlopen', return_value=reply):
            with self.assertRaisesRegex(ValueError, 'exact Inkling compatibility range'):
                models._read_range(_PRESET.mainline, 0, size, _sha(_HEADER), self.cancel)


if __name__ == '__main__':
    unittest.main()
