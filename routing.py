"""Select an available installed model before executing any tools."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPException
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from model_library import DEFAULT_LOCAL_MODEL, DownloadCancelled, ollama_store, pull_model


LOCAL_PORT = 11434
SERVER_PORT = 11435
_LOCAL_START_LOCK = threading.Lock()


class OllamaMissingError(ConnectionError):
    """No local Ollama service or executable could be found."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_LOOPBACK_HTTP = build_opener(ProxyHandler({}), _NoRedirect())


def _windows_user_path():
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
            path, _ = winreg.QueryValueEx(key, 'Path')
        return winreg.ExpandEnvironmentStrings(path)
    except (OSError, TypeError):
        return ''


def ollama_executable():
    command = shutil.which('ollama')
    if command:
        return command
    candidates = []
    if os.name == 'nt':
        local = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
        candidates.append(local / 'Programs' / 'Ollama' / 'ollama.exe')
        # An installer may just have updated the account PATH while Studio is open.
        candidates.extend(
            Path(folder.strip().strip('"')) / 'ollama.exe'
            for folder in _windows_user_path().split(os.pathsep)
            if folder.strip().strip('"') and Path(folder.strip().strip('"')).is_absolute()
        )
    elif sys.platform == 'darwin':
        command = Path('Applications/Ollama.app/Contents/Resources/ollama')
        candidates.extend([Path('/') / command, Path.home() / command, Path('/opt/homebrew/bin/ollama'),
                           Path('/usr/local/bin/ollama')])
    else:
        candidates.extend([Path('/usr/local/bin/ollama'), Path('/usr/bin/ollama')])
    return next((str(candidate) for candidate in candidates
                 if candidate.is_file() and os.access(candidate, os.X_OK)), None)


def _endpoint(port):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Invalid Ollama loopback port.')
    return f'http://127.0.0.1:{port}'


def _json_request(port, path, timeout=3):
    with _LOOPBACK_HTTP.open(_endpoint(port) + path, timeout=timeout) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('Ollama returned an unexpectedly large response.')
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError('Ollama returned an invalid object.')
    return data


def list_models(port=LOCAL_PORT, *, timeout=3):
    """Read installed model names, distinguishing an empty list from an offline daemon."""
    try:
        models = _json_request(port, '/api/tags', timeout=timeout).get('models')
        if not isinstance(models, list) or not all(
            isinstance(item, dict) and isinstance(item.get('name'), str) and item['name'] for item in models
        ):
            raise ValueError('Ollama returned an invalid inventory.')
        return [item['name'] for item in models]
    except (OSError, ValueError, HTTPException) as exc:
        raise ConnectionError(f'Ollama model inventory is unavailable on 127.0.0.1:{port}.') from exc

def inventory(port):
    try:
        return list_models(port)
    except ConnectionError:
        return []


def matching_model(names, model):
    model = str(model or '').strip().casefold()
    valid = {model} if ':' in model.rsplit('/', 1)[-1] else {model, model + ':latest'}
    return next((name for name in names if name.casefold() in valid), None)

def model_present(port, model):
    return matching_model(inventory(port), model) is not None


def _version():
    try:
        value = _json_request(LOCAL_PORT, '/api/version', timeout=1).get('version')
    except (OSError, ValueError, HTTPException):
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _local_port_in_use():
    try:
        with socket.create_connection(('127.0.0.1', LOCAL_PORT), timeout=.5):
            return True
    except OSError:
        return False


def _check_cancel(cancel):
    if cancel.is_set():
        raise DownloadCancelled('Model operation stopped.')


def require_local_runtime(cancel=None, notify=None, *, timeout=35):
    """Find or start Ollama on this computer and wait for a valid version response."""
    cancel = cancel if cancel is not None else threading.Event()
    while not _LOCAL_START_LOCK.acquire(timeout=.1):
        _check_cancel(cancel)
    try:
        _check_cancel(cancel)
        version = _version()
        if version:
            return version
        occupied = _local_port_in_use()
        process = None
        if not occupied:
            ollama = ollama_executable()
            if not ollama:
                raise OllamaMissingError('Ollama was not found on this computer. Install Ollama, then retry the local model.')
            if notify:
                notify('Starting Ollama on this computer', 0, 0)
            env = {**os.environ, 'OLLAMA_HOST': f'127.0.0.1:{LOCAL_PORT}', 'OLLAMA_MODELS': str(ollama_store())}
            try:
                process = subprocess.Popen(
                    [ollama, 'serve'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), env=env,
                )
            except OSError as exc:
                raise ConnectionError(f'Could not start local Ollama. {exc}') from exc
        elif notify:
            notify('Waiting for Ollama on this computer', 0, 0)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _check_cancel(cancel)
            version = _version()
            if version:
                return version
            if process is not None:
                exit_code = process.poll()
                if exit_code is not None and not _local_port_in_use():
                    raise ConnectionError(f'Local Ollama exited with code {exit_code} before serving port {LOCAL_PORT}. '
                                          'Open Ollama from the Start menu or Applications and check its logs.')
            cancel.wait(.35)
        if occupied:
            raise ConnectionError(f'Port {LOCAL_PORT} is open but did not report a valid Ollama version. '
                                  'Check the local service and retry.')
        raise ConnectionError(f'Local Ollama did not start serving port {LOCAL_PORT} within {timeout} seconds. '
                              'Open Ollama and retry the download.')
    finally:
        _LOCAL_START_LOCK.release()

def ensure_local_runtime():
    """Start an installed local Ollama service without downloading a model."""
    try:
        require_local_runtime()
        return True
    except ConnectionError:
        return False


def install_model(model, cancel, notify, *, port=LOCAL_PORT, server_config=None, timeout=7200):
    """Install into local Ollama or an explicitly chosen loopback SSH tunnel."""
    _check_cancel(cancel)
    if port == LOCAL_PORT:
        require_local_runtime(cancel, notify)
        options = {}
    elif port == SERVER_PORT and server_config is not None:
        from amd_runtime import ensure_amd_tunnel

        notify('Connecting to the selected server Ollama', 0, 0)
        ready, detail = ensure_amd_tunnel(server_config, require_model=False, cancel=cancel)
        _check_cancel(cancel)
        if not ready:
            raise ConnectionError(detail)
        options = {'endpoint': _endpoint(port)}
    else:
        raise ValueError('Choose Local or a configured Server for the model installation.')
    _check_cancel(cancel)
    installed = matching_model(list_models(port), model)
    if not installed:
        pull_model(model, cancel, notify, timeout, **options)
        installed = matching_model(list_models(port), model)
    if not installed:
        raise ConnectionError('Ollama did not list the requested model after installation.')
    _check_cancel(cancel)
    return installed


def delete_local_model(model, cancel=None):
    """Remove one exact model through the local daemon and verify its inventory."""
    cancel = cancel if cancel is not None else threading.Event()
    require_local_runtime(cancel)
    installed = matching_model(list_models(LOCAL_PORT), model)
    if not installed:
        raise FileNotFoundError('This model is no longer installed in local Ollama.')
    _check_cancel(cancel)
    request = Request(
        _endpoint(LOCAL_PORT) + '/api/delete', method='DELETE',
        data=json.dumps({'model': installed, 'name': installed}).encode(),
        headers={'Content-Type': 'application/json'},
    )
    try:
        with _LOOPBACK_HTTP.open(request, timeout=30):
            pass
    except HTTPError as exc:
        raise RuntimeError(f'Local Ollama rejected the removal (HTTP {exc.code}).') from exc
    if matching_model(list_models(LOCAL_PORT), installed):
        raise RuntimeError('Ollama still lists the model after requesting its removal.')
    return installed


def ensure_local_model(model, emit=None, timeout=7200, cancel=None, allow_default_download=False):
    """Install a missing local Ollama model on demand; never attempts remote pulls."""
    cancel = cancel if cancel is not None else threading.Event()
    def progress(status, completed, total):
        if emit:
            detail = f' · {100 * completed // total}%' if total else ''
            emit('status', f'{model} · {status}{detail}')
    if model == DEFAULT_LOCAL_MODEL and allow_default_download:
        return bool(install_model(model, cancel, progress, timeout=timeout))
    require_local_runtime(cancel, progress)
    if matching_model(list_models(LOCAL_PORT), model):
        return True
    raise ConnectionError('Open Model downloads and install the selected model, or choose one already installed.')

def choose_route(config, preference='auto', benchmarks=None):
    ports={'local':LOCAL_PORT,'server':SERVER_PORT,'local_large':LOCAL_PORT}
    routes=[preference] if preference in ports else ['server','local']
    with ThreadPoolExecutor(max_workers=2) as pool:
        available=dict(zip(routes,pool.map(lambda r:inventory(ports[r]),routes)))
    valid={}
    for route in routes:
        model=config[route+'_model']
        if installed := matching_model(available[route], model):
            valid[route]=installed
    if not valid:raise ConnectionError('Selected models are unavailable. Start Ollama or reconnect your server, then check the model IDs in Settings.')
    scores={r['route']:r['elapsed_seconds'] for r in (benchmarks or {}).get('results',[]) if r.get('success') and r.get('quality_pass',False) and r.get('agent_pass',False) and r.get('model') in (config.get(r['route']+'_model'),valid.get(r['route']))}
    route=min(valid,key=lambda r:scores.get(r,10000 if r=='server' else 10001)) if preference=='auto' else next(iter(valid))
    reason='fastest measured available route' if route in scores and preference=='auto' else 'available coding route' if preference=='auto' else 'manual selection'
    return {'route':route,'url':_endpoint(ports[route]),'model':valid[route],'reason':reason}
