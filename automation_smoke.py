"""Optional, bounded acceptance check against a user's own Ollama runtime.

Only a disposable fixture is exposed; mutating, remote, desktop and worker tools
are blocked by this test harness. This does not use an API provider or real work.
"""
import argparse
import json
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import patch
import agent_core as core


def run(url,model,seconds=120):
    cancel=threading.Event();timer=threading.Timer(seconds,cancel.set)
    events=[];started=time.monotonic();marker='FIXTURE-CHECK-OK'
    with tempfile.TemporaryDirectory(prefix='talktoai-automation-') as folder:
        (Path(folder)/'README.md').write_text('Public test fixture. Marker: '+marker,encoding='utf-8')
        tools=core.ProjectTools(folder,False,cancel);original=tools.execute
        def execute(name,args):
            if name not in ('list_files','read_file','project_info','file_fingerprint','project_map','search_code'):
                raise PermissionError('This acceptance fixture allows local read-only project inspection only.')
            return original(name,args)
        def emit(kind,data):
            events.append((kind,data))
            if kind in ('status','tool'):print(kind+': '+(data['name'] if kind=='tool' else str(data)),flush=True)
        error=None;timer.start()
        try:
            with patch.object(tools,'execute',side_effect=execute),patch.object(core,'ACTIVE_PROVIDER',None),patch.object(core,'DESKTOP_ACCESS',False),patch.object(core,'ACTIVE_REMOTE_ALLOWED',False),patch.object(core,'REMOTE_PILOT',False),patch.object(core,'run_subagent',side_effect=PermissionError('No workers in this fixture')):
                core._run_agent(url,model,[{'role':'user','content':'Perform a tiny read-only acceptance test. Load the context tool group using enable_tools, read README.md with read_file, and report its marker exactly. Do not edit, run commands, connect to a server, use workers or inspect outside this project. Keep the final answer under 30 words.'}],folder,False,cancel,emit,4,{'num_ctx':8192},tools)
        except Exception as exc:error=type(exc).__name__+': '+str(exc)
        finally:timer.cancel()
    replies=[v.get('content','') for k,v in events if k=='message' and v.get('role')=='assistant' and not v.get('tool_calls')]
    executed=[v for k,v in events if k=='tool']
    names=[v['name'] for v in executed]
    result={'model':model,'elapsed_seconds':round(time.monotonic()-started,2),'cancelled':cancel.is_set(),'tools':names,
            'loaded_context':any(v['name']=='enable_tools' and v['args'].get('group')=='context' for v in executed),
            'read_fixture':'read_file' in names,'marker_reported':any(marker in text for text in replies),'error':error}
    result['passed']=result['loaded_context'] and result['read_fixture'] and result['marker_reported'] and not error and not result['cancelled']
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:11434');parser.add_argument('--model',required=True);parser.add_argument('--seconds',type=int,default=120);parser.add_argument('--output',required=True)
    args=parser.parse_args();result=run(args.url,args.model,args.seconds)
    Path(args.output).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2));raise SystemExit(0 if result['passed'] else 1)
