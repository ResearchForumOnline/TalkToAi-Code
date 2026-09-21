"""Select an available installed model before executing any tools."""
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor

def inventory(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/tags',timeout=3) as r:
            return [m['name'] for m in json.load(r).get('models',[])]
    except Exception:return []

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
