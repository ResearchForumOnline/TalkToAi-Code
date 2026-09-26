"""Read-only, bounded diagnostics for the app's local and tunnel endpoints."""
import json
import time
from http.client import HTTPException
import urllib.request
from concurrent.futures import ThreadPoolExecutor

MAX_BYTES = 256 * 1024


def probe(port):
    try:
        deadline=time.monotonic()+3
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/tags', timeout=3) as response:
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


def diagnose(config):
    with ThreadPoolExecutor(max_workers=2) as pool:
        states = dict(zip((11434, 11435), pool.map(probe, (11434, 11435))))
    results = []
    for route, port in [('local', 11434), ('local_large', 11434), ('server', 11435)]:
        state = states[port]
        model = str(config.get(route + '_model', '')).strip()
        present = bool(model) and (model in state['models'] or model + ':latest' in state['models'])
        if state['state'] == 'invalid':
            action = 'The endpoint returned an invalid model inventory. Check that this port serves Ollama.'
        elif state['state'] == 'unreachable':
            action = 'Use Reconnect server model; check the SSH host alias in Connections.' if route == 'server' else 'Start or install Ollama, then check again.'
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
    lines = ['MODEL CONNECTION DIAGNOSTICS', 'Local tools run on this computer; Server changes the inference location.',
             'This check does not load or download a model, alter settings, or test inference.']
    for item in diagnose(config):
        label = str(config.get('server_label') or 'Server') if item['route'] == 'server' else item['route'].replace('_', ' ').title()
        lines.extend(['', f"{label} — 127.0.0.1:{item['port']} — {item['state']}",
                      'Selected: ' + item['model'], item['action'],
                      'Installed (up to 12): ' + ', '.join(item['available'])])
    return '\n'.join(lines)
