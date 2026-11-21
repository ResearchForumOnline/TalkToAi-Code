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
_SMOKE_TIMEOUT = 35
_SMOKE_TOKENS = 4

_VULKAN_DEVICE = re.compile(r'^\s+(Vulkan[0-9]{1,2}): ([^\r\n]{1,256}) \(([0-9]+) MiB, ([0-9]+) MiB free\)\s*$')
_SOFTWARE_DEVICES = ('llvmpipe', 'lavapipe', 'swiftshader', 'software', 'warp')
_OFFLOAD = re.compile(rb'\boffloaded ([0-9]{1,4})/([0-9]{1,4}) layers to GPU\b')
_DEVICE_BUFFER = re.compile(rb'\bVulkan[0-9]{1,2}\s+model buffer size =\s+([0-9]+(?:\.[0-9]+)?) MiB\b')


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
    digest = hashlib.sha256()
    total = 0
    deadline = time.monotonic() + _DOWNLOAD_TIMEOUT
    message = 'Downloading the verified Inkling GPU runner' if vulkan else 'Downloading the verified Inkling CPU runner'
    notify(message, 0, size)
    request = Request(f'{_ENGINE_RELEASE_DOWNLOAD}/{name}', headers={'User-Agent': 'TalkToAi-Code', 'Accept': 'application/octet-stream'})
    try:
        with build_opener(_ReleaseRedirect()).open(request, timeout=15) as response, target.open('xb') as out:
            while chunk := response.read(_CHUNK):
                _cancelled(cancel)
                if time.monotonic() > deadline:
                    raise TimeoutError('Inkling runner download timed out.')
                total += len(chunk)
                if total > size:
                    raise ValueError('The Inkling runner download exceeded its pinned size.')
                digest.update(chunk)
                out.write(chunk)
                notify(message, total, size)
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
    env['PATH'] = os.pathsep.join((str(Path(sysroot) / 'System32'), sysroot))
    if key is not None:
        env['LLAMA_API_KEY'] = key
    return env


def _vulkan_loader_available():
    # The official backend links against the Vulkan loader installed with Windows drivers.
    return supported() and (Path(os.environ.get('SYSTEMROOT', r'C:\Windows')) / 'System32' / 'vulkan-1.dll').is_file()


def _select_vulkan_device(output):
    devices = []
    for line in output.decode('utf-8', errors='replace').splitlines():
        row = _VULKAN_DEVICE.fullmatch(line)
        if not row:
            continue
        identifier, name, total, free = row.groups()
        if int(free) > 0 and not any(software in name.casefold() for software in _SOFTWARE_DEVICES):
            devices.append((int(free), int(total), identifier, name))
    if not devices:
        return None
    _, _, identifier, name = max(devices)
    display_name = re.sub(r'[^\w .()+-]', '', name).strip()[:96]
    return identifier, display_name or identifier


def _probe_vulkan(executable, check_cancel):
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
                    raise TimeoutError('The Vulkan device check timed out.')
                time.sleep(0.15)
            check_cancel()
            if process.returncode != 0:
                raise RuntimeError('The Vulkan device check failed.')
            log.seek(0)
            return _select_vulkan_device(log.read(65_536))
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

    def run_request():
        nonlocal passed
        try:
            payload = {'model': INKLING_MODEL, 'messages': [{'role': 'user', 'content': 'Say hello.'}],
                       'max_tokens': _SMOKE_TOKENS, 'temperature': 0, 'stream': False, 'cache_prompt': False}
            result = _request_local_json(port, '/v1/chat/completions', key, payload=payload, timeout=_SMOKE_TIMEOUT)
            count = result.get('usage', {}).get('completion_tokens')
            passed = 'error' not in result and type(count) is int and 0 < count <= _SMOKE_TOKENS
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
            return False
    check_cancel()
    return passed


def _log_tail(log, limit):
    # The child and its standard output share a write offset. Read with an independent position.
    if isinstance(log.name, int):
        end = os.fstat(log.fileno()).st_size
        return os.pread(log.fileno(), limit, max(0, end - limit))
    with open(log.name, 'rb', opener=lambda path, flags: os.open(path, flags | os.O_TEMPORARY)) as reader:
        end = reader.seek(0, os.SEEK_END)
        reader.seek(max(0, end - limit))
        return reader.read(limit)


def _offloaded_layers(log):
    detail = _log_tail(log, _CHUNK)
    rows = _OFFLOAD.findall(detail)
    if not rows or not any(float(size) > 0 for size in _DEVICE_BUFFER.findall(detail)):
        return 0, None
    count, total = map(int, rows[-1])
    return (count, total) if 0 < count <= total else (0, None)


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
    status: InklingStatus | None = None

    def endpoint(self):
        return f'http://127.0.0.1:{self.port}/v1', self.key


def _startup_error(session):
    detail = _log_tail(session.log, 16_384).lower()
    if b'unknown model architecture' in detail or b'unknown model type' in detail:
        return 'This GGUF variant requires a different Inkling runner. Use a verified Mainline Support download from Models and setup.'
    if any(message in detail for message in (b'out of memory', b'failed to allocate', b'bad allocation', b'bad_alloc')):
        return 'Inkling ran out of memory. Release other loaded models or try the smaller Inkling preset.'
    return 'The Inkling runner could not load this GGUF. Verify the download and try the smaller preset if available memory is limited.'


class _VulkanFallback(RuntimeError):
    pass


class InklingRuntime:
    def __init__(self, state_folder):
        self.state_folder = Path(state_folder)
        self._guard = threading.RLock()
        self._launch = threading.Lock()
        self._generation = 0
        self._session = None
        self._vulkan_unavailable = None
        self._model_cpu_reasons = {}
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

    def start(self, weights, cancel, notify):
        while not self._launch.acquire(timeout=0.25):
            _cancelled(cancel)
        try:
            _cancelled(cancel)
            stamp = _file_stamp(weights)
            with self._guard:
                generation = self._generation
                current = self._live_session_locked()
                if current and current.status and current.weights == stamp:
                    return current.endpoint()

            def check_cancel():
                _cancelled(cancel)
                with self._guard:
                    if self._generation != generation:
                        raise DownloadCancelled('Inkling setup stopped.')

            with stamp[0].open('rb') as file:
                if file.read(4) != b'GGUF':
                    raise ValueError('The selected Inkling download is not a GGUF file.')
            reason = self._vulkan_unavailable or self._model_cpu_reasons.get(stamp)
            if not reason:
                runner = self._available_vulkan(cancel, notify, check_cancel)
                if runner:
                    executable, device = runner
                    try:
                        return self._launch_runner(executable, stamp, cancel, notify, check_cancel, device=device)
                    except DownloadCancelled:
                        raise
                    except _VulkanFallback as exc:
                        reason = str(exc)
                    except (OSError, RuntimeError):
                        reason = 'The Vulkan runner could not load this model.'
                    check_cancel()
                    self._model_cpu_reasons[stamp] = reason
                    notify(f'{reason} Trying the CPU runner.', 0, 0)
                else:
                    reason = self._vulkan_unavailable
            check_cancel()
            executable = self.ensure_engine(cancel, notify)
            return self._launch_runner(executable, stamp, cancel, notify, check_cancel, cpu_reason=reason)
        finally:
            self._launch.release()

    def _available_vulkan(self, cancel, notify, check_cancel):
        reason = 'Windows has no Vulkan loader.'
        if _vulkan_loader_available():
            reason = 'No compatible Vulkan GPU was detected.'
            try:
                executable = self.ensure_engine(cancel, notify, vulkan=True)
                check_cancel()
                device = _probe_vulkan(executable, check_cancel)
                if device:
                    return executable, device
            except DownloadCancelled:
                raise
            except (OSError, ValueError, RuntimeError, zipfile.BadZipFile):
                reason = 'The Vulkan runner could not verify a compatible GPU.'
        check_cancel()
        self._vulkan_unavailable = reason
        return None

    def _launch_runner(self, executable, stamp, cancel, notify, check_cancel, *, device=None, cpu_reason=None):
        port = _choose_port()
        key = secrets.token_urlsafe(24)
        command = [str(executable), '--model', str(stamp[0]), '--host', '127.0.0.1', '--port', str(port),
                   '--alias', INKLING_MODEL, '--jinja', '--no-webui', '--no-agent', '--no-slots',
                   '--ctx-size', str(INKLING_CONTEXT), '--parallel', '1', '--reasoning-effort', 'none',
                   '--sleep-idle-seconds', '3600']
        if device:
            command += ['--device', device[0], '--fit', 'on', '--fit-target', '1024', '--log-verbosity', '3']
        else:
            command += ['--device', 'none', '--fit', 'off', '--n-gpu-layers', '0', '--log-verbosity', '2']
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
                session = self._session = _Session(process, log, port, key, stamp)
            compute = 'GPU' if device else 'CPU'
            notify(f'Loading Inkling on the {compute}. The first load can take several minutes.', 0, 0)
            deadline = time.monotonic() + (_VULKAN_START_TIMEOUT if device else _START_TIMEOUT)
            while time.monotonic() < deadline:
                check_cancel()
                with self._guard:
                    if self._session is not session:
                        raise DownloadCancelled('Inkling setup stopped.')
                    if process.poll() is not None:
                        raise RuntimeError(_startup_error(session))
                if _ready(port, key):
                    status = InklingStatus('cpu', cpu_reason) if not device else self._verify_vulkan(session, device, notify, check_cancel)
                    with self._guard:
                        check_cancel()
                        if self._session is session and process.poll() is None:
                            session.status = status
                            return session.endpoint()
                cancel.wait(0.3)
            raise TimeoutError('Inkling took too long to load. Release other loaded models or try the smaller preset.')
        finally:
            if session and not session.status:
                with self._guard:
                    if self._session is session:
                        self._stop_locked()

    def _verify_vulkan(self, session, device, notify, check_cancel):
        with self._guard:
            check_cancel()
            count, total = _offloaded_layers(session.log)
        if not count:
            raise _VulkanFallback('The GPU could not hold any Inkling model layers.')
        notify('Checking Inkling generation on the GPU.', 0, 0)
        if not _probe_inference(session.port, session.key, check_cancel):
            raise _VulkanFallback('The Vulkan runner did not pass its generation check.')
        return InklingStatus('vulkan', f'{device[1]} passed the Vulkan generation check.', count, total)

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
            elif session.status and session.status.backend == 'vulkan':
                self._vulkan_unavailable = 'The Vulkan runner stopped during use. The CPU runner was selected for this app session.'
        finally:
            session.log.close()

    def stop(self):
        with self._guard:
            self._generation += 1
            self._stop_locked()
