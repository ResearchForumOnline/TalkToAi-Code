"""Verify selection and staging of the two pinned CUDA archives."""

import hashlib
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

import inkling_cuda
import inkling_runtime


def _zip(names):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name in names:
            archive.writestr(name, f'fixture {name}'.encode())
    return stream.getvalue()


class InklingCudaTests(unittest.TestCase):
    def test_only_frozen_windows_app_directories_supply_crt_fallbacks(self):
        with tempfile.TemporaryDirectory() as location:
            folder = Path(location).resolve()
            for name in ('flat', 'onedir'):
                with self.subTest(layout=name):
                    app = folder / name
                    root = app / '_internal' if name == 'onedir' else app
                    qt = root / 'PySide6'
                    qt.mkdir(parents=True)
                    for target in (root / 'vcruntime140.dll', root / 'vcruntime140_1.dll', qt / 'msvcp140.dll'):
                        target.touch()
                    with patch.object(inkling_cuda.sys, 'platform', 'win32'), \
                            patch.object(inkling_cuda.sys, 'executable', str(app / 'TalkToAiCode.exe')), \
                            patch.object(inkling_cuda.sys, '_MEIPASS', str(root), create=True):
                        with patch.object(inkling_cuda.sys, 'frozen', False, create=True):
                            self.assertEqual(inkling_cuda.bundled_crt_paths(), ())
                        with patch.object(inkling_cuda.sys, 'frozen', True, create=True):
                            self.assertEqual(inkling_cuda.bundled_crt_paths(), (str(root), str(qt)))
                            with patch.object(inkling_cuda.sys, '_MEIPASS', str(folder)):
                                self.assertEqual(inkling_cuda.bundled_crt_paths(), ())
                            (root / 'vcruntime140_1.dll').unlink()
                            self.assertEqual(inkling_cuda.bundled_crt_paths(), (str(qt),))

    def test_crt_fallback_rejects_a_windows_path_separator_inside_the_directory(self):
        with tempfile.TemporaryDirectory() as location:
            app = Path(location).resolve() / f'App{os.pathsep}Folder'
            app.mkdir()
            (app / 'vcruntime140.dll').touch()
            (app / 'vcruntime140_1.dll').touch()
            with patch.object(inkling_cuda.sys, 'platform', 'win32'), \
                    patch.object(inkling_cuda.sys, 'frozen', True, create=True), \
                    patch.object(inkling_cuda.sys, 'executable', str(app / 'TalkToAiCode.exe')), \
                    patch.object(inkling_cuda.sys, '_MEIPASS', str(app), create=True):
                self.assertEqual(inkling_cuda.bundled_crt_paths(), ())

    def test_driver_version_retries_only_when_driver_requests_initialization(self):
        with tempfile.TemporaryDirectory() as location:
            driver = Path(location) / 'System32' / 'nvcuda.dll'
            driver.parent.mkdir()
            driver.touch()
            for results, init_result, expected, retries in (
                ([0], 0, 13050, 0),
                ([100], 0, 0, 0),
                ([3, 0], 0, 13050, 1),
                ([3, 3], 0, 0, 1),
                ([3], 803, 0, 1),
            ):
                with self.subTest(results=results, init_result=init_result):
                    outcomes = iter(results)

                    def query(pointer):
                        status = next(outcomes)
                        if status == 0:
                            pointer._obj.value = 13050
                        return status

                    api = Mock(cuDriverGetVersion=Mock(side_effect=query), cuInit=Mock(return_value=init_result))
                    with patch.object(inkling_cuda.sys, 'platform', 'win32'), \
                            patch.dict(inkling_cuda.os.environ, {'SYSTEMROOT': location}), \
                            patch.object(inkling_cuda.ctypes, 'WinDLL', return_value=api, create=True) as load_library:
                        self.assertEqual(inkling_cuda._driver_version(), expected)
                    load_library.assert_called_once_with(str(driver), winmode=0x00000800)
                    self.assertEqual(api.cuDriverGetVersion.call_count, len(results))
                    if retries:
                        api.cuInit.assert_called_once_with(0)
                    else:
                        api.cuInit.assert_not_called()

    def test_driver_selection_and_no_gpu_skips_provisioning(self):
        for version, sdk in ((11990, None), (12000, '12.4'), (12080, '12.4'), (13000, '13.5'), (14000, '13.5')):
            self.assertEqual(inkling_cuda._sdk_for_driver(version), sdk)
        with tempfile.TemporaryDirectory() as location, \
                patch.object(inkling_runtime, 'supported', return_value=True), \
                patch.object(inkling_cuda, '_driver_version', return_value=0), \
                patch.object(inkling_runtime, '_download_pinned_archive') as download:
            self.assertIsNone(inkling_cuda.cuda_runner(location, threading.Event(), Mock()))
            download.assert_not_called()
            self.assertEqual(list(Path(location).iterdir()), [])

    def test_parts_are_verified_cached_and_repaired(self):
        engine = _zip({
            inkling_runtime._SERVER, *inkling_runtime._DLLS, 'ggml-cuda.dll',
            'ggml-cpu-x64.dll', '../unrelated.dll', 'unrequested.exe',
        })
        deps = _zip({'cublas64_13.dll', 'cublasLt64_13.dll', 'cudart64_13.dll'})
        payloads = {'runner.zip': engine, 'runtime.zip': deps}
        pins = tuple((name, len(payload), hashlib.sha256(payload).hexdigest()) for name, payload in payloads.items())

        def download_fixture(target, name, size, digest, *_args, **_kwargs):
            content = payloads[name]
            self.assertEqual((size, digest), (len(content), hashlib.sha256(content).hexdigest()))
            target.write_bytes(content)

        with tempfile.TemporaryDirectory() as location, \
                patch.object(inkling_runtime, 'supported', return_value=True), \
                patch.object(inkling_cuda, '_driver_version', return_value=13050), \
                patch.dict(inkling_cuda.CUDA_ARCHIVES, {'13.5': pins}), \
                patch.object(inkling_runtime, '_download_pinned_archive', side_effect=download_fixture) as download:
            token = threading.Event()
            server = inkling_cuda.cuda_runner(location, token, Mock())
            self.assertEqual(server.read_bytes(), b'fixture llama-server.exe')
            self.assertTrue((server.parent / 'cudart64_13.dll').is_file())
            self.assertFalse((server.parent / 'unrequested.exe').exists())
            self.assertFalse((server.parent.parent / 'unrelated.dll').exists())
            self.assertEqual(inkling_cuda.cuda_runner(location, token, Mock()), server)
            library = server.parent / 'ggml-cuda.dll'
            library.write_bytes(b'corrupt')
            self.assertEqual(inkling_cuda.cuda_runner(location, token, Mock()), server)
            self.assertEqual(library.read_bytes(), b'fixture ggml-cuda.dll')
            self.assertEqual(download.call_count, 2)

    def test_bundle_rejects_a_driver_in_the_runtime_folder(self):
        with zipfile.ZipFile(io.BytesIO(_zip({'nvcuda.dll'}))) as archive:
            with self.assertRaisesRegex(ValueError, 'Invalid Inkling CUDA runtime member'):
                inkling_cuda._bundle_members([archive], '12.4', 'llama-server.exe', set())

    def test_promotion_retries_a_transient_windows_sharing_violation(self):
        source, destination, cancel = Mock(), Mock(), Mock()
        cancel.is_set.return_value = False
        locked = PermissionError('A scanner is using the file.')
        locked.winerror = 32
        source.replace.side_effect = [locked, None]
        inkling_cuda._move_engine(source, destination, cancel)
        self.assertEqual(source.replace.call_count, 2)
        cancel.wait.assert_called_once_with(.25)
