"""Select an available installed model before executing any tools."""
import json
import os
from pathlib import Path
import urllib.request
import subprocess
import shutil
import time
import threading

_LOCAL_START_LOCK = threading.Lock()
from concurrent.futures import ThreadPoolExecutor
from model_library import DEFAULT_LOCAL_MODEL, QWEN_LOCAL_MODEL, pull_model


def ollama_executable():
    command = shutil.which('ollama')
    if command:
        return command
    local = os.environ.get('LOCALAPPDATA')
    if os.name == 'nt' and local:
        installed = Path(local) / 'Programs' / 'Ollama' / 'ollama.exe'
        if installed.is_file():
            return str(installed)
    return None

def inventory(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/tags',timeout=3) as r:
            return [m['name'] for m in json.load(r).get('models',[])]
    except Exception:return []

def model_present(port, model):
    names=inventory(port)
    return model in names or model+':latest' in names

def ensure_local_runtime():
    """Start an installed local Ollama service without downloading a model."""
    with _LOCAL_START_LOCK:
        def ready():
            try:
                with urllib.request.urlopen('http://127.0.0.1:11434/api/version', timeout=1) as response:
                    return response.status == 200
            except (OSError, ValueError):
                return False
        if ready(): return True
        ollama = ollama_executable()
        if not ollama: return False
        try:
            subprocess.Popen([ollama, 'serve'], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                env={**os.environ, 'OLLAMA_HOST': '127.0.0.1:11434'})
        except OSError:
            return False
        for _ in range(10):
            if ready(): return True
            time.sleep(.3)
        return False


def ensure_local_model(model, emit=None, timeout=7200, cancel=None, allow_default_download=False):
    """Install a missing local Ollama model on demand; never attempts remote pulls."""
    if not ensure_local_runtime():
        raise ConnectionError('Start or install Ollama on this computer. TalkToAi Code connects on 127.0.0.1:11434.')
    if model_present(11434, model): return True
    if model == QWEN_LOCAL_MODEL or (model == DEFAULT_LOCAL_MODEL and not allow_default_download):
        raise ConnectionError('Open Model downloads and install the selected model, or choose one already installed.')
    def progress(status, completed, total):
        if emit:
            detail = f' · {100 * completed // total}%' if total else ''
            emit('status', f'{model} · {status}{detail}')
    pull_model(model, cancel if cancel is not None else threading.Event(), progress, timeout)
    return model_present(11434, model)

def choose_route(config, preference='auto', benchmarks=None):
    ports={'local':11434,'server':11435,'local_large':11434}
    routes=[preference] if preference in ports else ['server','local']
    with ThreadPoolExecutor(max_workers=2) as pool:
        available=dict(zip(routes,pool.map(lambda r:inventory(ports[r]),routes)))
    valid=[]
    for route in routes:
        model=config[route+'_model']
        if model in available[route] or model+':latest' in available[route]:valid.append(route)
    if not valid:raise ConnectionError('Selected models are unavailable. Start Ollama or reconnect your server, then check the model IDs in Settings.')
    scores={r['route']:r['elapsed_seconds'] for r in (benchmarks or {}).get('results',[]) if r.get('success') and r.get('quality_pass',False) and r.get('agent_pass',False) and r.get('model')==config.get(r['route']+'_model')}
    if preference=='auto':valid.sort(key=lambda r:scores.get(r,10000 if r=='server' else 10001))
    route=valid[0]
    reason='fastest measured available route' if route in scores and preference=='auto' else 'available coding route' if preference=='auto' else 'manual selection'
    return {'route':route,'url':f'http://127.0.0.1:{ports[route]}','model':config[route+'_model'],'reason':reason}
