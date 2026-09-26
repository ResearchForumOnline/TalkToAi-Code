"""Select an available installed model before executing any tools."""
import json
import urllib.request
import subprocess
import shutil
import time
import threading

_LOCAL_START_LOCK = threading.Lock()
from concurrent.futures import ThreadPoolExecutor

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
        ollama = shutil.which('ollama')
        if not ollama: return False
        try:
            subprocess.Popen([ollama, 'serve'], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError:
            return False
        for _ in range(10):
            if ready(): return True
            time.sleep(.3)
        return False


def ensure_local_model(model, emit=None, timeout=7200):
    """Install a missing local Ollama model on demand; never attempts remote pulls."""
    ensure_local_runtime()
    if model_present(11434, model): return True
    ollama=shutil.which('ollama')
    if not ollama:
        raise ConnectionError('Ollama is not installed or is not on PATH. Install Ollama, then retry.')
    if emit: emit('status', 'Downloading local model '+model+' Â· this happens once and may take a while')
    result=subprocess.run([ollama,'pull',model],capture_output=True,text=True,timeout=timeout)
    if result.returncode!=0:
        detail=(result.stderr or result.stdout or 'ollama pull failed').strip()[-1200:]
        raise RuntimeError('Could not download '+model+': '+detail)
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
    if not valid:raise ConnectionError('Selected models are unavailable. Start Ollama / the AMD tunnel or check Runtime settings.')
    scores={r['route']:r['elapsed_seconds'] for r in (benchmarks or {}).get('results',[]) if r.get('success') and r.get('quality_pass',False) and r.get('agent_pass',False) and r.get('model')==config.get(r['route']+'_model')}
    if preference=='auto':valid.sort(key=lambda r:scores.get(r,10000 if r=='server' else 10001))
    route=valid[0]
    reason='fastest measured available route' if route in scores and preference=='auto' else 'available coding route' if preference=='auto' else 'manual selection'
    return {'route':route,'url':f'http://127.0.0.1:{ports[route]}','model':config[route+'_model'],'reason':reason}
