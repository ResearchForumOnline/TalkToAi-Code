"""Packaged-runtime smoke test. No user files, credentials or paid API calls."""
import json
import tempfile
import threading
import os
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path


def smoke(destination):
    result={}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers()
            self.wfile.write(b'<label>Player<input aria-label="Player"></label><button onclick="document.querySelector(\'h1\').textContent=\'Hello \'+document.querySelector(\'input\').value">Start</button><h1>Ready</h1>')
    server=None;browser=None
    try:
        import pywinauto
        result['computer_dependency']=pywinauto.__version__
        from browser_tools import BrowserTools
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        with tempfile.TemporaryDirectory(prefix='talktoai-release-') as folder:
            from process_jobs import ProcessJobs
            from workspace_outputs import read_batch, register_output
            from agent_core import ProjectTools
            tools=ProjectTools(folder,True)
            Path(folder,'report.txt').write_text('Packaged fixture evidence',encoding='utf-8')
            result['batch_read']=json.loads(read_batch(tools,[{'path':'report.txt'}]))['files'][0]['complete']
            result['output_registration']=bool(json.loads(register_output(tools,'report.txt'))['sha256'])
            if os.name=='nt':
                jobs=ProcessJobs(folder)
                try:
                    key=jobs.start('powershell.exe',['-NoProfile','-NonInteractive','-Command','Write-Output TALKTOAI_JOB_OK'])['id']
                    deadline=time.monotonic()+15
                    status=jobs.status(key)
                    while status['state']=='running' and time.monotonic()<deadline:status=jobs.status(key,wait_seconds=1)
                    result['managed_process']=status['state']=='completed' and status['exit_code']==0 and 'TALKTOAI_JOB_OK' in status['output']
                finally:jobs.close()
            from sample_projects import ensure_score_arena, SCORE_ARENA_FILES
            sample=ensure_score_arena(Path(__file__).resolve().parent,Path(folder)/'projects')
            result['bundled_example']=all((sample/name).is_file() for name in SCORE_ARENA_FILES)
            browser=BrowserTools(folder,threading.Event())
            browser.execute('open',f'http://127.0.0.1:{server.server_port}')
            browser.execute('fill','Player','Builder');browser.execute('click','Start')
            result['browser_interaction']='Hello Builder' in browser.execute('inspect')
            artifact=json.loads(browser.execute('screenshot'))
            result['browser_screenshot']=Path(artifact['artifact']).stat().st_size>1000
            from agent_core import image_for_model
            result['vision_attachment']=len(image_for_model(artifact['artifact']))>1000
            browser.close();browser=None
        result['passed']=all(result.get(key,False) for key in ('bundled_example','browser_interaction','browser_screenshot','vision_attachment','batch_read','output_registration')) and (os.name!='nt' or result.get('managed_process',False))
    except Exception as exc:result.update(passed=False,error=f'{type(exc).__name__}: {exc}')
    finally:
        if browser:browser.close()
        if server:server.shutdown();server.server_close()
    Path(destination).write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result['passed']
