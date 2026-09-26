"""Keep the optional server Ollama tunnel usable without inspecting SSH secrets.

Legacy module/function names remain compatible with existing installations.
"""

import json
import shutil
import socket
import subprocess
import threading
import time
import urllib.request


PORT = 11435
_TUNNEL_LOCK = threading.Lock()


def model_inventory(timeout=2):
    try:
        request = urllib.request.Request(f'http://127.0.0.1:{PORT}/api/tags')
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
        return [model.get('name', '') for model in data.get('models', [])]
    except (OSError, ValueError, TypeError):
        return None


def port_in_use():
    try:
        with socket.create_connection(('127.0.0.1', PORT), timeout=1):
            return True
    except OSError:
        return False


def ensure_amd_tunnel(config, timeout=12):
    """Return (ready, detail). Start a tunnel only when its loopback port is free."""
    with _TUNNEL_LOCK:
        return _ensure_amd_tunnel(config, timeout)


def _ensure_amd_tunnel(config, timeout):
    label = ' '.join(str(config.get('server_label') or 'Server').split())[:60] or 'Server'
    model = str(config.get('server_model', '')).strip()
    names = model_inventory()
    if names is not None:
        if model in names or model + ':latest' in names:
            return True, f'{label} model available'
        return False, f'{label} is reachable, but the selected model is missing. Check Model choices.'
    if port_in_use():
        return False, 'Port 11435 is occupied but does not serve Ollama. Close that listener and retry.'
    alias = str(config.get('active_ssh_alias', '')).strip()
    if not alias or alias.startswith('-'):
        return False, f'Set an SSH host alias in Connections, then retry {label}.'
    ssh = shutil.which('ssh')
    if not ssh:
        return False, 'OpenSSH is unavailable on this PC. Install or enable it, then retry.'
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        process = subprocess.Popen(
            [ssh, '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes',
             '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=20',
             '-o', 'ServerAliveCountMax=3', '-N', '-L', '11435:127.0.0.1:11434', alias],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=flags,
        )
    except OSError as exc:
        return False, f'Could not start the {label} SSH tunnel: {exc}'
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False, f'SSH exited before the {label} tunnel connected. Check your SSH alias and server.'
        names = model_inventory(timeout=1)
        if names is not None:
            if model in names or model + ':latest' in names:
                return True, f'{label} tunnel connected and model available'
            return False, f'{label} tunnel connected, but the selected model is missing. Check Model choices.'
        time.sleep(0.4)
    return False, f'{label} tunnel did not become ready. Check SSH connectivity and server Ollama.'
