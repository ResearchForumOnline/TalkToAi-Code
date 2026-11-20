"""Read-only, bounded diagnostics for the app's local and tunnel endpoints."""
import json
import shutil
import time
from http.client import HTTPException
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from model_library import ollama_store
from updates import VERSION

MAX_BYTES = 256 * 1024
_loopback = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def running_probe(port):
    """Read Ollama residency without loading a model or inferring GPU inventory."""
    try:
        deadline=time.monotonic()+3
        with _loopback.open(f'http://127.0.0.1:{port}/api/ps',timeout=3) as response:
            chunks=[];size=0;read=getattr(response,'read1',response.read)
            while size<=MAX_BYTES:
                if time.monotonic()>deadline:raise TimeoutError('Residency deadline')
                chunk=read(min(8192,MAX_BYTES+1-size))
                if not chunk:break
                chunks.append(chunk);size+=len(chunk)
        if size>MAX_BYTES:raise ValueError('Residency response too large')
        data=json.loads(b''.join(chunks));models=data.get('models') if isinstance(data,dict) else None
        if not isinstance(models,list):raise ValueError('Invalid residency response')
        result=[]
        for model in models:
            if not isinstance(model,dict) or not isinstance(model.get('name'),str):raise ValueError('Invalid running model')
            size=model.get('size');vram=model.get('size_vram');ctx=model.get('context_length')
            valid=lambda value:type(value) is int and value>=0
            result.append({'name':model['name'],'size':size if valid(size) else None,
                           'size_vram':vram if valid(vram) else None,'context_length':ctx if valid(ctx) else None})
        return {'state':'reachable','models':result}
    except (ValueError,UnicodeError,RecursionError,HTTPException):return {'state':'invalid','models':[]}
    except OSError:return {'state':'unreachable','models':[]}


def residency_summary(state,model):
    if state.get('state')!='reachable':return 'Running-model residency unavailable; CPU/GPU use is unknown.'
    aliases={model,model+':latest'}
    current=next((item for item in state.get('models',[]) if item['name'] in aliases),None)
    if current is None:return 'Selected model is not loaded. The next request may need loading time; GPU availability is not established.'
    vram=current.get('size_vram');size=current.get('size')
    if vram is None:mode='CPU/GPU allocation unknown'
    elif vram==0:mode='CPU/system-memory execution (Ollama reports zero model VRAM)'
    elif size and vram>=size:mode='GPU-resident model'
    else:mode='GPU offload with possible system-memory allocation'
    details=[]
    if size is not None:details.append(f'model allocation {size/1024**3:.1f} GiB')
    if vram is not None:details.append(f'VRAM {vram/1024**3:.1f} GiB')
    if current.get('context_length'):details.append(f"context {current['context_length']:,}")
    return mode+(' — '+', '.join(details) if details else '')+'. Residency is not a speed benchmark.'


def probe(port):
    try:
        deadline=time.monotonic()+3
        with _loopback.open(f'http://127.0.0.1:{port}/api/tags', timeout=3) as response:
            chunks=[];size=0
            read=getattr(response,'read1',response.read)
            while size <= MAX_BYTES:
                if time.monotonic() > deadline:raise TimeoutError('Inventory deadline')
                chunk=read(min(8192,MAX_BYTES+1-size))
                if not chunk:break
                chunks.append(chunk);size+=len(chunk)
            raw=b''.join(chunks)
        if len(raw) > MAX_BYTES:
            return {'state': 'invalid', 'models': []}
        data = json.loads(raw)
        models = data.get('models') if isinstance(data, dict) else None
        if not isinstance(models, list) or any(not isinstance(m, dict) or not isinstance(m.get('name'), str) for m in models):
            return {'state': 'invalid', 'models': []}
        return {'state': 'reachable', 'models': sorted({m['name'] for m in models})}
    except (ValueError, UnicodeError, RecursionError, HTTPException):
        return {'state': 'invalid', 'models': []}
    except OSError:
        return {'state': 'unreachable', 'models': []}


def diagnose(config, include_server=True):
    routes = [('local', 11434), ('local_large', 11434)]
    if include_server:
        routes.append(('server', 11435))
    ports = sorted({port for _, port in routes})
    with ThreadPoolExecutor(max_workers=2) as pool:
        states = dict(zip(ports, pool.map(probe, ports)))
    results = []
    for route, port in routes:
        state = states[port]
        model = str(config.get(route + '_model', '')).strip()
        present = bool(model) and (model in state['models'] or model + ':latest' in state['models'])
        if state['state'] == 'invalid':
            action = 'The endpoint returned an invalid model inventory. Check that this port serves Ollama.'
        elif state['state'] == 'unreachable':
            action = 'Use Reconnect server model and check the SSH host alias in Connections.' if route == 'server' else 'Open Models and setup. Choose a model to install or use Check Ollama connection to start your installed runtime.'
        elif not model:
            action = 'Set this model ID in Settings.'
        elif not present:
            action = 'Choose an installed model ID in Settings, or install the intended model on its inference machine.'
        else:
            action = 'Selected model is installed. A coding trial is still needed to measure its ability and speed.'
        results.append({'route': route, 'port': port, 'state': state['state'], 'model': model,
                        'installed': present, 'available': state['models'][:12], 'action': action})
    return results


def report(config):
    route = config.get('preferred_route', 'local')
    server = route == 'server' or bool(config.get('active_ssh_alias'))
    ports = (11434, 11435) if server else (11434,)
    lines = [f'TalkToAi Code {VERSION} · Model diagnostics',
             'Selected route: ' + str(route).replace('_', ' ').title(),
             'This check does not load or download a model.']
    if not server:
        lines.append('Local downloads and local chat use Ollama on this PC. SSH is optional.')
    try:
        path = ollama_store()
        volume = next(p for p in (path, *path.parents) if p.exists())
        lines.append(f'Free space on the local model drive: {shutil.disk_usage(volume).free / 2**30:.1f} GiB')
    except (OSError, StopIteration):
        lines.append('Free space on the configured model drive is unavailable.')
    with ThreadPoolExecutor(max_workers=2) as pool:
        running=dict(zip(ports,pool.map(running_probe,ports)))
    for item in diagnose(config, include_server=server):
        label = str(config.get('server_label') or 'Server') if item['route'] == 'server' else item['route'].replace('_', ' ').title()
        lines.extend(['', f"{label} — 127.0.0.1:{item['port']} — {item['state']}",
                      'Selected: ' + item['model'], item['action'],residency_summary(running[item['port']],item['model']),
                      'Installed (up to 12): ' + ', '.join(item['available'])])
    return '\n'.join(lines)
