import atexit
import hashlib
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

import inkling_runtime as runtime


def _archive():
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        for name in sorted({runtime._SERVER, *runtime._DLLS, 'ggml-cpu-x64.dll'}):
            archive.writestr(name, ('verified ' + name).encode())
        archive.writestr('../outside.dll', b'not a runtime entry')
    return data.getvalue()


class InklingRuntimeTests(unittest.TestCase):
    def setUp(self):
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        self.root = Path(state.name)
        self.manager = runtime.InklingRuntime(self.root)
        self.addCleanup(atexit.unregister, self.manager.stop)
        self.addCleanup(self.manager.stop)
        self.cancel = threading.Event()
        self.notify = Mock()

    def test_pinned_archive_reused_and_altered_runtime_repaired_without_downloading_again(self):
        package = _archive()
        with (patch.object(runtime, 'supported', return_value=True),
              patch.multiple(runtime, ENGINE_BYTES=len(package), ENGINE_SHA256=hashlib.sha256(package).hexdigest()),
              patch.object(runtime, '_download_engine', side_effect=lambda target, *_: target.write_bytes(package)) as download):
            engine = self.manager.ensure_engine(self.cancel, self.notify)
            self.assertEqual(engine.read_bytes(), b'verified llama-server.exe')
            self.assertEqual(engine, self.manager.ensure_engine(self.cancel, self.notify))
            (engine.parent / 'ggml.dll').write_bytes(b'tampered')
            (engine.parent / 'unverified.dll').write_bytes(b'additional')
            self.assertEqual(engine, self.manager.ensure_engine(self.cancel, self.notify))
        self.assertEqual((engine.parent / 'ggml.dll').read_bytes(), b'verified ggml.dll')
        self.assertFalse((engine.parent / 'unverified.dll').exists())
        self.assertFalse((engine.parent.parent / 'outside.dll').exists())
        download.assert_called_once()

    def test_runner_download_rejects_truncated_or_wrong_digest(self):
        for content, size in ((b'prefix', 7), (b'bad', 3)):
            with self.subTest(content=content):
                target = self.root / (content.decode() + '.zip')
                fake_opener = Mock()
                fake_opener.open.return_value = io.BytesIO(content)
                with (patch.object(runtime, 'build_opener', return_value=fake_opener),
                      patch.multiple(runtime, ENGINE_BYTES=size, ENGINE_SHA256='0' * 64)):
                    with self.assertRaisesRegex(ValueError, 'size or SHA-256'):
                        runtime._download_engine(target, self.cancel, self.notify)

    def test_early_cancellation_avoids_downloading_or_running_an_engine(self):
        self.cancel.set()
        with (patch.object(runtime, 'supported', return_value=True),
              patch.object(runtime, '_download_engine') as download):
            with self.assertRaises(runtime.DownloadCancelled):
                self.manager.ensure_engine(self.cancel, self.notify)
            download.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_launch_authenticates_only_loopback_and_reuses_same_weights(self):
        weights = self.root / 'inkling.gguf'
        weights.write_bytes(b'GGUFverified model')
        process = Mock()
        process.poll.return_value = None
        with (patch.object(self.manager, 'ensure_engine', return_value=self.root / runtime._SERVER) as ensure,
              patch.object(runtime, '_choose_port', return_value=40200),
              patch.object(runtime, '_ready', return_value=True) as ready,
              patch.dict(runtime.os.environ, {'OPENAI_API_KEY': 'fixture', 'LLAMA_ARG_AGENT': '1'}),
              patch.object(runtime.subprocess, 'Popen', return_value=process) as spawn):
            url, key = self.manager.start(weights, self.cancel, self.notify)
            self.assertEqual((url, key), self.manager.start(weights, self.cancel, self.notify))
            self.assertTrue(self.manager.running(weights))
            self.manager.stop()
        self.assertEqual(url, 'http://127.0.0.1:40200/v1')
        self.assertTrue(key)
        arguments = spawn.call_args.args[0]
        self.assertEqual(arguments[arguments.index('--host') + 1], '127.0.0.1')
        self.assertIn('--jinja', arguments)
        self.assertIn('--no-agent', arguments)
        self.assertEqual(arguments[arguments.index('--ctx-size') + 1], '8192')
        self.assertEqual(arguments[arguments.index('--reasoning-effort') + 1], 'none')
        self.assertNotIn(key, arguments)
        self.assertEqual(spawn.call_args.kwargs['env']['LLAMA_API_KEY'], key)
        self.assertNotIn('OPENAI_API_KEY', spawn.call_args.kwargs['env'])
        self.assertNotIn('LLAMA_ARG_AGENT', spawn.call_args.kwargs['env'])
        ready.assert_called_once_with(40200, key)
        spawn.assert_called_once()
        ensure.assert_called_once()
        process.terminate.assert_called_once()
        self.assertFalse(self.manager.running())

    def test_stop_while_load_is_pending_terminates_only_owned_process(self):
        weights = self.root / 'inkling.gguf'
        weights.write_bytes(b'GGUF')
        process = Mock()
        process.poll.return_value = None

        def stop_during_health(*_):
            self.cancel.set()
            return False

        with (patch.object(self.manager, 'ensure_engine', return_value=self.root / runtime._SERVER),
              patch.object(runtime, '_ready', side_effect=stop_during_health),
              patch.object(runtime.subprocess, 'Popen', return_value=process)):
            with self.assertRaises(runtime.DownloadCancelled):
                self.manager.start(weights, self.cancel, self.notify)
        process.terminate.assert_called_once()
        self.assertFalse(self.manager.running())

    def test_early_runner_failure_returns_actionable_sanitised_error(self):
        weights = self.root / 'inkling.gguf'
        weights.write_bytes(b'GGUF')

        def failed_runner(*args, **kwargs):
            kwargs['stdout'].write(b'some private path: error loading model: unknown model architecture: inkling2')
            kwargs['stdout'].flush()
            process = Mock()
            process.poll.return_value = 1
            return process

        with (patch.object(self.manager, 'ensure_engine', return_value=self.root / runtime._SERVER),
              patch.object(runtime.subprocess, 'Popen', side_effect=failed_runner)):
            with self.assertRaisesRegex(RuntimeError, 'Mainline Support') as caught:
                self.manager.start(weights, self.cancel, self.notify)
        self.assertNotIn('private path', str(caught.exception))

    def test_healthy_port_must_return_authentic_model_alias(self):
        with patch.object(runtime, '_get_local_json', side_effect=[{'status': 'ok'}, {'data': [{'id': 'other'}]}]):
            self.assertFalse(runtime._ready(40000, 'local-key'))
        with patch.object(runtime, '_get_local_json', side_effect=[{'status': 'ok'}, {'data': [{'id': runtime.INKLING_MODEL}]}]) as request:
            self.assertTrue(runtime._ready(40000, 'local-key'))
        self.assertEqual([call.args for call in request.call_args_list],
                         [(40000, '/health', 'local-key'), (40000, '/v1/models', 'local-key')])

    def test_unexpected_runtime_redirect_does_not_leave_https_github_hosts(self):
        redirect = runtime._ReleaseRedirect()
        for address in ('http://github.com/asset', 'https://localhost/asset',
                        'https://user@github.com/asset', 'https://github.com:444/asset'):
            with self.subTest(address=address), self.assertRaises(ConnectionError):
                redirect.redirect_request(None, None, 302, '', {}, address)


if __name__ == '__main__':
    unittest.main()
