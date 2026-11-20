"""Packaged-runtime smoke test. No user files, credentials or paid API calls."""
import json
import tempfile
import threading
import os
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path


_MODEL_FIXTURE = b'GGUFtalktoai-store-fixture'


def _check_store_model_upgrade(folder, port):
    """Exercise a real HTTP range transfer using the exact 8.8 Store Qwen cache keys."""
    import hashlib
    from unittest.mock import patch
    import model_library
    from model_storage import display_path, native_path

    entry = model_library.ModelFile(model_library.QWEN_FILE.name, len(_MODEL_FIXTURE),
                                    hashlib.sha256(_MODEL_FIXTURE).hexdigest())
    with tempfile.TemporaryDirectory(dir=native_path(folder)) as temp:
        root = (Path(temp) / 'AppData' / 'Local' / 'Packages' / 'talktoai.TalkToAiCode-AICodingStudio_hj8h1ht46zkh6'
                / 'LocalCache' / 'Local' / 'TalkToAiCodeStore' / 'models')
        legacy = (root / '.partial' / 'd3c802c79ad2855215276e1093f5332d495ffde32f28322c4513398b92ce39ad'
                  / 'a33b3ef7ee7f07bcfed9405d1af59fdec88d96134efb43e76e945b717b61ca1c')
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(_MODEL_FIXTURE[:4])
        args = (model_library.QWEN_REPO, model_library.QWEN_REVISION, [entry], display_path(root),
                '', threading.Event(), lambda *_: None)
        with patch.object(model_library, 'HF_URL', f'http://127.0.0.1:{port}/model-check'):
            _, [verified] = model_library.download_files(*args)
            model_library.download_files(*args)
        return len(str(display_path(legacy))) > 260 and not legacy.exists() and verified.read_bytes() == _MODEL_FIXTURE


def smoke(destination):
    from model_library import DEFAULT_LOCAL_MODEL
    from runtime_diagnostics import REPLY_CHECK_PROMPT, last_reply_check, measure_local_reply
    result={}
    chat_requests=[]
    model_ranges=[]
    desktop_worker_check=os.name=='nt' and os.environ.get('TALKTOAI_VERIFY_WINDOWS_WORKER')=='1'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            if self.path != '/api/chat':
                self.send_error(404)
                return
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            chat_requests.append(request)
            text = 'I will remember Luna.' if len(chat_requests) == 1 else 'Luna.'
            if request['messages'][-1]['content'] == REPLY_CHECK_PROMPT:
                text = '</think>\n\n5'
            body = (json.dumps({'message': {'content': text}, 'done': True}) + '\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-ndjson')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/api/tags':
                data = json.dumps({'models':[{'name':DEFAULT_LOCAL_MODEL,'digest':'a'*64}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if self.path.startswith('/model-check/'):
                requested = self.headers.get('Range')
                model_ranges.append(requested)
                data = _MODEL_FIXTURE
                if requested == 'bytes=4-':
                    self.send_response(206)
                    self.send_header('Content-Range', f'bytes 4-{len(data)-1}/{len(data)}')
                    data = data[4:]
                else:
                    self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers()
            if self.path=='/report':
                self.wfile.write(b'<title>Report</title><h1>Packaged report ready</h1>')
            else:
                self.wfile.write(b'<label>Player<input aria-label="Player"></label><button aria-label="Start" onclick="document.querySelector(\'h1\').textContent=\'Hello \'+document.querySelector(\'input\').value">+</button><h1>Ready</h1><a href="/report" target="_blank">Open report</a>')
    server=None;browser=None
    try:
        if os.name=='nt':
            import pywinauto
            result['computer_dependency']=pywinauto.__version__
            if getattr(__import__('sys'),'frozen',False):
                import ctypes
                user32=ctypes.windll.user32
                user32.GetThreadDpiAwarenessContext.restype=ctypes.c_void_p
                user32.AreDpiAwarenessContextsEqual.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
                result['per_monitor_v2']=bool(user32.AreDpiAwarenessContextsEqual(user32.GetThreadDpiAwarenessContext(),ctypes.c_void_p(-4)))
        else:
            result['computer_dependency']='Windows-only PC Pilot; portable browser checks remain available'
        from ethics_policy import verify_release_policy
        result['operating_policy']=verify_release_policy()['status']=='verified'
        from providers import ProviderProfile, configure_request
        free_profile=ProviderProfile('Packaged free fixture','https://openrouter.ai/api/v1','openrouter/free',cost_tier='free',free_only=True)
        free_request=configure_request(free_profile,{'models':['paid/fixture'],'plugins':[{'id':'web'}]}, {})
        result['free_provider_policy']=(free_request['model']=='openrouter/free'
            and free_request['provider']['max_price']=={'prompt':0,'completion':0,'request':0,'image':0}
            and free_request['provider']['require_parameters'] and 'models' not in free_request and 'plugins' not in free_request)
        if os.name=='nt':
            from control_cancel import EscapeCancel
            escape=EscapeCancel()
            try:result['escape_hook_registration']=escape.arm(lambda:None)
            finally:escape.disarm()
        from browser_tools import BrowserTools
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        import agent_core
        previous_provider, previous_pool = agent_core.ACTIVE_PROVIDER, agent_core.ACTIVE_PROVIDER_POOL
        chat_history = []
        visible_replies = []
        try:
            agent_core.set_active_provider(None)
            for prompt in ('Remember Luna.', 'What was her name?'):
                chat_history.append({'role': 'user', 'content': prompt})
                events = []
                agent_core.run_conversation(f'http://127.0.0.1:{server.server_port}', 'fixture-chat', chat_history,
                                             threading.Event(), lambda kind, value: events.append((kind, value)))
                visible_replies.append(''.join(value for kind, value in events if kind == 'delta'))
                chat_history.extend(value for kind, value in events if kind == 'message')
        finally:
            agent_core.set_active_provider(previous_provider, previous_pool)
        result['conversation_round_trip'] = (visible_replies == ['I will remember Luna.', 'Luna.']
            and len(chat_requests) == 2 and all('tools' not in request for request in chat_requests)
            and chat_requests[1]['messages'][1:] == chat_history[:3]
            and chat_history[-1] == {'role': 'assistant', 'content': 'Luna.'})
        with tempfile.TemporaryDirectory(prefix='talktoai-release-') as folder:
            check_directory = Path(folder, 'model-checks')
            check = measure_local_reply(DEFAULT_LOCAL_MODEL, threading.Event(), lambda *_: None,
                                        port=server.server_port, history_dir=check_directory)
            saved = last_reply_check(check_directory, check['digest'])
            result['installed_model_reply_check'] = (check['outcome'] == 'verified' and check['saved']
                and saved is not None and saved['outcome'] == 'verified' and 'preview' not in saved)
            result['openzero_template_prefix_clean'] = check['preview'] == '5'
            from process_jobs import ProcessJobs
            from workspace_outputs import read_batch, register_output
            from agent_core import ProjectTools
            tools=ProjectTools(folder,True)
            Path(folder,'report.txt').write_text('Packaged fixture evidence',encoding='utf-8')
            chain=json.loads(tools.execute('invoke_chain',{'steps':json.dumps([
                {'tool':'project_info'}, {'tool':'read_file','args':{'path':'report.txt'}}
            ])}))
            result['read_only_chain']=(chain['status']=='completed' and chain['completed']==2
                                       and chain['steps'][1]['result']=='Packaged fixture evidence')
            from remote_intent import requests_remote_work
            result['remote_intent']=(requests_remote_work('Check my VPS project')
                                     and not requests_remote_work('Use my server model to make a game'))
            from task_navigation import natural_desktop_target
            fixture_game=Path(folder,'Desktop','Work','nightfall','native')
            fixture_game.mkdir(parents=True)
            (fixture_game/'project.godot').write_text('[application]\nconfig/name="NIGHTFALL"\n',encoding='utf-8')
            target=natural_desktop_target('Find my NIGHTFALL game on Desktop',home=folder)
            result['named_project_target']=(Path(target.get('root',''))==fixture_game.resolve()
                                            and not target.get('truncated'))
            result['batch_read']=json.loads(read_batch(tools,[{'path':'report.txt'}]))['files'][0]['complete']
            result['output_registration']=bool(json.loads(register_output(tools,'report.txt'))['sha256'])
            from project_playbooks import save_playbook, find_playbooks
            save_playbook(folder,'Fixture workflow','Use for this release fixture',
                          ['Read report.txt'],'Fixture file exists',['report.txt'])
            result['project_playbooks']=find_playbooks(folder,'Fixture workflow')['entries'][0]['evidence_status']=='current'
            from research_journal import record_experiment, compare_experiments
            first=json.loads(record_experiment(folder,'Fixture baseline','fixture','reported',{'steps':10}))
            second=json.loads(record_experiment(folder,'Fixture candidate','fixture','reported',{'steps':8}))
            comparison=json.loads(compare_experiments(folder,first['id'],second['id'],'steps'))
            result['research_comparison']=comparison['candidate_minus_baseline']==-2 and bool(comparison['warnings'])
            from app_preferences import AppPreferenceManager
            preference_config={}
            preference_path=Path(folder,'preference-audit.jsonl')
            preference_manager=AppPreferenceManager(preference_config,lambda:None,preference_path)
            changed=preference_manager.change({'keep_going':False})
            restored=preference_manager.rollback(changed['change_id'])
            result['audited_preferences']=(changed['changed'] and restored['changed']
                                          and preference_manager.inspect()['preferences']['keep_going'] is True)
            from routing_evaluation import audit_routing_evaluation
            routing_fixture={'schema':'talktoai.routing-evaluation.v1','dataset':'packaged fixture',
                'split':'held_out','event_definition':'Review required under fixture rubric',
                'rubric_version':'fixture-v1','baseline_version':'baseline','candidate_version':'candidate',
                'cases':[{'id':'one','family':'fixture','actual_review_required':True,
                    'baseline':{'route':'allow'},'candidate':{'route':'review'}}]}
            Path(folder,'routing-fixture.json').write_text(json.dumps(routing_fixture),encoding='utf-8')
            routing_result=json.loads(audit_routing_evaluation(folder,'routing-fixture.json'))
            result['routing_audit']=(routing_result['baseline']['counts']['false_allow']==1
                                     and routing_result['candidate']['counts']['false_allow']==0)
            from candidate_apply import preview_application, apply_candidate, rollback_application
            result['candidate_controls_imported']=all(callable(item) for item in
                (preview_application,apply_candidate,rollback_application))
            from workspace_change_evidence import WorkspaceChangeTracker
            tracker=WorkspaceChangeTracker(folder)
            Path(folder,'fixture.py').write_text('value = 1\n',encoding='utf-8')
            observed=tracker.observe('packaged fixture')
            result['source_change_evidence']=observed['count']==1 and observed['complete']
            from offline_assistant import run_offline
            local_report=run_offline(folder,command='python_syntax')
            result['offline_program']=(local_report['status']=='completed'
                and local_report.get('findings')==0 and local_report['metrics']['model_calls']==0
                and local_report['metrics']['network_calls']==0)
            from project_templates import create_project_template
            starter=create_project_template(folder,'browser-game','offline-game')
            result['offline_starter']=(starter['status']=='created'
                and Path(starter['project_path'],'index.html').is_file()
                and Path(starter['project_path'],'game.js').stat().st_size>1000)
            if os.name=='nt':
                if desktop_worker_check:
                    from computer_tools import ComputerSession
                    desktop=ComputerSession(folder,threading.Event())
                    try:
                        windows=json.loads(desktop.execute('windows'))
                        result['desktop_worker']=bool(windows) and all(entry.get('handle') for entry in windows)
                    finally:desktop.close()
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
            from model_library import DEFAULT_LOCAL_MODEL, OPENZERO_FILE, QWEN_FILE, QWEN_REPO, gguf_selection, repository_id
            from model_library_dialog import ModelLibraryDialog
            result['model_library']=(callable(ModelLibraryDialog) and DEFAULT_LOCAL_MODEL.startswith('hf.co/')
                                     and repository_id('https://huggingface.co/'+QWEN_REPO)==QWEN_REPO
                                     and gguf_selection([QWEN_FILE])
                                     and QWEN_FILE.size>OPENZERO_FILE.size>0)
            result['store_model_download_upgrade'] = (_check_store_model_upgrade(folder, server.server_port)
                                                      and model_ranges == ['bytes=4-'])
            if getattr(__import__('sys'), 'frozen', False):
                from asset_library import PACKS, install_pack
                project = Path(folder) / 'offline-kenney-assets'
                project.mkdir()
                pack = next(item for item in PACKS if item['slug'] == 'tiny-dungeon')
                bundle = Path(__file__).resolve().parent / 'assets' / 'kenney-cc0.zip'
                result['bundled_game_assets'] = bundle.is_file() and install_pack(project, pack, threading.Event()) > 0
            from integration_dialog import integration_dialog
            from mcp_plugins import McpPlugins
            plugins=McpPlugins(folder,{'fixture':{'command':'fixture-command','enabled':True}},threading.Event())
            try:
                result['mcp_plugins']=callable(integration_dialog) and plugins.execute('plugins','','','')['enabled_plugins']==['fixture']
            finally:plugins.close()
            from project_data_plugin import inspect_project_data
            data_fixture=Path(folder,'data.csv')
            data_fixture.write_text('Player,Score\nBuilder,42\n',encoding='utf-8')
            result['project_data']=inspect_project_data(data_fixture)['preview']==[['Player','Score'],['Builder','42']]
            browser=BrowserTools(folder,threading.Event())
            opened=json.loads(browser.execute('open',f'http://127.0.0.1:{server.server_port}'))
            home_tab=next(tab['id'] for tab in opened['tabs'] if tab['current'])
            browser.execute('fill','Player','Builder');browser.execute('click','Start')
            result['browser_interaction']='Hello Builder' in browser.execute('inspect')
            artifact=json.loads(browser.execute('screenshot'))
            result['browser_screenshot']=Path(artifact['artifact']).stat().st_size>1000
            from agent_core import image_for_model
            result['vision_attachment']=len(image_for_model(artifact['artifact']))>1000
            report=json.loads(browser.execute('click','Open report'))
            result['browser_popup']=report['url'].endswith('/report') and 'Packaged report ready' in report['page']
            result['browser_tabs']=len(report['tabs'])==2 and 'Hello Builder' in browser.execute('switch',home_tab)
            browser.close();browser=None
        result['passed']=all(result.get(key,False) for key in ('free_provider_policy','offline_program','offline_starter','operating_policy','bundled_example','browser_interaction','browser_popup','browser_screenshot','vision_attachment','read_only_chain','remote_intent','named_project_target','batch_read','output_registration','project_playbooks','research_comparison','source_change_evidence','audited_preferences','routing_audit','candidate_controls_imported')) and (os.name!='nt' or (result.get('managed_process',False) and result.get('escape_hook_registration',False)))
        result['passed']=result['passed'] and all(result.get(key,False) for key in
                                                  ('browser_tabs','model_library','store_model_download_upgrade','mcp_plugins','project_data'))
        result['passed'] = result['passed'] and all(result[key] for key in
            ('conversation_round_trip', 'installed_model_reply_check', 'openzero_template_prefix_clean'))
        if desktop_worker_check:result['passed']=result['passed'] and result.get('desktop_worker',False)
        if getattr(__import__('sys'),'frozen',False):result['passed']=result['passed'] and result.get('bundled_game_assets',False)
        if os.name=='nt' and getattr(__import__('sys'),'frozen',False):result['passed']=result['passed'] and result.get('per_monitor_v2',False)
    except Exception as exc:result.update(passed=False,error=f'{type(exc).__name__}: {exc}')
    finally:
        if browser:browser.close()
        if server:server.shutdown();server.server_close()
    Path(destination).write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result['passed']
