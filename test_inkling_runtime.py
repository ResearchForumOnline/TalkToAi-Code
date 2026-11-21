import atexit
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

import inkling_runtime as runtime


def _archive(vulkan=False):
    names = {runtime._SERVER, *runtime._DLLS, 'ggml-cpu-x64.dll'}
    if vulkan:
        names |= runtime._VULKAN_REQUIRED | {'vulkan-1.dll'}
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        for name in sorted(names):
            archive.writestr(name, ('verified ' + name).encode())
        archive.writestr('../outside.dll', b'not a runtime entry')
    return data.getvalue()


def _loop_gpu_log(count, device='CUDA0', *, buffer='1199.52', prefix=True):
    metadata = (
        b'0.02.013.245 I llama_model_loader: - kv   0:                       general.architecture str = inkling2_loop\n'
        b'0.02.013.246 I llama_model_loader: - kv   4:                  inkling2_loop.block_count u32 = 24\n'
        b'0.02.013.247 I print_info: n_layer_all           = 48\n'
    ) if prefix else b''
    return metadata + (
        f'0.02.013.250 I load_tensors: offloaded {count}/49 layers to GPU\n'
        f'0.02.013.251 I load_tensors:       {device} model buffer size = {buffer} MiB\n'
    ).encode()


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
        self.loader = self.enterContext(patch.object(runtime, '_vulkan_loader_available', return_value=False))
        self.cuda = self.enterContext(patch.object(self.manager, '_available_cuda', return_value=None))

    def _gpu_fixture(self, *, free_mib=14000, total_mib=16384):
        self.loader.return_value = True
        cpu = self.root / 'engine' / runtime._SERVER
        gpu = self.root / 'engine-vulkan' / runtime._SERVER
        ensured = self.enterContext(patch.object(self.manager, 'ensure_engine',
            side_effect=lambda *_, vulkan=False: gpu if vulkan else cpu))
        device = runtime._GpuDevice('Vulkan1', 'Fixture GPU', free_mib, total_mib)
        probe = self.enterContext(patch.object(runtime, '_probe_gpu', return_value=device))
        self.enterContext(patch.object(runtime, '_ready', return_value=True))
        weights = self.root / 'inkling.gguf'
        weights.write_bytes(b'GGUF')
        return weights, ensured, probe

    @staticmethod
    def _process(log, contents=b'', exit_code=None):
        log.write(contents)
        log.flush()
        process = Mock()
        process.poll.return_value = exit_code
        return process

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

    def test_vulkan_archive_checks_backend_and_keeps_system_loader_outside_cache(self):
        package = _archive(vulkan=True)
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            with self.assertRaisesRegex(ValueError, 'expected Vulkan backend'):
                runtime._engine_members(archive, vulkan=True)
        with (patch.object(runtime, 'supported', return_value=True),
              patch.multiple(runtime, VULKAN_BYTES=len(package), VULKAN_SHA256=hashlib.sha256(package).hexdigest(),
                             _VULKAN_DLL_BYTES=len(b'verified ggml-vulkan.dll')),
              patch.object(runtime, '_download_engine', side_effect=lambda target, *_: target.write_bytes(package)) as download):
            engine = self.manager.ensure_engine(self.cancel, self.notify, vulkan=True)
            self.assertEqual(engine.parent.name, 'engine-vulkan')
            self.assertEqual((engine.parent / 'ggml-vulkan.dll').read_bytes(), b'verified ggml-vulkan.dll')
            self.assertFalse((engine.parent / 'vulkan-1.dll').exists())
            self.assertEqual(engine, self.manager.ensure_engine(self.cancel, self.notify, vulkan=True))
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
            self.assertEqual(self.manager.status().label, 'CPU')
            self.assertIn('no Vulkan loader', self.manager.status().reason)
            self.manager.stop()
        self.assertEqual(url, 'http://127.0.0.1:40200/v1')
        self.assertTrue(key)
        arguments = spawn.call_args.args[0]
        self.assertEqual(arguments[arguments.index('--host') + 1], '127.0.0.1')
        self.assertIn('--jinja', arguments)
        self.assertIn('--no-agent', arguments)
        self.assertEqual(arguments[arguments.index('--device') + 1], 'none')
        self.assertEqual(arguments[arguments.index('--fit') + 1], 'off')
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
        self.assertIsNone(self.manager.status())

    def test_child_only_environment_adds_verified_bundled_crt_after_system_paths(self):
        crt = (str(self.root / '_internal'), str(self.root / '_internal' / 'PySide6'))
        with (patch.object(runtime, 'bundled_crt_paths', return_value=crt),
              patch.dict(runtime.os.environ, {'SYSTEMROOT': r'C:\Windows', 'PATH': 'user-bin', 'OPENAI_API_KEY': 'fixture'})):
            env = runtime._engine_environment('runner-only-key')
            self.assertEqual(runtime.os.environ['PATH'], 'user-bin')
        self.assertEqual(env['PATH'].split(runtime.os.pathsep), [str(Path(r'C:\Windows') / 'System32'), r'C:\Windows', *crt])
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertEqual(env['LLAMA_API_KEY'], 'runner-only-key')

    def test_log_tail_can_be_reopened_without_moving_the_writing_child_offset(self):
        script = '''
import sys

for chunk in (b'abcdef', b'XYZ', b'q' * (1024 * 1024) + b'END'):
    if not sys.stdin.buffer.read(1):
        break
    sys.stdout.buffer.write(chunk)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(b'+')
    sys.stderr.buffer.flush()
'''
        with tempfile.TemporaryFile(mode='w+b') as log:
            child = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE, stdout=log,
                                     stderr=subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                def write_next():
                    child.stdin.write(b'+')
                    child.stdin.flush()
                    self.assertEqual(child.stderr.read(1), b'+')

                write_next()
                self.assertEqual(runtime._log_tail(log, 4), b'cdef')
                log.seek(2)
                self.assertEqual(runtime._log_tail(log, 4), b'cdef')
                self.assertEqual(log.tell(), 2)

                write_next()
                self.assertEqual(runtime._log_tail(log, 6), b'abXYZf')
                self.assertEqual(log.tell(), 5)

                log.seek(0, 2)
                write_next()
                self.assertEqual(runtime._log_tail(log, 10), b'qqqqqqqEND')
                self.assertEqual(runtime._log_tail(log, 3), b'END')
                self.assertEqual(log.tell(), 6 + runtime._CHUNK + 3)
                self.assertEqual(child.wait(timeout=5), 0)
            finally:
                child.stdin.close()
                child.stderr.close()
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)

    def test_missing_loader_is_cached_when_another_model_is_loaded(self):
        first, second = (self.root / name for name in ('inkling-A.gguf', 'inkling-B.gguf'))
        first.write_bytes(b'GGUF')
        second.write_bytes(b'GGUF')
        with (patch.object(self.manager, 'ensure_engine', return_value=self.root / runtime._SERVER) as ensured,
              patch.object(runtime, '_probe_gpu') as probe,
              patch.object(runtime, '_ready', return_value=True),
              patch.object(runtime.subprocess, 'Popen', side_effect=lambda *a, **kw: self._process(kw['stdout']))):
            self.manager.start(first, self.cancel, self.notify)
            self.manager.start(second, self.cancel, self.notify)
        self.loader.assert_called_once()
        self.cuda.assert_called_once()
        probe.assert_not_called()
        self.assertEqual([call.kwargs for call in ensured.call_args_list], [{}, {}])

    def test_lists_only_real_vulkan_devices_and_prefers_available_memory(self):
        output = b'Available devices:\r\n  (none)\r\n'
        self.assertIsNone(runtime._select_gpu_device(output, 'Vulkan'))
        output = (b'Available devices:\r\n  Vulkan0: llvmpipe (LLVM 20) (65536 MiB, 65000 MiB free)\r\n'
                  b'  CUDA0: CUDA (48000 MiB, 40000 MiB free)\r\n'
                  b'  Vulkan1: Intel(R) Graphics (5000 MiB, 4000 MiB free)\r\n'
                  b'  Vulkan3: Fixture GPU (10000 MiB, 8000 MiB free)\r\n')
        self.assertEqual(runtime._select_gpu_device(output, 'Vulkan'), runtime._GpuDevice('Vulkan3', 'Fixture GPU', 8000, 10000))
        self.assertEqual(runtime._select_gpu_device(output, 'CUDA'), runtime._GpuDevice('CUDA0', 'CUDA', 40000, 48000))

    def test_verified_offload_and_authenticated_generation_select_gpu(self):
        weights, ensured, probe = self._gpu_fixture()
        output = b'load_tensors: offloaded 23/41 layers to GPU\nload_tensors: Vulkan1 model buffer size = 7895.10 MiB\n'
        with (patch.object(runtime, '_probe_inference', return_value=True) as inference,
              patch.object(runtime.subprocess, 'Popen', side_effect=lambda *a, **kw: self._process(kw['stdout'], output)) as spawn):
            endpoint = self.manager.start(weights, self.cancel, self.notify)
            self.assertEqual(endpoint, self.manager.start(weights, self.cancel, self.notify))
            self.assertEqual(endpoint, self.manager.start(weights, self.cancel, self.notify, acceleration='gpu'))
        status = self.manager.status()
        self.assertEqual(status.label, 'GPU · 23/41 layers')
        self.assertEqual(status.backend, 'vulkan')
        self.assertEqual((status.device_name, status.vram_free_mib, status.vram_total_mib), ('Fixture GPU', 14000, 16384))
        self.assertIn('14,000 MiB free of 16,384 MiB', self.manager.diagnostic())
        self.assertEqual(inference.call_args.args[:2], (int(endpoint[0].split(':')[-1].split('/')[0]), endpoint[1]))
        args = spawn.call_args.args[0]
        self.assertEqual(args[args.index('--device') + 1], 'Vulkan1')
        self.assertEqual(args[args.index('--fit') + 1], 'on')
        self.assertEqual(args[args.index('--fit-target') + 1], '1024')
        self.assertEqual(args[args.index('--batch-size') + 1], '256')
        self.assertEqual(args[args.index('--ubatch-size') + 1], '16')
        self.assertEqual(args[args.index('--log-verbosity') + 1], '4')
        self.assertEqual(args[args.index('--flash-attn') + 1], 'off')
        self.assertIn('--no-op-offload', args)
        self.assertNotIn('--n-gpu-layers', args)
        probe.assert_called_once()
        ensured.assert_called_once_with(self.cancel, self.notify, vulkan=True)
        inference.assert_called_once()

    def test_vulkan_without_real_offload_falls_back_and_does_not_retry_for_same_weights(self):
        weights, ensure, probe = self._gpu_fixture()
        output = (b'load_tensors: offloaded 23/41 layers to GPU\nload_tensors: Vulkan1 model buffer size = 7895.10 MiB\n'
                  b'load_tensors: offloaded 0/41 layers to GPU\n')

        def spawn_attempt(command, **kwargs):
            data = output if 'Vulkan1' in command else b''
            return self._process(kwargs['stdout'], data)

        with (patch.object(runtime, '_probe_inference') as inference,
              patch.object(runtime.subprocess, 'Popen', side_effect=spawn_attempt) as spawn):
            self.manager.start(weights, self.cancel, self.notify)
            self.assertEqual(self.manager.status().label, 'CPU')
            self.assertIn('did not offload any verified Inkling transformer blocks', self.manager.status().reason)
            self.manager.stop()
            self.manager.start(weights, self.cancel, self.notify)
        probe.assert_called_once()
        inference.assert_not_called()
        self.assertEqual(spawn.call_count, 4)
        self.assertEqual([call.kwargs for call in ensure.call_args_list], [{'vulkan': True}, {}, {}])

    def test_vulkan_allocation_or_generation_failure_returns_fresh_cpu_session(self):
        weights, ensure, probe = self._gpu_fixture()
        self.notify.side_effect = lambda *_: self.manager.status()

        def spawn_attempt(command, **kwargs):
            if 'Vulkan1' not in command:
                return self._process(kwargs['stdout'])
            if failure == 'allocation':
                return self._process(kwargs['stdout'], b'vk::OutOfDeviceMemoryError\n', 1)
            return self._process(kwargs['stdout'], b'offloaded 3/41 layers to GPU\nVulkan1 model buffer size = 4000.00 MiB\n')

        for failure, reason in (('allocation', 'ran out of available memory'), ('generation', 'generation check')):
            with self.subTest(failure=failure):
                weights.write_bytes(b'GGUF' + failure.encode())
                with (patch.object(runtime, '_probe_inference', return_value=False),
                      patch.object(runtime, '_choose_port', side_effect=[40600, 40601, 40602]),
                      patch.object(runtime.subprocess, 'Popen', side_effect=spawn_attempt) as spawn):
                    url, key = self.manager.start(weights, self.cancel, self.notify)
                self.assertEqual(url, 'http://127.0.0.1:40602/v1')
                self.assertEqual(self.manager.status().backend, 'cpu')
                self.assertIn(reason, self.manager.status().reason)
                self.assertNotEqual(key, spawn.call_args_list[0].kwargs['env']['LLAMA_API_KEY'])
                self.assertEqual(spawn.call_count, 3)
                self.manager.stop()
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(ensure.call_count, 4)

    def test_8_gb_gpu_starts_with_non_looped_physical_blocks_and_keeps_context(self):
        weights, ensure, _ = self._gpu_fixture(free_mib=7202, total_mib=8191)

        def spawn_attempt(command, **kwargs):
            self.assertIn('--cpu-moe', command)
            return self._process(kwargs['stdout'], _loop_gpu_log(31, 'Vulkan1'))

        with (patch.object(runtime, '_probe_inference', return_value=True) as inference,
              patch.object(runtime.subprocess, 'Popen', side_effect=spawn_attempt) as spawn):
            self.manager.start(weights, self.cancel, self.notify, acceleration='gpu')
        spawn.assert_called_once()
        partial = spawn.call_args.args[0]
        self.assertEqual(partial[partial.index('--fit') + 1], 'off')
        self.assertEqual(partial[partial.index('--n-gpu-layers') + 1], '31')
        self.assertEqual(partial[partial.index('--ctx-size') + 1], '8192')
        self.assertEqual(partial[partial.index('--batch-size') + 1], '128')
        self.assertEqual(partial[partial.index('--ubatch-size') + 1], '16')
        self.assertEqual(partial[partial.index('--log-verbosity') + 1], '4')
        self.assertEqual(partial[partial.index('--flash-attn') + 1], 'off')
        self.assertIn('--cpu-moe', partial)
        self.assertIn('--no-kv-offload', partial)
        self.assertIn('--no-op-offload', partial)
        self.assertIn('system RAM', self.manager.diagnostic())
        self.assertEqual(self.manager.status().label, 'GPU · 7/25 layers')
        inference.assert_called_once()
        ensure.assert_called_once_with(self.cancel, self.notify, vulkan=True)
        self.cuda.assert_called_once()

    def test_8_gb_nvidia_uses_compact_cuda_without_first_loading_vulkan(self):
        weights, ensure, _ = self._gpu_fixture()
        device = runtime._GpuDevice('CUDA0', 'NVIDIA GeForce RTX 3050', 7202, 8191)
        self.cuda.return_value = self.root / 'engine-cuda' / runtime._SERVER, device

        def spawn_attempt(command, **kwargs):
            if 'CUDA0' in command:
                self.assertEqual(command[command.index('--log-verbosity') + 1], '4')
                self.assertEqual(command[command.index('--n-gpu-layers') + 1], '31')
                return self._process(kwargs['stdout'], _loop_gpu_log(31))
            return self._process(kwargs['stdout'], b'vk::OutOfDeviceMemoryError\n', 1)

        with (patch.object(runtime, '_probe_inference', return_value=True) as inference,
              patch.object(runtime.subprocess, 'Popen', side_effect=spawn_attempt) as spawn):
            self.manager.start(weights, self.cancel, self.notify, acceleration='gpu')
        spawn.assert_called_once()
        self.assertEqual(self.manager.status().backend, 'cuda')
        self.assertEqual(self.manager.status().label, 'GPU · 7/25 layers')
        self.assertIn('NVIDIA GeForce RTX 3050 via CUDA reported 7,202 MiB free of 8,191 MiB', self.manager.diagnostic())
        inference.assert_called_once()
        ensure.assert_not_called()
        self.cuda.assert_called_once()

    def test_inkling_physical_layers_ignore_aliases_and_output_only_plans(self):
        device = runtime._GpuDevice('CUDA0', 'NVIDIA GeForce RTX 3050', 7202, 8191)
        for logical, expected in ((1, (0, None)), (8, (0, None)), (25, (0, None)), (26, (2, 25)), (31, (7, 25)), (49, (25, 25))):
            with self.subTest(logical=logical), tempfile.TemporaryFile(mode='w+b') as log:
                log.write(_loop_gpu_log(logical))
                log.flush()
                self.assertEqual(runtime._offloaded_layers(log, device), expected)

    def test_inkling_requires_full_selected_device_evidence(self):
        device = runtime._GpuDevice('CUDA0', 'NVIDIA GeForce RTX 3050', 7202, 8191)
        cases = (
            _loop_gpu_log(8, prefix=False),
            _loop_gpu_log(31, prefix=False),
            _loop_gpu_log(31, 'CUDA1'),
            _loop_gpu_log(31, buffer='0.00'),
            _loop_gpu_log(31).replace(b'block_count u32 = 24', b'block_count u32 = 48'),
            _loop_gpu_log(31) + b'load_tensors: offloaded 31/49 layers to GPU\nCPU_Mapped model buffer size = 24000.0 MiB\n',
        )
        for contents in cases:
            with self.subTest(contents=contents), tempfile.TemporaryFile(mode='w+b') as log:
                log.write(contents)
                log.flush()
                self.assertEqual(runtime._offloaded_layers(log, device), (0, None))

    def test_8_gb_cuda_output_only_then_vulkan_memory_failure_stays_failed_in_gpu_mode(self):
        weights, ensure, _ = self._gpu_fixture(free_mib=7202, total_mib=8191)
        self.cuda.return_value = self.root / 'engine-cuda' / runtime._SERVER, runtime._GpuDevice('CUDA0', 'NVIDIA GeForce RTX 3050', 7202, 8191)

        def spawn_attempt(command, **kwargs):
            if 'CUDA0' in command:
                return self._process(kwargs['stdout'], _loop_gpu_log(8, buffer='883.42'))
            return self._process(kwargs['stdout'], b'vk::OutOfDeviceMemoryError\n', 1)

        with (patch.object(runtime, '_probe_inference') as generation,
              patch.object(runtime.subprocess, 'Popen', side_effect=spawn_attempt) as spawn):
            with self.assertRaises(runtime.InklingAccelerationError):
                self.manager.start(weights, self.cancel, self.notify, acceleration='gpu')
        self.assertIn('CUDA GPU did not offload any verified Inkling transformer blocks', self.manager.diagnostic())
        self.assertIn('Vulkan runner ran out of available memory', self.manager.diagnostic())
        self.assertEqual(spawn.call_count, 3)
        self.assertIn('CUDA0', spawn.call_args_list[0].args[0])
        self.assertIsNone(self.manager.status())
        generation.assert_not_called()
        ensure.assert_called_once_with(self.cancel, self.notify, vulkan=True)

    def test_gpu_download_failure_is_not_reported_as_missing_hardware(self):
        weights, ensure, gpu_probe = self._gpu_fixture()
        ensure.side_effect = ConnectionError('https://private.invalid/dont-display')
        with self.assertRaisesRegex(runtime.InklingAccelerationError, 'Could not download the verified Vulkan runner from GitHub'):
            self.manager.start(weights, self.cancel, self.notify, acceleration='gpu')
        self.assertNotIn('private.invalid', self.manager.diagnostic())
        gpu_probe.assert_not_called()

    def test_gpu_startup_error_preserves_windows_loader_failure_code(self):
        weights, _, _ = self._gpu_fixture()
        with patch.object(runtime.subprocess, 'Popen', side_effect=lambda *a, **kw: self._process(kw['stdout'], b'', 0xc0000135)):
            with self.assertRaisesRegex(runtime.InklingAccelerationError, r'Windows could not load a library.*0xC0000135'):
                self.manager.start(weights, self.cancel, self.notify, acceleration='gpu')

    def test_require_gpu_preserves_hardware_failure_and_does_not_launch_cpu(self):
        weights, ensure, _ = self._gpu_fixture()
        output = b'private-model.gguf: vk::OutOfDeviceMemoryError\n'
        with patch.object(runtime.subprocess, 'Popen', side_effect=lambda *a, **kw: self._process(kw['stdout'], output, 1)) as spawn:
            with self.assertRaisesRegex(runtime.InklingAccelerationError, 'available memory') as failure:
                self.manager.start(weights, self.cancel, self.notify, acceleration='gpu')
        self.assertEqual(spawn.call_count, 2)
        ensure.assert_called_once_with(self.cancel, self.notify, vulkan=True)
        self.assertEqual(self.manager.diagnostic(), str(failure.exception))
        self.assertIn('14,000 MiB free of 16,384 MiB', self.manager.diagnostic())
        self.assertNotIn('private-model.gguf', self.manager.diagnostic())
        self.assertIsNone(self.manager.status())

    def test_explicit_mode_retries_stale_auto_and_does_not_reuse_incompatible_workers(self):
        weights, _, device_probe = self._gpu_fixture()
        self.loader.return_value = False

        def spawn_attempt(command, **kwargs):
            if 'Vulkan1' in command:
                return self._process(kwargs['stdout'], b'offloaded 8/25 layers to GPU\nVulkan1 model buffer size = 1230.00 MiB\n')
            return self._process(kwargs['stdout'])

        with (patch.object(runtime, '_probe_inference', return_value=True),
              patch.object(runtime.subprocess, 'Popen', side_effect=spawn_attempt) as spawn):
            auto = self.manager.start(weights, self.cancel, self.notify)
            self.assertEqual(self.manager.status().backend, 'cpu')
            self.loader.return_value = True
            gpu = self.manager.start(weights, self.cancel, self.notify, acceleration='gpu')
            self.assertEqual(self.manager.status().backend, 'vulkan')
            self.assertNotEqual(auto, gpu)
            self.loader.reset_mock()
            self.cuda.reset_mock()
            cpu = self.manager.start(weights, self.cancel, self.notify, acceleration='cpu')
            self.assertNotEqual(cpu, gpu)
            self.assertEqual(self.manager.status().backend, 'cpu')
            self.assertEqual(self.manager.diagnostic(), 'CPU only is selected.')
            self.loader.assert_not_called()
            self.cuda.assert_not_called()
            self.assertEqual(cpu, self.manager.start(weights, self.cancel, self.notify, acceleration='cpu'))
            self.assertEqual(spawn.call_count, 3)
        device_probe.assert_called_once()
        with self.assertRaisesRegex(ValueError, 'Require GPU'):
            self.manager.start(weights, self.cancel, self.notify, acceleration='invalid')

    def test_auto_rechecks_available_gpu_after_cached_memory_pressure_expires(self):
        weights, _, device_probe = self._gpu_fixture()
        clock = Mock(return_value=90)
        graphics_memory_available = False

        def spawn_attempt(command, **kwargs):
            if 'Vulkan1' not in command:
                return self._process(kwargs['stdout'])
            if not graphics_memory_available:
                return self._process(kwargs['stdout'], b'vk::OutOfDeviceMemoryError\n', 1)
            return self._process(kwargs['stdout'], b'offloaded 8/25 layers to GPU\nVulkan1 model buffer size = 1230.00 MiB\n')

        with (patch.object(runtime, '_probe_inference', return_value=True),
              patch.object(runtime.time, 'monotonic', clock),
              patch.object(runtime.subprocess, 'Popen', side_effect=spawn_attempt) as spawn):
            cpu = self.manager.start(weights, self.cancel, self.notify)
            self.assertEqual(self.manager.status().backend, 'cpu')
            self.assertEqual(self.manager.start(weights, self.cancel, self.notify), cpu)
            graphics_memory_available = True
            clock.return_value += runtime._GPU_RETRY_COOLDOWN + 1
            self.assertNotEqual(self.manager.start(weights, self.cancel, self.notify), cpu)
            self.assertEqual(self.manager.status().backend, 'vulkan')
        self.assertEqual(spawn.call_count, 4)
        self.assertEqual(device_probe.call_count, 2)

    def test_vulkan_crash_after_ready_prefers_cpu_for_the_next_request(self):
        weights, _, probe = self._gpu_fixture()
        processes = []

        def spawn_attempt(command, **kwargs):
            process = self._process(kwargs['stdout'], b'offloaded 3/41 layers to GPU\nVulkan1 model buffer size = 4000.00 MiB\n')
            processes.append(process)
            return process

        with (patch.object(runtime, '_probe_inference', return_value=True),
              patch.object(runtime.subprocess, 'Popen', side_effect=spawn_attempt)):
            self.manager.start(weights, self.cancel, self.notify)
            processes[0].poll.return_value = 1
            self.assertIsNone(self.manager.status())
            self.manager.start(weights, self.cancel, self.notify)
        probe.assert_called_once()
        self.assertEqual(self.manager.status().backend, 'cpu')
        self.assertIn('stopped during use', self.manager.status().reason)

    def test_user_stop_during_vulkan_probe_does_not_retry_on_cpu(self):
        weights, ensure, probe = self._gpu_fixture()

        def stop_worker(executable, api, check_cancel):
            self.manager.stop()
            check_cancel()

        probe.side_effect = stop_worker
        with patch.object(runtime.subprocess, 'Popen') as spawn:
            with self.assertRaises(runtime.DownloadCancelled):
                self.manager.start(weights, self.cancel, self.notify)
        ensure.assert_called_once_with(self.cancel, self.notify, vulkan=True)
        spawn.assert_not_called()

    def test_gpu_generation_check_requires_positive_bounded_tokens_without_errors(self):
        for reply, expected in (({'tokens_predicted': 2}, True),
                                ({'tokens_predicted': 5}, True),
                                ({'tokens_predicted': 1}, False),
                                ({'tokens_predicted': 6}, False),
                                ({'tokens_predicted': 2, 'error': 'out of memory'}, False)):
            with self.subTest(reply=reply), patch.object(runtime, '_request_local_json', return_value=reply) as request:
                self.assertEqual(runtime._probe_inference(40000, 'private-local-key', lambda: None), expected)
                self.assertEqual(request.call_args.args, (40000, '/completion', 'private-local-key'))
                payload = request.call_args.kwargs['payload']
                self.assertEqual(payload['n_predict'], 2)
                self.assertTrue(payload['ignore_eos'])
                self.assertEqual(request.call_args.kwargs['timeout'], 120)
                self.assertNotIn('tools', payload)
                self.assertFalse(payload['cache_prompt'])

    def test_gpu_generation_can_be_stopped_before_http_returns(self):
        request_started = threading.Event()
        release_request = threading.Event()

        def request(*args, **kwargs):
            request_started.set()
            release_request.wait(3)
            return {'tokens_predicted': 2}

        def check_cancel():
            if request_started.is_set():
                raise runtime.DownloadCancelled('Stopped during the generation check.')

        try:
            with patch.object(runtime, '_request_local_json', side_effect=request):
                with self.assertRaises(runtime.DownloadCancelled):
                    runtime._probe_inference(40000, 'private-local-key', check_cancel)
                self.assertTrue(request_started.is_set())
                self.assertFalse(release_request.is_set())
        finally:
            release_request.set()

    def test_gpu_generation_timeout_describes_which_check_failed(self):
        with patch.object(runtime, '_request_local_json', side_effect=TimeoutError):
            with self.assertRaisesRegex(runtime._GpuFallback, 'generation check exceeded 120 seconds'):
                runtime._probe_inference(40000, 'private-local-key', lambda: None)

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
        with patch.object(runtime, '_request_local_json', side_effect=[{'status': 'ok'}, {'data': [{'id': 'other'}]}]):
            self.assertFalse(runtime._ready(40000, 'local-key'))
        with patch.object(runtime, '_request_local_json', side_effect=[{'status': 'ok'}, {'data': [{'id': runtime.INKLING_MODEL}]}]) as request:
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
