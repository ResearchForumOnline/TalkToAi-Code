"""Read-only, bounded diagnostics for the app's local and tunnel endpoints."""
import json
import math
import re
import shutil
import time
from datetime import datetime, timezone
from http.client import HTTPException
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from model_library import ollama_store
from routing import matching_model
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
        present = bool(model and matching_model(state['models'], model))
        if state['state'] == 'invalid':
            action = 'The endpoint returned an invalid model inventory. Check that this port serves Ollama.'
        elif state['state'] == 'unreachable':
            action = 'Use Reconnect server model and check the SSH host alias in Connections.' if route == 'server' else 'Open Models and setup. Choose a model to install or use Check Ollama connection to start your installed runtime.'
        elif not model:
            action = 'Set this model ID in Settings.'
        elif not present:
            action = 'Choose an installed model ID in Settings, or install the intended model on its inference machine.'
        elif route == 'server':
            action = 'The selected model is installed on the server.'
        else:
            action = 'The selected model is installed. Choose Test reply under Installed Ollama to check that it can answer.'
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


REPLY_CHECK_PROTOCOL = 'local-addition-v1'
REPLY_CHECK_PROMPT = 'What is 2 + 3? Reply with just the digit.'
_DIGEST = re.compile(r'(?:sha256:)?([0-9a-f]{64})', re.IGNORECASE)
_MEASUREMENT_KEYS = ('protocol', 'digest', 'outcome', 'checked_at', 'elapsed_seconds', 'first_token_seconds')


def _digest(value):
    match = _DIGEST.fullmatch(value) if isinstance(value, str) else None
    return match[1].lower() if match else None


def last_reply_check(history_dir, digest):
    """Read the latest completed check of this exact manifest and protocol."""
    from model_storage import library_root
    digest = _digest(digest)
    if not digest or history_dir is None:
        return None
    try:
        with (library_root(history_dir) / (digest + '.json')).open('rb') as stream:
            raw = stream.read(2049)
        if len(raw) > 2048:
            return None
        record = json.loads(raw)
        if (not isinstance(record, dict) or record.get('protocol') != REPLY_CHECK_PROTOCOL
                or record.get('digest') != digest or record.get('outcome') not in ('verified', 'different')):
            return None
        timestamp = datetime.fromisoformat(record['checked_at'])
        if timestamp.tzinfo is None:
            return None
        timestamp.astimezone()  # Also validate the local display conversion.
        for key in ('elapsed_seconds', 'first_token_seconds'):
            value = record[key]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                return None
        return {key:record[key] for key in _MEASUREMENT_KEYS}
    except (OSError, ValueError, OverflowError, KeyError, TypeError, RecursionError):
        return None


def measure_local_reply(model, cancel, notify, *, history_dir=None, port=None):
    """Run one fixed probe against local Ollama. Only completed, identified results can be saved."""
    from agent_core import _http_stream
    from conversation import _conversation_payload
    from model_library import DownloadCancelled
    from model_storage import library_root
    from routing import LOCAL_PORT, installed_model_records, require_local_runtime
    from session_store import atomic_write

    port = LOCAL_PORT if port is None else port
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Invalid local Ollama port.')
    if port == LOCAL_PORT:
        require_local_runtime(cancel, notify)
    def inventory():
        return {item['name']:_digest(item.get('digest')) for item in installed_model_records(port)}
    before = inventory()
    installed = matching_model(before, model)
    if not installed:
        raise ValueError('This model is no longer installed. Choose Refresh models.')
    notify(f'Checking whether {installed} answers 2 + 3 with the digit 5. A first reply may take time to load.', 0, 0)
    payload = _conversation_payload(installed, [{'role':'user', 'content':REPLY_CHECK_PROMPT}], {})
    payload['options'].update(num_predict=64, temperature=0, seed=0)
    started = time.monotonic()
    first = None
    preview = ''
    has_text = False
    exact = True
    complete = False
    detail = ''
    try:
        for frame in _http_stream(f'http://127.0.0.1:{port}', payload, cancel):
            if frame.get('error'):
                raise RuntimeError(str(frame['error'])[:500])
            content = (frame.get('message') or {}).get('content')
            if content:
                if not isinstance(content, str):
                    raise ValueError('Ollama returned an invalid text reply.')
                if first is None:
                    first = time.monotonic() - started
                preview += content[:max(0, 241-len(preview))]
                visible = content.strip()
                if visible:
                    exact = exact and not has_text and visible == '5'
                    has_text = True
            if frame.get('done') is True:
                complete = frame.get('done_reason') != 'length' and has_text
                break
    except InterruptedError as exc:
        raise DownloadCancelled('Reply check stopped.') from exc
    except (OSError, HTTPException, RuntimeError, ValueError) as exc:
        detail = str(exc)[:400]
    if cancel.is_set():
        raise DownloadCancelled('Reply check stopped.')
    result = {'protocol':REPLY_CHECK_PROTOCOL, 'model':installed, 'digest':None,
              'outcome':'incomplete' if not complete else 'verified' if exact else 'different',
              'checked_at':datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
              'elapsed_seconds':round(time.monotonic()-started, 3),
              'first_token_seconds':round(first, 3) if first is not None else None,
              'preview':' '.join(preview[:240].split()) + ('…' if len(preview)>240 else ''),
              'saved':False, 'note':detail}
    if not complete:
        return result
    # The tag may have been reassigned by another app while Ollama was loading.
    try:
        after = inventory()
    except ConnectionError:
        after = {}
    if cancel.is_set():
        raise DownloadCancelled('Reply check stopped.')
    if not before[installed] or after.get(installed) != before[installed]:
        result['note'] = 'The manifest changed or could not be verified. This check was not saved.'
        return result
    result['digest'] = before[installed]
    if history_dir is not None:
        receipt = {key:result[key] for key in _MEASUREMENT_KEYS}
        try:
            path = library_root(history_dir) / (result['digest'] + '.json')
            atomic_write(path, json.dumps(receipt, allow_nan=False).encode('utf-8'))
            result['saved'] = True
        except (OSError, ValueError):
            result['note'] = 'The reply check finished. Its history could not be saved on this PC.'
    return result


def reply_check_text(result):
    labels = {'verified':'Verified the expected answer (5)', 'different':'The model answered differently (expected 5)',
              'incomplete':'The model did not finish a text reply'}
    message = f'{result["model"]} · {labels[result["outcome"]]} · {result["elapsed_seconds"]:.1f}s.'
    if result['preview']:
        message += '\nReply: '+result['preview']
    if result['note']:
        message += '\n'+result['note']
    return message


def check_local_reply(model, cancel, notify):
    """Retain the human-readable check for callers outside the model library."""
    result = measure_local_reply(model, cancel, notify)
    message = reply_check_text(result)
    if result['outcome'] == 'incomplete':
        raise ConnectionError(message)
    return message
