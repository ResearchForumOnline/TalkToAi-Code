"""Regression checks for resumable, immutable model downloads and local selection."""

import hashlib
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, call, patch

import model_library as library


class _Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}


class ModelLibraryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.repo = 'owner/model'
        self.revision = 'a' * 40
        self.cancel = threading.Event()
        self.notify = Mock()

    def test_repository_returns_immutable_revision_and_file_metadata(self):
        raw = json.dumps({'sha': self.revision, 'siblings': [
            {'rfilename': 'weights/Q4.gguf', 'size': 123, 'lfs': {'sha256': 'b' * 64}},
            {'rfilename': 'params.json', 'size': 44, 'blobId': 'c' * 40},
            {'rfilename': '../escape.txt', 'size': 12},
            {'rfilename': 'a/CON.txt', 'size': 12},
        ]}).encode()
        with patch('model_library.urlopen', return_value=_Response(raw)) as net:
            commit, files = library.repository_files('https://huggingface.co/owner/model', 'stable/test', 'read_token')
        path = net.call_args.args[0]
        self.assertIn('/revision/stable%2Ftest?blobs=true', path.full_url)
        self.assertNotIn('Authorization', path.headers)
        self.assertEqual(path.unredirected_hdrs['Authorization'], 'Bearer read_token')
        self.assertEqual(commit, self.revision)
        self.assertEqual([(item.name, item.size) for item in files], [('weights/Q4.gguf', 123), ('params.json', 44)])
        self.assertEqual((files[0].sha256, files[1].git_sha1), ('b' * 64, 'c' * 40))

    def test_cancelled_download_resumes_on_same_commit(self):
        payload = b'GGUF' + b'weights' * 90
        entry = library.ModelFile('bits/Q4.gguf', len(payload), hashlib.sha256(payload).hexdigest())
        current = 0

        def pause(label, completed, total):
            nonlocal current
            current = completed
            if completed:
                self.cancel.set()

        with patch('model_library._CHUNK', 64), patch('model_library.urlopen', return_value=_Response(payload)):
            with self.assertRaises(library.DownloadCancelled):
                library.download_files(self.repo, self.revision, [entry], self.root, '', self.cancel, pause)
        self.assertGreater(current, 0)
        self.assertFalse((self.root / self.repo / self.revision / entry.name).exists())
        self.cancel.clear()
        with patch('model_library.urlopen', return_value=_Response(
            payload[current:], 206, {'Content-Range': f'bytes {current}-{len(payload)-1}/{len(payload)}'},
        )) as net:
            folder, [target] = library.download_files(self.repo, self.revision, [entry], self.root,
                                                       '', self.cancel, self.notify)
        self.assertEqual(net.call_args.args[0].get_header('Range'), f'bytes={current}-')
        self.assertEqual(folder, self.root / self.repo / self.revision)
        self.assertEqual(target.read_bytes(), payload)
        self.assertFalse(any(path.is_file() for path in (self.root / '.partial').rglob('*')))

    def test_wrong_checksum_is_never_published_and_does_not_resume(self):
        payload = b'GGUF-wrong'
        entry = library.ModelFile('Q4.gguf', len(payload), 'e' * 64)
        with patch('model_library.urlopen', return_value=_Response(payload)):
            with self.assertRaisesRegex(ValueError, 'checksum'):
                library.download_files(self.repo, self.revision, [entry], self.root, '', self.cancel, self.notify)
        self.assertEqual(list(self.root.rglob('*.gguf')), [])
        self.assertFalse(any(path.is_file() for path in (self.root / '.partial').rglob('*')))

    def test_non_gguf_git_blob_download(self):
        payload = b'{"model_type":"fixture"}'
        digest = hashlib.sha1(f'blob {len(payload)}\0'.encode() + payload).hexdigest()
        entry = library.ModelFile('config.json', len(payload), git_sha1=digest)
        with patch('model_library.urlopen', return_value=_Response(payload)):
            _, [path] = library.download_files(self.repo, self.revision, [entry], self.root,
                                                '', self.cancel, self.notify)
        self.assertEqual(path.read_bytes(), payload)

    def test_split_gguf_is_expanded_and_non_gguf_stays_download_only(self):
        first = library.ModelFile('q4/MyModel-00001-of-00002.gguf', 4)
        second = library.ModelFile('q4/MyModel-00002-of-00002.gguf', 4)
        config = library.ModelFile('config.json', 2)
        self.assertEqual(library.selected_files([first, second, config], [second]), [first, second])
        self.assertTrue(library.gguf_selection([first, second]))
        self.assertFalse(library.gguf_selection([first, second, config]))
        self.assertFalse(library.gguf_selection([config]))
        with self.assertRaisesRegex(ValueError, 'missing shards'):
            library.selected_files([second], [second])

    def test_ollama_uses_the_exact_model_id_and_requires_success(self):
        def updates():
            yield {'status':'download','digest':'sha256:fixture','total':10,'completed':7}
            yield {'status':'success'}
        with patch('model_library._preflight_pull') as preflight, patch(
            'model_library._ollama_updates', return_value=updates(),
        ) as net:
            library.pull_model(library.QWEN_LOCAL_MODEL, self.cancel, self.notify)
        path, payload, cancel, _ = net.call_args.args
        preflight.assert_called_once_with(library.QWEN_LOCAL_MODEL)
        self.assertEqual(path, '/api/pull')
        self.assertEqual(payload['model'], library.QWEN_LOCAL_MODEL)
        self.assertIs(cancel, self.cancel)
        self.notify.assert_any_call('Download', 7, 10)
        with patch('model_library._preflight_pull'), patch(
            'model_library._ollama_updates', return_value=(item for item in [{'status':'pulling manifest'}]),
        ):
            with self.assertRaisesRegex(ConnectionError, 'confirmed completion'):
                library.pull_model(library.QWEN_LOCAL_MODEL, self.cancel, self.notify)

    def test_cancel_interrupts_silent_local_verification(self):
        release = threading.Event()
        verifying = threading.Event()
        cancel = self.cancel

        class SilentVerification(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                body = b'{"status":"verifying sha256 digest"}\n'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body) + 100))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
                verifying.set()
                release.wait(10)

        def stop_when_verifying():
            if verifying.wait(2):
                cancel.set()

        with ThreadingHTTPServer(('127.0.0.1', 0), SilentVerification) as server:
            started = time.monotonic()
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            stopper = threading.Thread(target=stop_when_verifying, daemon=True)
            stopper.start()
            try:
                with patch('model_library.OLLAMA_URL', f'http://127.0.0.1:{server.server_port}'), patch(
                    'model_library._preflight_pull',
                ):
                    with self.assertRaises(library.DownloadCancelled):
                        library.pull_model(library.QWEN_LOCAL_MODEL, cancel, self.notify, timeout=3)
                self.assertLess(time.monotonic() - started, 2)
            finally:
                release.set()
                server.shutdown()
                worker.join()
                stopper.join()

    def test_only_checksum_verification_gets_long_idle_allowance(self):
        conn = Mock()
        raw = b'{"status":"verifying sha256 digest"}\n{"status":"writing manifest"}\n{"status":"success"}\n'
        conn.getresponse.return_value = _Response(raw)
        with patch('model_library.HTTPConnection', return_value=conn), patch('model_library._preflight_pull'):
            library.pull_model(library.QWEN_LOCAL_MODEL, self.cancel, self.notify, timeout=3)
        self.assertEqual(conn.sock.settimeout.call_args_list, [call(300), call(900), call(300)])

    def test_split_import_registers_names_and_digests(self):
        paths = [self.root / f'weights-{index:05d}-of-00002.gguf' for index in (1, 2)]
        for index, path in enumerate(paths):
            path.write_bytes(b'GGUF' + bytes([index]))
        with patch.object(library._LOCAL_HTTP, 'open', side_effect=lambda *args, **kwargs: _Response(b'')), patch(
            'model_library._ollama_progress',
        ) as register:
            model = library.import_gguf(paths, self.cancel, self.notify)
        endpoint, payload, *_ = register.call_args.args
        self.assertEqual(endpoint, '/api/create')
        self.assertEqual(payload['model'], model)
        self.assertEqual(payload['files'], {path.name: 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})

    def test_low_disk_fails_before_any_network_write(self):
        entry = library.ModelFile('weights.gguf', 100)
        with patch('model_library.shutil.disk_usage', return_value=Mock(free=99)), patch('model_library.urlopen') as net:
            with self.assertRaisesRegex(OSError, 'Insufficient disk space'):
                library.download_files(self.repo, self.revision, [entry], self.root, '', self.cancel, self.notify)
        net.assert_not_called()


if __name__ == '__main__':
    unittest.main()
