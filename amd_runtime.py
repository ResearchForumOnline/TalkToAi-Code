"""Keep the optional server Ollama tunnel usable without inspecting SSH secrets.

Legacy module/function names remain compatible with existing installations.
"""

import shutil
import socket
import subprocess
import tempfile
import threading
import time
from routing import SERVER_PORT, list_models, matching_model
from ssh_tools import validate_alias


PORT = SERVER_PORT
_TUNNEL_LOCK = threading.Lock()


def model_inventory(timeout=2):
    try:
        return list_models(PORT, timeout=timeout)
    except ConnectionError:
        return None


def port_in_use():
    try:
        with socket.create_connection(('127.0.0.1', PORT), timeout=1):
            return True
    except OSError:
        return False


def ensure_amd_tunnel(config, timeout=12, *, require_model=True, cancel=None):
    """Return (ready, detail). Start a tunnel only when its loopback port is free."""
    cancel = cancel if cancel is not None else threading.Event()
    while not _TUNNEL_LOCK.acquire(timeout=.1):
        if cancel.is_set():
            return False, 'Server connection stopped.'
    try:
        return _ensure_amd_tunnel(config, timeout, require_model, cancel)
    finally:
        _TUNNEL_LOCK.release()


def _ensure_amd_tunnel(config, timeout, require_model, cancel):
    label = ' '.join(str(config.get('server_label') or 'Server').split())[:60] or 'Server'
    model = str(config.get('server_model', '')).strip()

    def available(names):
        if require_model and not matching_model(names, model):
            return False, f'{label} is reachable, but the selected model is missing. Check Model downloads.'
        return True, f'{label} Ollama is reachable.'

    if cancel.is_set():
        return False, 'Server connection stopped.'
    names = model_inventory()
    if names is not None:
        return available(names)
    if port_in_use():
        return False, f'Port {PORT} is occupied but does not serve Ollama. Check the existing listener and retry.'
    try:
        alias = validate_alias(config.get('active_ssh_alias', ''))
    except ValueError:
        return False, f'Set a valid OpenSSH host alias in Connections, then retry {label}.'
    ssh = shutil.which('ssh')
    if not ssh:
        return False, 'OpenSSH is unavailable on this PC. Install or enable it, then retry.'
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    with tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen(
                [ssh, '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes',
                 '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=20',
                 '-o', 'ServerAliveCountMax=3', '-N', '-T',
                 '-L', f'127.0.0.1:{PORT}:127.0.0.1:11434', alias],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors, creationflags=flags,
            )
        except OSError as exc:
            return False, f'Could not start the {label} SSH tunnel: {exc}'
        keep = False
        try:
            deadline = time.monotonic() + timeout
            while not cancel.is_set() and time.monotonic() < deadline:
                if process.poll() is not None:
                    errors.seek(0, 2)
                    errors.seek(max(0, errors.tell() - 512))
                    detail = ' '.join(errors.read(512).decode(errors='replace').split())
                    return False, f'SSH could not connect to {label}. {detail or "Check your SSH alias and server."}'
                names = model_inventory(timeout=1)
                if names is not None:
                    keep = True
                    return available(names)
                cancel.wait(.4)
            if cancel.is_set():
                return False, 'Server connection stopped.'
            return False, f'{label} tunnel did not expose Ollama. Check the remote Ollama service and SSH forwarding.'
        finally:
            if not keep and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
