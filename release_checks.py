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
            if self.path=='/report':
                self.wfile.write(b'<title>Report</title><h1>Packaged report ready</h1>')
            else:
                self.wfile.write(b'<label>Player<input aria-label="Player"></label><button aria-label="Start" onclick="document.querySelector(\'h1\').textContent=\'Hello \'+document.querySelector(\'input\').value">+</button><h1>Ready</h1><a href="/report" target="_blank">Open report</a>')
    server=None;browser=None
    try:
        if os.name=='nt':
            import pywinauto
            result['computer_dependency']=pywinauto.__version__
        else:
            result['computer_dependency']='Windows-only PC Pilot; portable browser checks remain available'
        from ethics_policy import verify_release_policy
        result['operating_policy']=verify_release_policy()['status']=='verified'
        if os.name=='nt':
            from control_cancel import EscapeCancel
            escape=EscapeCancel()
            try:result['escape_hook_registration']=escape.arm(lambda:None)
            finally:escape.disarm()
        from browser_tools import BrowserTools
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        with tempfile.TemporaryDirectory(prefix='talktoai-release-') as folder:
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
            report=json.loads(browser.execute('click','Open report'))
            result['browser_popup']=report['url'].endswith('/report') and 'Packaged report ready' in report['page']
            browser.close();browser=None
        result['passed']=all(result.get(key,False) for key in ('offline_program','offline_starter','operating_policy','bundled_example','browser_interaction','browser_popup','browser_screenshot','vision_attachment','read_only_chain','remote_intent','named_project_target','batch_read','output_registration','project_playbooks','research_comparison','source_change_evidence','audited_preferences','routing_audit','candidate_controls_imported')) and (os.name!='nt' or (result.get('managed_process',False) and result.get('escape_hook_registration',False)))
    except Exception as exc:result.update(passed=False,error=f'{type(exc).__name__}: {exc}')
    finally:
        if browser:browser.close()
        if server:server.shutdown();server.server_close()
    Path(destination).write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result['passed']
