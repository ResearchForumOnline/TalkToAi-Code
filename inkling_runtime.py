"""Start a pinned, app-owned llama.cpp runner for Inkling 2 Mini on Windows x64."""
from __future__ import annotations

import atexit
from dataclasses import dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import BinaryIO
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

from inkling_cuda import bundled_crt_paths
from model_library import DownloadCancelled
from model_storage import _plain, library_root, native_path, storage_lock


INKLING_MODEL = 'inkling-2-mini'
INKLING_CONTEXT = 8192
ENGINE_TAG = 'b12613'
ENGINE_ARCHIVE = f'llama-{ENGINE_TAG}-bin-win-cpu-x64.zip'
ENGINE_BYTES = 25_009_376
ENGINE_SHA256 = '4c4ca218083b090393f517b598e7c86fc61e62f4d15f8748d6fedf24f809f429'
VULKAN_ARCHIVE = f'llama-{ENGINE_TAG}-bin-win-vulkan-x64.zip'
VULKAN_BYTES = 38_845_965
VULKAN_SHA256 = '56f1fca056efc933a39226fa767328a0082ebd42ec3c4d2e0193a37ac44abdee'
ENGINE_RELEASE_URL = f'https://github.com/ggml-org/llama.cpp/releases/tag/{ENGINE_TAG}'
_ENGINE_RELEASE_DOWNLOAD = f'https://github.com/ggml-org/llama.cpp/releases/download/{ENGINE_TAG}'
_SERVER = 'llama-server.exe'
_DLLS = {'ggml.dll', 'llama.dll', 'llama-common.dll', 'llama-server-impl.dll', 'libomp.dll', 'mtmd.dll'}
_VULKAN_DLL = 'ggml-vulkan.dll'
_VULKAN_DLL_BYTES = 44_856_320
_VULKAN_REQUIRED = {_VULKAN_DLL, 'ggml-base.dll', 'ggml-cpu-x64.dll'}
_DOWNLOAD_HOSTS = {'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}
_CHUNK = 1024 * 1024
_START_TIMEOUT = 420
_DOWNLOAD_TIMEOUT = 300
_VULKAN_START_TIMEOUT = 240
_DEVICE_TIMEOUT = 12
_SMOKE_TIMEOUT = 120
_SMOKE_TOKENS = 2
# Inkling Mini has 24 physical blocks and 24 aliases for the repeated loop.
# b12613 counts those aliases in --n-gpu-layers. 31 covers the six physical
# blocks after the loop, the 24 aliases, and the output layer.
_PARTIAL_GPU_LAYERS = 31
_INKLING_LOOP_SLOTS_WITH_OUTPUT = 49
_INKLING_PHYSICAL_LAYERS_WITH_OUTPUT = 25
_COMPACT_VRAM_MIB = 8704
_GPU_LOG_VERBOSITY = '4'  # b12613 sends model-loader INFO through its TRACE level
_GPU_RETRY_COOLDOWN = 240

_GPU_DEVICE = re.compile(r'^\s+((?:Vulkan|CUDA)[0-9]{1,2}): ([^\r\n]{1,256}) \(([0-9]+) MiB, ([0-9]+) MiB free\)\s*$')
_SOFTWARE_DEVICES = ('llvmpipe', 'lavapipe', 'swiftshader', 'software', 'warp')
_OFFLOAD = re.compile(rb'\boffloaded ([0-9]{1,4})/([0-9]{1,4}) layers to GPU\b')
_DEVICE_BUFFER = re.compile(rb'\b((?:Vulkan|CUDA)[0-9]{1,2})\s+model buffer size =\s+([0-9]+(?:\.[0-9]+)?) MiB\b')
_LOOP_ARCH = re.compile(rb'\bgeneral\.architecture[ \t]+str[ \t]*=[ \t]*inkling2_loop\b')
_PHYSICAL_BLOCKS = re.compile(rb'\binkling2_loop\.block_count[ \t]+u32[ \t]*=[ \t]*([0-9]{1,4})\b')


def supported():
    return sys.platform == 'win32' and platform.machine().lower() in ('amd64', 'x86_64')


def _cancelled(cancel):
    if cancel.is_set():
        raise DownloadCancelled('Inkling setup stopped.')


def _digest(handle, cancel):
    digest = hashlib.sha256()
    while chunk := handle.read(_CHUNK):
        _cancelled(cancel)
        digest.update(chunk)
    return digest.hexdigest()


class _ReleaseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        dest = urlsplit(newurl)
        if (dest.scheme != 'https' or dest.hostname not in _DOWNLOAD_HOSTS or dest.username is not None
                or dest.password is not None or dest.port not in (None, 443)):
            raise ConnectionError('GitHub redirected the Inkling runner download to an unexpected location.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _archive_pin(vulkan):
    return (VULKAN_ARCHIVE, VULKAN_BYTES, VULKAN_SHA256) if vulkan else (ENGINE_ARCHIVE, ENGINE_BYTES, ENGINE_SHA256)


def _download_engine(target, cancel, notify, vulkan=False):
    name, size, sha256 = _archive_pin(vulkan)
    message = 'Downloading the verified Inkling GPU runner' if vulkan else 'Downloading the verified Inkling CPU runner'
    _download_pinned_archive(target, name, size, sha256, cancel, notify, message)


def _download_pinned_archive(target, name, size, sha256, cancel, notify, message, *, timeout=_DOWNLOAD_TIMEOUT):
    digest = hashlib.sha256()
    total = 0
    deadline = time.monotonic() + timeout
    notify(message, 0, size)
    request = Request(f'{_ENGINE_RELEASE_DOWNLOAD}/{name}', headers={'User-Agent': 'TalkToAi-Code', 'Accept': 'application/octet-stream'})
    try:
        with build_opener(_ReleaseRedirect()).open(request, timeout=15) as response, target.open('xb') as out:
            last_update = 0.0
            while chunk := response.read(_CHUNK):
                _cancelled(cancel)
                if time.monotonic() > deadline:
                    raise TimeoutError('Inkling runner download timed out.')
                total += len(chunk)
                if total > size:
                    raise ValueError('The Inkling runner download exceeded its pinned size.')
                digest.update(chunk)
                out.write(chunk)
                now = time.monotonic()
                if total == size or now - last_update >= .25:
                    notify(message, total, size)
                    last_update = now
            _cancelled(cancel)
    except URLError as exc:
        raise ConnectionError('Could not download the Inkling runner from GitHub. Check the connection and retry.') from exc
    if total != size or digest.hexdigest() != sha256:
        raise ValueError('The Inkling runner download failed its size or SHA-256 check. Retry the download.')


def _engine_members(package, vulkan=False):
    """Select root-level runtime files without extracting any archive paths."""
    required = {_SERVER, *_DLLS}
    if vulkan:
        required |= _VULKAN_REQUIRED
    extra = r'ggml-cpu-[a-z0-9-]+\.dll' if vulkan else r'ggml-[a-z0-9-]+\.dll'
    members = {}
    for item in package.infolist():
        name = item.filename.lower()
        if name not in required and not re.fullmatch(extra, name):
            continue
        if (name in members or item.is_dir() or item.file_size <= 0
                or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)):
            raise ValueError('Invalid Inkling runner archive entry.')
        members[name] = item
    limit = (100 if vulkan else 90) * _CHUNK
    if not required <= members.keys() or sum(i.file_size for i in members.values()) > limit:
        raise ValueError('The Inkling runner archive is incomplete or exceeds its extraction limit.')
    if vulkan and members[_VULKAN_DLL].file_size != _VULKAN_DLL_BYTES:
        raise ValueError('The Inkling GPU archive does not contain the expected Vulkan backend.')
    return members


def _verified_engine(folder, package, members, cancel):
    if not folder.is_dir() or {item.name.lower() for item in folder.iterdir()} != members.keys():
        return False
    for name, member in members.items():
        local = _plain(folder / name)
        if not local.is_file() or local.stat().st_size != member.file_size:
            return False
        with package.open(member) as upstream, local.open('rb') as installed:
            if _digest(installed, cancel) != _digest(upstream, cancel):
                return False
    return True


def _engine_environment(key=None):
    # Give the runner only Windows environment settings and its own private API key.
    keep = {'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA'}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    sysroot = env.get('SYSTEMROOT', r'C:\Windows')
    env['PATH'] = os.pathsep.join((str(Path(sysroot) / 'System32'), sysroot, *bundled_crt_paths()))
    if key is not None:
        env['LLAMA_API_KEY'] = key
    return env


def _vulkan_loader_available():
    # The official backend links against the Vulkan loader installed with Windows drivers.
    return supported() and (Path(os.environ.get('SYSTEMROOT', r'C:\Windows')) / 'System32' / 'vulkan-1.dll').is_file()


@dataclass(frozen=True)
class _GpuDevice:
    identifier: str
    name: str
    free_mib: int
    total_mib: int

    @property
    def backend(self):
        return 'cuda' if self.identifier.startswith('CUDA') else 'vulkan'

    @property
    def api(self):
        return 'CUDA' if self.backend == 'cuda' else 'Vulkan'

    def describe(self):
        return f'{self.name} via {self.api} reported {self.free_mib:,} MiB free of {self.total_mib:,} MiB.'


def _select_gpu_device(output, api):
    devices = []
    for line in output.decode('utf-8', errors='replace').splitlines():
        row = _GPU_DEVICE.fullmatch(line)
        if not row:
            continue
        identifier, name, total, free = row.groups()
        if identifier.startswith(api) and int(total) > 0 and not any(software in name.casefold() for software in _SOFTWARE_DEVICES):
            devices.append((int(free), int(total), identifier, name))
    if not devices:
        return None
    free, total, identifier, name = max(devices)
    display_name = re.sub(r'[^\w .()+-]', '', name).strip()[:96]
    return _GpuDevice(identifier, display_name or identifier, free, total)


def _probe_gpu(executable, api, check_cancel):
    with tempfile.TemporaryFile(mode='w+b') as log:
        process = subprocess.Popen([str(executable), '--list-devices'], cwd=executable.parent,
                                   env=_engine_environment(), stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + _DEVICE_TIMEOUT
        try:
            while process.poll() is None:
                check_cancel()
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'The {api} device check timed out.')
                time.sleep(0.15)
            check_cancel()
            if process.returncode != 0:
                raise RuntimeError(f'The {api} device check failed.')
            log.seek(0)
            return _select_gpu_device(log.read(65_536), api)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)


def _choose_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def _request_local_json(port, path, key, *, payload=None, timeout=1):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    headers = {'Authorization': 'Bearer ' + key}
    try:
        if payload is None:
            connection.request('GET', path, headers=headers)
        else:
            headers['Content-Type'] = 'application/json'
            connection.request('POST', path, json.dumps(payload).encode('utf-8'), headers=headers)
        response = connection.getresponse()
        return json.loads(response.read(65_537)) if response.status == 200 else {}
    finally:
        connection.close()


def _ready(port, key):
    try:
        if _request_local_json(port, '/health', key).get('status') != 'ok':
            return False
        return any(model.get('id') == INKLING_MODEL for model in _request_local_json(port, '/v1/models', key).get('data', []))
    except (OSError, ValueError, http.client.HTTPException, AttributeError, TypeError):
        return False


def _probe_inference(port, key, check_cancel):
    """Check generation without sending any user content or tools to the worker."""
    finished = threading.Event()
    passed = False
    timed_out = False

    def run_request():
        nonlocal passed, timed_out
        try:
            payload = {'prompt': 'Hi', 'n_predict': _SMOKE_TOKENS, 'temperature': 0, 'ignore_eos': True,
                       'stream': False, 'cache_prompt': False}
            result = _request_local_json(port, '/completion', key, payload=payload, timeout=_SMOKE_TIMEOUT)
            count = result.get('tokens_predicted')
            # A second token exercises decode. llama.cpp can finish a partial UTF-8 codepoint.
            passed = 'error' not in result and type(count) is int and _SMOKE_TOKENS <= count <= _SMOKE_TOKENS + 3
        except TimeoutError:
            timed_out = True
        except (OSError, ValueError, http.client.HTTPException, AttributeError, TypeError):
            pass
        finally:
            finished.set()

    deadline = time.monotonic() + _SMOKE_TIMEOUT
    check_cancel()
    threading.Thread(target=run_request, daemon=True).start()
    while not finished.wait(0.2):
        check_cancel()
        if time.monotonic() >= deadline:
            timed_out = True
            break
    check_cancel()
    if timed_out:
        raise _GpuFallback(f'The GPU generation check exceeded {_SMOKE_TIMEOUT} seconds.')
    return passed


def _log_tail(log, limit):
    # The child and its standard output share a write offset. Read with an independent position.
    if os.name != 'nt':
        end = os.fstat(log.fileno()).st_size
        return os.pread(log.fileno(), limit, max(0, end - limit))

    import ctypes
    from ctypes import wintypes
    import msvcrt

    windows = ctypes.WinDLL('kernel32', use_last_error=True)
    reopen = windows.ReOpenFile
    reopen.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD)
    reopen.restype = wintypes.HANDLE
    # Share reads, writes and the original writer's delete access without adding delete-on-close.
    handle = reopen(msvcrt.get_osfhandle(log.fileno()), 0x80000000, 0x7, 0)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
    except OSError:
        windows.CloseHandle.argtypes = (wintypes.HANDLE,)
        windows.CloseHandle(handle)
        raise
    with os.fdopen(descriptor, 'rb') as reader:
        end = reader.seek(0, os.SEEK_END)
        reader.seek(max(0, end - limit))
        return reader.read(limit)


def _offloaded_layers(log, device):
    detail = _log_tail(log, _CHUNK)
    rows = list(_OFFLOAD.finditer(detail))
    if not rows:
        return 0, None
    last = rows[-1]
    identifier = device.identifier.encode('ascii')
    if not any(name == identifier and float(size) > 0 for name, size in _DEVICE_BUFFER.findall(detail, last.end())):
        return 0, None
    count, total = map(int, last.groups())
    loop_model = bool(_LOOP_ARCH.search(detail))
    if total == _INKLING_LOOP_SLOTS_WITH_OUTPUT and not loop_model:
        return 0, None
    if loop_model:
        physical = set(_PHYSICAL_BLOCKS.findall(detail))
        if len(physical) != 1:
            return 0, None
        physical_total = int(physical.pop()) + 1  # include the output layer, as llama.cpp does
        if total == _INKLING_LOOP_SLOTS_WITH_OUTPUT and physical_total != _INKLING_PHYSICAL_LAYERS_WITH_OUTPUT:
            return 0, None
        if not 1 < physical_total <= total:
            return 0, None
        count -= total - physical_total
        total = physical_total
    # A GPU output projection by itself does not accelerate a transformer block.
    return (count, total) if 1 < count <= total else (0, None)


def _file_stamp(weights):
    path = native_path(weights).resolve(strict=True)
    details = path.stat()
    if not stat.S_ISREG(details.st_mode):
        raise ValueError('Select a local Inkling GGUF file.')
    return path, details.st_size, details.st_mtime_ns, details.st_ino


@dataclass(frozen=True)
class InklingStatus:
    backend: str
    reason: str
    gpu_layers: int = 0
    total_layers: int | None = None
    device_name: str | None = None
    vram_free_mib: int | None = None
    vram_total_mib: int | None = None

    @property
    def label(self):
        if self.backend == 'cpu':
            return 'CPU'
        count = f'{self.gpu_layers}/{self.total_layers}' if self.total_layers else str(self.gpu_layers)
        return f'GPU · {count} layers'


@dataclass
class _Session:
    process: subprocess.Popen
    log: BinaryIO
    port: int
    key: str
    weights: tuple
    acceleration: str
    status: InklingStatus | None = None

    def endpoint(self):
        return f'http://127.0.0.1:{self.port}/v1', self.key


def _startup_error(session, device=None, exit_code=0):
    windows_status = exit_code & 0xffffffff
    if windows_status in (0xc0000135, 0xc0000139, 0xc000007b):
        runner = f'the {device.api} runner' if device else 'the Inkling runner'
        return f'Windows could not load a library required by {runner} (0x{windows_status:08X}).'
    detail = _log_tail(session.log, 16_384).lower()
    if b'unknown model architecture' in detail or b'unknown model type' in detail:
        return 'This GGUF variant requires a different Inkling runner. Use a verified Mainline Support download from Models and setup.'
    if device:
        if any(message in detail for message in (b'out of memory', b'failed to allocate', b'outofdevicememory',
                                                 b'outofhostmemory', b'out_of_device_memory', b'out_of_host_memory')):
            return f'The {device.api} runner ran out of available memory while loading Inkling.'
        if any(message in detail for message in (b'error_device_lost', b'errordevicelost', b'device lost')):
            return f'The {device.api} device stopped responding while loading Inkling.'
        return f'The {device.api} runner could not load this model.'
    if any(message in detail for message in (b'out of memory', b'failed to allocate', b'bad allocation', b'bad_alloc')):
        return 'Inkling ran out of memory. Release other loaded models or try the smaller Inkling preset.'
    return 'The Inkling runner could not load this GGUF. Verify the download and try the smaller preset if available memory is limited.'


class InklingAccelerationError(RuntimeError):
    """The requested GPU mode could not start a verified accelerated session."""


class _GpuFallback(RuntimeError):
    def __init__(self, reason, device=None):
        super().__init__(reason)
        self.device = device


def _hardware_status(backend, reason, device=None, layers=0, total=None):
    if not device:
        return InklingStatus(backend, reason)
    return InklingStatus(backend, f'{device.describe()} {reason}', layers, total,
                         device.name, device.free_mib, device.total_mib)


class InklingRuntime:
    def __init__(self, state_folder):
        self.state_folder = Path(state_folder)
        self._guard = threading.RLock()
        self._launch = threading.Lock()
        self._generation = 0
        self._session = None
        self._gpu_unavailable = None
        self._model_cpu_reasons = {}
        self._last_diagnostic = None
        atexit.register(self.stop)

    def ensure_engine(self, cancel, notify, *, vulkan=False):
        if not supported():
            raise RuntimeError('Automatic Inkling setup is available on Windows x64. Use a compatible local llama.cpp server on other platforms.')
        _cancelled(cancel)
        root = _plain(library_root(self.state_folder) / f'inkling-{ENGINE_TAG}')
        archive_name, size, sha256 = _archive_pin(vulkan)
        with storage_lock(root):
            archive = _plain(root / archive_name)
            engine = _plain(root / ('engine-vulkan' if vulkan else 'engine'))
            valid = False
            if archive.is_file() and archive.stat().st_size == size:
                with archive.open('rb') as file:
                    valid = _digest(file, cancel) == sha256
            with tempfile.TemporaryDirectory(dir=root, prefix='.stage-') as work:
                stage = _plain(Path(work))
                if not valid:
                    download = stage / 'release.zip'
                    _download_engine(download, cancel, notify, vulkan)
                    download.replace(archive)
                with zipfile.ZipFile(archive) as package:
                    members = _engine_members(package, vulkan)
                    if _verified_engine(engine, package, members, cancel):
                        return engine / _SERVER
                    staged_engine = stage / 'engine'
                    staged_engine.mkdir()
                    notify('Preparing the verified Inkling runner', 0, 0)
                    for name, member in members.items():
                        _cancelled(cancel)
                        with package.open(member) as source, (staged_engine / name).open('xb') as output:
                            shutil.copyfileobj(source, output, _CHUNK)
                _cancelled(cancel)
                if engine.exists():
                    # Preserve any old directory, including one left open by another Studio session.
                    engine.rename(_plain(root / ('.previous-' + secrets.token_hex(8))))
                staged_engine.replace(engine)
            return engine / _SERVER

    def running(self, weights=None):
        try:
            stamp = _file_stamp(weights) if weights is not None else None
        except (OSError, ValueError):
            return False
        with self._guard:
            current = self._live_session_locked()
            return bool(current and current.status
                        and (stamp is None or current.weights == stamp))

    def status(self) -> InklingStatus | None:
        with self._guard:
            current = self._live_session_locked()
            return current.status if current else None

    def diagnostic(self) -> str | None:
        with self._guard:
            self._live_session_locked()
            return self._last_diagnostic

    def start(self, weights, cancel, notify, *, acceleration='auto'):
        if acceleration not in ('auto', 'gpu', 'cpu'):
            raise ValueError('Choose Auto, Require GPU, or CPU only for Inkling.')
        while not self._launch.acquire(timeout=0.25):
            _cancelled(cancel)
        try:
            _cancelled(cancel)
            stamp = _file_stamp(weights)
            with self._guard:
                generation = self._generation
                current = self._live_session_locked()
                gpu_failure = self._model_cpu_reasons.get(stamp)
                if gpu_failure and time.monotonic() >= gpu_failure[1]:
                    self._model_cpu_reasons.pop(stamp)
                    gpu_failure = None
                cpu_status = self._gpu_unavailable or (gpu_failure[0] if gpu_failure else None)
                if current and current.status and current.weights == stamp:
                    if current.status.backend != 'cpu' and acceleration != 'cpu':
                        return current.endpoint()
                    if current.status.backend == 'cpu' and current.acceleration == acceleration:
                        if acceleration == 'cpu' or cpu_status:
                            return current.endpoint()

            def check_cancel():
                _cancelled(cancel)
                with self._guard:
                    if self._generation != generation:
                        raise DownloadCancelled('Inkling setup stopped.')

            with stamp[0].open('rb') as file:
                if file.read(4) != b'GGUF':
                    raise ValueError('The selected Inkling download is not a GGUF file.')
            with self._guard:
                check_cancel()
                self._stop_locked()
            if acceleration == 'cpu':
                cpu_status = InklingStatus('cpu', 'CPU only is selected.')
            elif acceleration == 'gpu' or not cpu_status:
                try:
                    endpoint = self._start_gpu(stamp, cancel, notify, check_cancel, acceleration)
                except _GpuFallback as exc:
                    check_cancel()
                    cpu_status = _hardware_status('cpu', str(exc), exc.device)
                    with self._guard:
                        self._last_diagnostic = cpu_status.reason
                        if exc.device:
                            self._model_cpu_reasons[stamp] = cpu_status, time.monotonic() + _GPU_RETRY_COOLDOWN
                        else:
                            self._gpu_unavailable = cpu_status
                    if acceleration == 'gpu':
                        raise InklingAccelerationError(cpu_status.reason) from None
                    notify(f'{cpu_status.reason} Trying the CPU runner.', 0, 0)
                else:
                    with self._guard:
                        self._gpu_unavailable = None
                        self._model_cpu_reasons.pop(stamp, None)
                    return endpoint
            check_cancel()
            executable = self.ensure_engine(cancel, notify)
            return self._launch_runner(executable, stamp, cancel, notify, check_cancel, acceleration, cpu_status=cpu_status)
        finally:
            self._launch.release()

    def _start_gpu(self, stamp, cancel, notify, check_cancel, acceleration):
        reasons = []
        device = None
        for available in (self._available_cuda, self._available_vulkan):
            check_cancel()
            try:
                runner = available(cancel, notify, check_cancel)
            except _GpuFallback as exc:
                reasons.append(str(exc))
                continue
            if not runner:
                continue
            executable, device = runner
            profiles = (False, True)
            if device.free_mib <= _COMPACT_VRAM_MIB:
                profiles = (True,) if device.backend == 'cuda' else (True, False)
            for partial in profiles:
                check_cancel()
                if partial:
                    notify(f'Trying a lower-memory {device.api} GPU setup for Inkling.', 0, 0)
                try:
                    return self._launch_runner(executable, stamp, cancel, notify, check_cancel, acceleration,
                                               device=device, partial=partial)
                except DownloadCancelled:
                    raise
                except _GpuFallback as exc:
                    reasons.append(str(exc))
                except (RuntimeError, OSError):
                    reasons.append(f'Windows could not start the verified {device.api} runner.')
        raise _GpuFallback(' '.join(dict.fromkeys(reasons)), device)

    def _available_vulkan(self, cancel, notify, check_cancel):
        if not _vulkan_loader_available():
            raise _GpuFallback('Windows has no Vulkan loader.')
        try:
            executable = self.ensure_engine(cancel, notify, vulkan=True)
        except DownloadCancelled:
            raise
        except ConnectionError:
            raise _GpuFallback('Could not download the verified Vulkan runner from GitHub.') from None
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile):
            raise _GpuFallback('Could not prepare the verified Vulkan runner in local storage.') from None
        return executable, self._checked_device(executable, 'Vulkan', check_cancel)

    def _available_cuda(self, cancel, notify, check_cancel):
        from inkling_cuda import cuda_runner

        try:
            executable = cuda_runner(self.state_folder, cancel, notify)
        except DownloadCancelled:
            raise
        except ConnectionError:
            raise _GpuFallback('Could not download the verified NVIDIA CUDA runner from GitHub.') from None
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile):
            raise _GpuFallback('Could not prepare the verified NVIDIA CUDA runner in local storage.') from None
        check_cancel()
        if not executable:
            return None
        return executable, self._checked_device(executable, 'CUDA', check_cancel)

    def _checked_device(self, executable, api, check_cancel):
        check_cancel()
        try:
            device = _probe_gpu(executable, api, check_cancel)
        except DownloadCancelled:
            raise
        except TimeoutError:
            raise _GpuFallback(f'The {api} device check timed out.') from None
        except (OSError, RuntimeError):
            raise _GpuFallback(f'The verified {api} runner could not check for GPUs.') from None
        if not device:
            raise _GpuFallback(f'The verified {api} runner found no compatible GPU.')
        return device

    def _launch_runner(self, executable, stamp, cancel, notify, check_cancel, acceleration, *,
                       device=None, partial=False, cpu_status=None):
        port = _choose_port()
        key = secrets.token_urlsafe(24)
        command = [str(executable), '--model', str(stamp[0]), '--host', '127.0.0.1', '--port', str(port),
                   '--alias', INKLING_MODEL, '--jinja', '--no-webui', '--no-agent', '--no-slots',
                   '--ctx-size', str(INKLING_CONTEXT), '--parallel', '1', '--reasoning-effort', 'none',
                   '--sleep-idle-seconds', '3600']
        if partial:
            command += ['--fit', 'off',
                        '--batch-size', '128', '--n-gpu-layers', str(_PARTIAL_GPU_LAYERS),
                        '--cpu-moe', '--no-kv-offload']
        elif device:
            command += ['--fit', 'on', '--fit-target', '1024', '--batch-size', '256']
        else:
            command += ['--device', 'none', '--fit', 'off', '--n-gpu-layers', '0', '--log-verbosity', '2']
        if device:
            # Avoid copying host-resident experts into the GPU scheduler's looped graph.
            command += ['--device', device.identifier, '--log-verbosity', _GPU_LOG_VERBOSITY,
                        '--no-op-offload', '--ubatch-size', '16', '--flash-attn', 'off']
        session = None
        try:
            with self._guard:
                check_cancel()
                self._stop_locked()
                log = tempfile.TemporaryFile(mode='w+b')
                try:
                    process = subprocess.Popen(command, cwd=executable.parent, env=_engine_environment(key),
                                               stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                except OSError as exc:
                    log.close()
                    raise RuntimeError('Windows could not start the verified Inkling runner. Check Windows app permissions and retry.') from exc
                session = self._session = _Session(process, log, port, key, stamp, acceleration)
            compute = f'{device.api} GPU' if device else 'CPU'
            notify(f'Loading Inkling on the {compute}. The first load can take several minutes.', 0, 0)
            deadline = time.monotonic() + (_VULKAN_START_TIMEOUT if device else _START_TIMEOUT)
            while time.monotonic() < deadline:
                check_cancel()
                with self._guard:
                    if self._session is not session:
                        raise DownloadCancelled('Inkling setup stopped.')
                    exit_code = process.poll()
                    if exit_code is not None:
                        if device:
                            raise _GpuFallback(_startup_error(session, device, exit_code))
                        raise RuntimeError(_startup_error(session, exit_code=exit_code))
                if _ready(port, key):
                    status = cpu_status if not device else self._verify_gpu(session, device, partial, notify, check_cancel)
                    with self._guard:
                        check_cancel()
                        if self._session is session and process.poll() is None:
                            session.status = status
                            self._last_diagnostic = status.reason
                            return session.endpoint()
                cancel.wait(0.3)
            if device:
                raise _GpuFallback(f'The {device.api} GPU took too long to load Inkling.')
            raise TimeoutError('Inkling took too long to load. Release other loaded models or try the smaller preset.')
        finally:
            if session and not session.status:
                with self._guard:
                    if self._session is session:
                        self._stop_locked()

    def _verify_gpu(self, session, device, partial, notify, check_cancel):
        with self._guard:
            check_cancel()
            count, total = _offloaded_layers(session.log, device)
        if not count:
            raise _GpuFallback(f'The {device.api} GPU did not offload any verified Inkling transformer blocks.')
        notify('Checking Inkling generation on the GPU.', 0, 0)
        if not _probe_inference(session.port, session.key, check_cancel):
            raise _GpuFallback(f'The {device.api} runner did not pass its generation check.')
        reason = ('The lower-memory GPU check passed with expert weights and the context cache in system RAM.'
                  if partial else 'The GPU generation check passed.')
        return _hardware_status(device.backend, reason, device, count, total)

    def _live_session_locked(self):
        if self._session and self._session.status and self._session.process.poll() is not None:
            self._stop_locked()
        return self._session

    def _stop_locked(self):
        session = self._session
        self._session = None
        if not session:
            return
        try:
            if session.process.poll() is None:
                session.process.terminate()
                try:
                    session.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    session.process.kill()
                    session.process.wait(timeout=3)
            elif session.status and session.status.backend != 'cpu':
                gpu = session.status
                self._last_diagnostic = f'{gpu.backend.upper()} stopped during use. Auto will use CPU for this app session.'
                self._gpu_unavailable = InklingStatus('cpu', self._last_diagnostic, device_name=gpu.device_name,
                                                       vram_free_mib=gpu.vram_free_mib, vram_total_mib=gpu.vram_total_mib)
        finally:
            session.log.close()

    def stop(self):
        with self._guard:
            self._generation += 1
            self._stop_locked()
