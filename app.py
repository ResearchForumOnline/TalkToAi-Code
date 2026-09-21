import json, os, pathlib, subprocess, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

ROOT = pathlib.Path(__file__).resolve().parent
DEFAULT_PROJECT = pathlib.Path(os.environ.get('LOCAL_CODEX_PROJECT', str(ROOT.parent))).resolve()
CONFIG = ROOT / 'config.json'

def load_config():
    try: return json.loads(CONFIG.read_text(encoding='utf-8'))
    except Exception: return {'project': str(DEFAULT_PROJECT), 'local_model': 'qwen3.5:4b', 'local_large_model': 'smtek/Qwen3.8-27B', 'server_model': 'openzero-qwen3-coder-30b-a3b-q3', 'backend': 'local'}

def safe_path(project, rel):
    base = pathlib.Path(project).expanduser().resolve()
    p = (base / rel).resolve()
    if p != base and base not in p.parents: raise ValueError('Path escapes the selected project')
    return p

def api_chat(url, model, messages):
    body = json.dumps({'model': model, 'messages': messages, 'stream': False, 'options': {'num_ctx': 8192, 'temperature': 0.2}}).encode()
    req = urllib.request.Request(url.rstrip('/') + '/api/chat', data=body, headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=600) as r: return json.loads(r.read().decode())

def ollama_ready(url):
    try:
        with urllib.request.urlopen(url.rstrip('/') + '/api/tags', timeout=3) as r:
            return {'online': True, 'models': [m.get('name') for m in json.loads(r.read().decode()).get('models', [])]}
    except Exception as e: return {'online': False, 'error': str(e)}

def engine_summary(project):
    root=pathlib.Path(project).resolve(); engines=[]
    if (root/'project.godot').exists(): engines.append('Godot')
    if (root/'ProjectSettings'/'ProjectVersion.txt').exists(): engines.append('Unity')
    # Do not recurse through a broad workspace on every status request.
    # Deeper Blender assets stay discoverable in the file browser.
    if list(root.glob('*.blend')) or (root/'assets').is_dir(): engines.append('Blender assets')
    return engines or ['General workspace']

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send_json(self, data, code=200):
        raw=json.dumps(data).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        if self.path == '/':
            raw=(ROOT/'index.html').read_bytes(); self.send_response(200); self.send_header('Content-Type','text/html'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if self.path.startswith('/api/tree'):
            c=load_config(); base=pathlib.Path(c['project']).resolve(); items=[]
            for p in sorted(base.rglob('*')):
                if any(x in p.parts for x in {'.git','node_modules','.venv','venv','__pycache__'}): continue
                if p.is_file() and p.stat().st_size < 2_000_000: items.append(str(p.relative_to(base)))
                if len(items)>=500: break
            self.send_json({'project':str(base),'files':items}); return
        if self.path == '/api/status':
            c=load_config(); self.send_json({'brand':'TalkToAi Code','project':c['project'],'engine':engine_summary(c['project']),'local':ollama_ready('http://127.0.0.1:11434'),'server':ollama_ready('http://127.0.0.1:11435'),'identity_url':'https://agentzero.talktoai.org/','openzero_url':'https://openzero.talktoai.org/','legacy_workbench_url':'https://zerothink.talktoai.org/'}); return
        self.send_error(404); return
    def do_POST(self):
        try:
            data=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0')))); c=load_config(); action=data.get('action')
            if action=='config':
                c.update({k:data[k] for k in ('project','backend') if k in data}); CONFIG.write_text(json.dumps(c,indent=2),encoding='utf-8'); self.send_json(c); return
            if action=='read': self.send_json({'text':safe_path(c['project'],data['path']).read_text(encoding='utf-8')}); return
            if action=='write':
                target=safe_path(c['project'],data['path']); target.parent.mkdir(parents=True,exist_ok=True); target.write_text(data.get('text',''),encoding='utf-8'); self.send_json({'ok':True,'path':str(target)}); return
            if action=='chat':
                url='http://127.0.0.1:11434'; model=c['local_model']
                if c.get('backend')=='server': url='http://127.0.0.1:11435'; model=c['server_model']
                system='You are TalkToAi Code, a private local coding agent in the TalkToAI ecosystem. Work like a careful game-development engineer: inspect before proposing edits, scope work to the project, favor small testable milestones, use concise answers to preserve local-model context, and never claim a file was changed unless the user saves it through this UI. AgentZero web sign-in is a separate browser identity flow; never request or expose credentials. Project root: '+c['project']
                self.send_json(api_chat(url,model,[{'role':'system','content':system}]+data.get('messages',[]))); return
            if action=='command':
                p=subprocess.run(data.get('command',''),cwd=c['project'],shell=True,capture_output=True,text=True,timeout=120)
                self.send_json({'returncode':p.returncode,'stdout':p.stdout[-12000:],'stderr':p.stderr[-12000:]}); return
            self.send_json({'error':'unknown action'},400)
        except Exception as e: self.send_json({'error':str(e)},500)

if __name__=='__main__':
    load_config(); print('TalkToAi Code at http://127.0.0.1:8766'); ThreadingHTTPServer(('127.0.0.1',8766),Handler).serve_forever()
