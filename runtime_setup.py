"""Set up the local inference runtime after an explicit model install."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from model_library import DownloadCancelled
import routing


_install_lock = threading.Lock()
_manual = 'Choose Get Ollama to install it from ollama.com, then retry.'


def _winget():
    # Use the per-user App Installer execution alias even when Studio inherited
    # a stale PATH. Never resolve an executable from the current project.
    local = os.environ.get('LOCALAPPDATA')
    if local:
        alias = Path(local) / 'Microsoft' / 'WindowsApps' / 'winget.exe'
        if alias.is_file():
            return str(alias)
    raise ConnectionError('Windows Package Manager is unavailable. ' + _manual)


def _cancelled(cancel):
    if cancel.is_set():
        raise DownloadCancelled('Stopped local model setup.')


def _install(cancel, notify):
    command = [_winget(), 'install', '--id', 'Ollama.Ollama', '--exact',
               '--source', 'winget', '--scope', 'user', '--silent', '--no-upgrade',
               '--accept-source-agreements', '--disable-interactivity']
    _cancelled(cancel)
    notify('Installing Ollama with Windows Package Manager', 0, 0)
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except OSError as exc:
        raise ConnectionError('Windows Package Manager could not start. ' + _manual) from exc
    started = time.monotonic()
    next_update = 10
    try:
        while process.poll() is None:
            if cancel.wait(0.5):
                raise DownloadCancelled('Stopped waiting for Ollama setup. Check the connection before retrying.')
            elapsed = int(time.monotonic() - started)
            if elapsed >= 1800:
                raise TimeoutError('Windows Package Manager took over 30 minutes. Check the connection or select Get Ollama.')
            if elapsed >= next_update:
                notify(f'Windows is downloading or installing Ollama · {elapsed // 60}m {elapsed % 60}s', 0, 0)
                next_update = elapsed + 10
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    if process.returncode and not routing.ollama_executable():
        code = process.returncode & 0xffffffff
        raise ConnectionError(f'Windows Package Manager could not install Ollama (0x{code:08X}). ' + _manual)


def require_model_runtime(cancel, notify):
    """Prepare this PC for a user-initiated install, with one setup at a time."""
    _cancelled(cancel)
    try:
        return routing.require_local_runtime(cancel, notify)
    except routing.OllamaMissingError:
        if sys.platform != 'win32':
            raise
    while not _install_lock.acquire(timeout=0.25):
        _cancelled(cancel)
    try:
        _cancelled(cancel)
        if not routing.ollama_executable():
            _install(cancel, notify)
        return routing.require_local_runtime(cancel, notify)
    finally:
        _install_lock.release()
