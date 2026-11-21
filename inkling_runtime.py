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
ENGINE_RELEASE_URL = f'https://github.com/ggml-org/llama.cpp/releases/tag/{ENGINE_TAG}'
_ENGINE_URL = f'https://github.com/ggml-org/llama.cpp/releases/download/{ENGINE_TAG}/{ENGINE_ARCHIVE}'
_SERVER = 'llama-server.exe'
_DLLS = {'ggml.dll', 'llama.dll', 'llama-common.dll', 'llama-server-impl.dll', 'libomp.dll', 'mtmd.dll'}
_DOWNLOAD_HOSTS = {'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}
_CHUNK = 1024 * 1024
_START_TIMEOUT = 420
_DOWNLOAD_TIMEOUT = 300


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


def _download_engine(target, cancel, notify):
    digest = hashlib.sha256()
    total = 0
    deadline = time.monotonic() + _DOWNLOAD_TIMEOUT
    notify('Downloading the pinned Inkling runner', 0, ENGINE_BYTES)
    request = Request(_ENGINE_URL, headers={'User-Agent': 'TalkToAi-Code', 'Accept': 'application/octet-stream'})
    try:
        with build_opener(_ReleaseRedirect()).open(request, timeout=15) as response, target.open('xb') as out:
            while chunk := response.read(_CHUNK):
                _cancelled(cancel)
                if time.monotonic() > deadline:
                    raise TimeoutError('Inkling runner download timed out.')
                total += len(chunk)
                if total > ENGINE_BYTES:
                    raise ValueError('The Inkling runner download exceeded its pinned size.')
                digest.update(chunk)
                out.write(chunk)
                notify('Downloading the pinned Inkling runner', total, ENGINE_BYTES)
            _cancelled(cancel)
    except URLError as exc:
        raise ConnectionError('Could not download the Inkling runner from GitHub. Check the connection and retry.') from exc
    if total != ENGINE_BYTES or digest.hexdigest() != ENGINE_SHA256:
        raise ValueError('The Inkling runner download failed its size or SHA-256 check. Retry the download.')


def _engine_members(package):
    """Select root-level runtime files without extracting any archive paths."""
    members = {}
    for item in package.infolist():
        name = item.filename.lower()
        if name not in {_SERVER, *_DLLS} and not re.fullmatch(r'ggml-[a-z0-9-]+\.dll', name):
            continue
        if (name in members or item.is_dir() or item.file_size <= 0
                or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)):
            raise ValueError('Invalid Inkling runner archive entry.')
        members[name] = item
    if not {_SERVER, *_DLLS} <= members.keys() or sum(i.file_size for i in members.values()) > 90 * _CHUNK:
        raise ValueError('The Inkling runner archive is incomplete or exceeds its extraction limit.')
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


def _engine_environment(key):
    # Give the runner only Windows environment settings and its own private API key.
    keep = {'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA'}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    sysroot = env.get('SYSTEMROOT', r'C:\Windows')
    env['PATH'] = os.pathsep.join((str(Path(sysroot) / 'System32'), sysroot))
    env['LLAMA_API_KEY'] = key
    return env


def _choose_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def _get_local_json(port, path, key):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
    try:
        connection.request('GET', path, headers={'Authorization': 'Bearer ' + key})
        response = connection.getresponse()
        return json.loads(response.read(65_537)) if response.status == 200 else {}
    finally:
        connection.close()


def _ready(port, key):
    try:
        if _get_local_json(port, '/health', key).get('status') != 'ok':
            return False
        return any(model.get('id') == INKLING_MODEL for model in _get_local_json(port, '/v1/models', key).get('data', []))
    except (OSError, ValueError, http.client.HTTPException, AttributeError, TypeError):
        return False


def _file_stamp(weights):
    path = native_path(weights).resolve(strict=True)
    details = path.stat()
    if not stat.S_ISREG(details.st_mode):
        raise ValueError('Select a local Inkling GGUF file.')
    return path, details.st_size, details.st_mtime_ns, details.st_ino


@dataclass
class _Session:
    process: subprocess.Popen
    log: BinaryIO
    port: int
    key: str
    weights: tuple
    ready: bool = False

    def endpoint(self):
        return f'http://127.0.0.1:{self.port}/v1', self.key


def _startup_error(session):
    size = session.log.seek(0, os.SEEK_END)
    session.log.seek(max(0, size - 16_384))
    detail = session.log.read().lower()
    if b'unknown model architecture' in detail or b'unknown model type' in detail:
        return 'This GGUF variant requires a different Inkling runner. Use a verified Mainline Support download from Models and setup.'
    if any(message in detail for message in (b'out of memory', b'failed to allocate', b'bad allocation', b'bad_alloc')):
        return 'Inkling ran out of memory. Release other loaded models or try the smaller Inkling preset.'
    return 'The Inkling runner could not load this GGUF. Verify the download and try the smaller preset if available memory is limited.'


class InklingRuntime:
    def __init__(self, state_folder):
        self.state_folder = Path(state_folder)
        self._guard = threading.RLock()
        self._launch = threading.Lock()
        self._generation = 0
        self._session = None
        atexit.register(self.stop)

    def ensure_engine(self, cancel, notify):
        if not supported():
            raise RuntimeError('Automatic Inkling setup is available on Windows x64. Use a compatible local llama.cpp server on other platforms.')
        _cancelled(cancel)
        root = _plain(library_root(self.state_folder) / f'inkling-{ENGINE_TAG}')
        with storage_lock(root):
            archive = _plain(root / ENGINE_ARCHIVE)
            engine = _plain(root / 'engine')
            valid = False
            if archive.is_file() and archive.stat().st_size == ENGINE_BYTES:
                with archive.open('rb') as file:
                    valid = _digest(file, cancel) == ENGINE_SHA256
            with tempfile.TemporaryDirectory(dir=root, prefix='.stage-') as work:
                stage = _plain(Path(work))
                if not valid:
                    download = stage / 'release.zip'
                    _download_engine(download, cancel, notify)
                    download.replace(archive)
                with zipfile.ZipFile(archive) as package:
                    members = _engine_members(package)
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
            current = self._session
            return bool(current and current.ready and current.process.poll() is None
                        and (stamp is None or current.weights == stamp))

    def start(self, weights, cancel, notify):
        while not self._launch.acquire(timeout=0.25):
            _cancelled(cancel)
        session = None
        completed = False
        try:
            _cancelled(cancel)
            stamp = _file_stamp(weights)
            with self._guard:
                generation = self._generation
                current = self._session
                if current and current.ready and current.process.poll() is None and current.weights == stamp:
                    return current.endpoint()
            with stamp[0].open('rb') as file:
                if file.read(4) != b'GGUF':
                    raise ValueError('The selected Inkling download is not a GGUF file.')
            executable = self.ensure_engine(cancel, notify)
            port = _choose_port()
            key = secrets.token_urlsafe(24)
            command = [str(executable), '--model', str(stamp[0]), '--host', '127.0.0.1', '--port', str(port),
                       '--alias', INKLING_MODEL, '--jinja', '--no-webui', '--no-agent', '--no-slots',
                       '--ctx-size', str(INKLING_CONTEXT), '--parallel', '1', '--n-gpu-layers', '0',
                       '--reasoning-effort', 'none', '--sleep-idle-seconds', '3600', '--log-verbosity', '2']
            with self._guard:
                _cancelled(cancel)
                if self._generation != generation:
                    raise DownloadCancelled('Inkling setup stopped.')
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
            notify('Loading Inkling on this computer. The first load can take several minutes.', 0, 0)
            deadline = time.monotonic() + _START_TIMEOUT
            while time.monotonic() < deadline:
                _cancelled(cancel)
                with self._guard:
                    if self._session is not session:
                        raise DownloadCancelled('Inkling setup stopped.')
                    if process.poll() is not None:
                        raise RuntimeError(_startup_error(session))
                if _ready(port, key):
                    with self._guard:
                        _cancelled(cancel)
                        if self._session is session and process.poll() is None:
                            session.ready = completed = True
                            return session.endpoint()
                cancel.wait(0.3)
            raise TimeoutError('Inkling took too long to load. Release other loaded models or try the smaller preset.')
        finally:
            if session and not completed:
                with self._guard:
                    if self._session is session:
                        self._stop_locked()
            self._launch.release()

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
        finally:
            session.log.close()

    def stop(self):
        with self._guard:
            self._generation += 1
            self._stop_locked()
