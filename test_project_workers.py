import hashlib
import json
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import agent_core as core
import project_workers as workers
import routing


@contextmanager
def fixture(reply, metadata=None):
    payloads = []
    default = {'details': {'quantization_level':'Q4_K_M'}, 'model_info': {'general.architecture':'qwen3'},
               'capabilities': ['completion', 'tools']}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            payloads.append((self.path, body))
            if self.path == '/api/show':
                data = default if metadata is None else metadata
            elif self.path == '/api/chat':
                data = {'message': reply(body), 'done': True}
            else:
                self.send_error(404)
                return
            data = (json.dumps(data) + '\n').encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval':.025}, daemon=True)
    thread.start()
    try:
        with patch.object(routing, 'LOCAL_PORT', server.server_port), patch.object(core, 'AUTO_CONTEXT', False), \
                patch.object(core, 'ACTIVE_PROVIDER', None), patch.object(core, 'DESKTOP_ACCESS', False), \
                patch.object(core, 'model_supports_vision', return_value=False), \
                patch.object(core, '_provider_stream', side_effect=AssertionError('Unexpected API inference')):
            yield f'http://127.0.0.1:{server.server_port}', payloads
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def call(name, args):
    return {'tool_calls':[{'function':{'name':name,'arguments':args}}]}


class ProjectWorkerTests(unittest.TestCase):
    def test_intent_ignores_ordinary_chat_opt_out_and_descriptive_requests(self):
        from studio import chat_requests_tools
        for text in ('hello', 'What are subagents?', 'Show me how to use 3 agents.',
                     'Write a bedtime story with two agents.', 'Use Agent Zero to write a poem.'):
            with self.subTest(text=text):
                self.assertFalse(chat_requests_tools(text))
                self.assertEqual(workers.plan_reviews(text), [])
        self.assertTrue(chat_requests_tools('Please use 3 subagents to review my project.'))
        self.assertFalse(chat_requests_tools('Use 2 agents to write a poem.'))
        self.assertEqual(workers.requested_count('Use two or three local agents to inspect my project.'),3)
        self.assertFalse(workers.plan_reviews('Review the source and tests without workers.'))
        self.assertFalse(workers.plan_reviews('Review src/app.py.'))
        self.assertEqual([item.role for item in workers.plan_reviews('Review the implementation, tests and documentation.')],
                         ['investigator','test_planner','documenter'])
        self.assertEqual(len(workers.plan_reviews('Use 8 agents on this project.', 2)), 2)
        self.assertEqual(workers.worker_limit(0), 0)
        self.assertEqual(workers.worker_limit(-2), 0)
        self.assertEqual(workers.worker_limit('bad'), 3)

    def test_three_real_local_workers_read_independent_files_and_parent_gets_evidence(self):
        names = {'investigator':'Main.py','test_planner':'test_main.py','documenter':'README.md'}
        seen = []
        parent_turns = []
        report_events = []
        def respond(payload):
            history = payload['messages']
            if history[0]['content'].startswith('You are a read-only project reviewer'):
                self.assertEqual(payload['model'], 'local-qwen')
                self.assertLessEqual({t['function']['name'] for t in payload['tools']}, workers.READ_TOOLS)
                self.assertNotIn('old private conversation', str(history))
                role = history[1]['content'].split('Role: ', 1)[1].split('.', 1)[0]
                if history[-1]['role'] == 'user':
                    seen.append(role)
                    return call('read_file', {'path':names[role]})
                self.assertIn('evidence-' + role, history[-1]['content'])
                return {'content': role + ' observed evidence-' + role + ' in ' + names[role]}
            parent_turns.append(history)
            self.assertNotIn('delegate_review', {t['function']['name'] for t in payload['tools']})
            return {'content':'Reviewed the project using the three local reports.'}
        with tempfile.TemporaryDirectory() as folder, fixture(respond) as (url, payloads):
            for role, path in names.items():
                Path(folder,path).write_text('evidence-' + role, encoding='utf8')
            history = [{'role':'user','content':'old private conversation'}, {'role':'assistant','content':'Understood.'},
                       {'role':'user','content':'Use three agents to review the implementation, tests and documentation.'}]
            core.run_agent_zero(url,'local-qwen',history,folder,False,threading.Event(),
                                lambda kind,value:report_events.append((kind,value)))
        self.assertEqual(seen, list(names))
        self.assertEqual(len(parent_turns), 1)
        reports = [json.loads(m['content']) for m in parent_turns[0] if m.get('tool_name')=='delegate_review']
        self.assertEqual([r['role'] for r in reports], list(names))
        for report in reports:
            self.assertEqual(report['schema'], 'talktoai.project-review.v1')
            self.assertEqual(report['status'], 'completed')
            self.assertIn('read_file',report['tools_used'])
            self.assertIn('evidence-'+report['role'], report['report'])
            path = names[report['role']]
            file_hash = hashlib.sha256(('evidence-'+report['role']).encode()).hexdigest()
            self.assertTrue(any(f['path']==path and f['sha256']==file_hash for item in report['evidence'] for f in item.get('files', [])))
        self.assertEqual(sum(kind=='tool' and item.get('name')=='delegate_review' for kind,item in report_events), 3)
        self.assertEqual(sum(path=='/api/chat' for path,_ in payloads), 7)

    def test_compound_builds_select_only_explicitly_requested_independent_investigations(self):
        cases = [
            ('Fix the crash in src/load.py and investigate gaps in its regression tests.',
             ['investigator','test_planner']),
            ('Could you implement autosave for my app, then examine the setup guide for outdated instructions?',
             ['investigator','documenter']),
            ('Build a settings dialog for the app; audit the existing unit tests and README examples.',
             ['investigator','test_planner','documenter']),
            ('Fix the parser. Also investigate which tests failed on the last run.',
             ['investigator','test_planner']),
            ('Review the implementation, tests and docs.', ['investigator','test_planner','documenter']),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertTrue(workers.requests_project_workers(text))
                self.assertEqual([a.role for a in workers.plan_reviews(text)], expected)
                self.assertEqual([a.role for a in workers.plan_reviews(text,1)], expected[:1])
                self.assertEqual(workers.plan_reviews(text,0), [])

    def test_specialist_team_respects_scope_budget_and_independent_work(self):
        cases = [
            ('game', 'Review gameplay, tests and asset licenses in this Godot project.',
             ['gameplay_reviewer', 'playtest_reviewer', 'asset_reviewer']),
            ('research', 'Review sources, methods and claims in my research project.',
             ['evidence_reviewer', 'methods_reviewer', 'fact_checker']),
            ('writing', 'Revise the project draft and check its facts and terminology.',
             ['draft_reviewer', 'fact_checker', 'consistency_reviewer']),
        ]
        from chat_intent import chat_requests_tools
        for focus, task, roles in cases:
            with self.subTest(focus=focus):
                self.assertTrue(chat_requests_tools(task, focus=focus))
                self.assertEqual([item.role for item in workers.plan_reviews(task, focus=focus)], roles)
                self.assertEqual([item.role for item in workers.plan_reviews(task, 2, focus=focus)], roles[:2])
        for task in ('Tell me about gameplay and sprites.',
                     'Fix movement in this Godot project and run tests.',
                     'Review gameplay and tests in this project without agents.',
                     'Compare gameplay and tests from the internet.',
                     'Review gameplay and skip asset licenses in this project.'):
            with self.subTest(task=task):
                self.assertEqual(workers.plan_reviews(task, focus='game'), [])

    def test_games_specialists_read_local_evidence_and_agent_zero_synthesizes(self):
        names = {'gameplay_reviewer':'player.gd', 'playtest_reviewer':'tests/test_movement.gd',
                 'asset_reviewer':'assets/LICENSE.txt'}
        events = []
        reviewed = []
        def respond(payload):
            history = payload['messages']
            if history[0]['content'].startswith('You are a read-only project reviewer'):
                role = history[1]['content'].split('Role: ', 1)[1].split('.', 1)[0]
                self.assertLessEqual({item['function']['name'] for item in payload['tools']}, workers.READ_TOOLS)
                if history[-1]['role'] == 'user':
                    return call('read_file', {'path':names[role]})
                return {'content':'Observed local evidence in ' + names[role]}
            self.assertIn('You are Agent Zero', history[0]['content'])
            self.assertIn('Work as a game developer', history[0]['content'])
            reviewed.extend(json.loads(item['content']) for item in history if item.get('tool_name') == 'delegate_review')
            return {'content':'Applied the gameplay, playtest and license evidence in the review.'}
        with tempfile.TemporaryDirectory() as folder, fixture(respond) as (url, payloads):
            for role, relative in names.items():
                path = Path(folder, relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('Verified fixture for ' + role, encoding='utf-8')
            task = 'Review gameplay, tests and asset licenses in this Godot project.'
            core.run_agent_zero(url, 'local-qwen', [{'role':'user','content':task}], folder, False,
                                threading.Event(), lambda kind,value: events.append((kind,value)),
                                task_kind='chat', focus='game')
        self.assertEqual([item['role'] for item in reviewed], list(names))
        self.assertTrue(all(item['status'] == 'completed' and 'read_file' in item['tools_used'] for item in reviewed))
        self.assertEqual(sum(kind=='tool' and value.get('automatic') and value.get('name')=='delegate_review'
                             for kind,value in events), 3)

    def test_chat_worker_predicate_leaves_ordinary_single_and_external_tasks_to_the_normal_router(self):
        for text in (
            'Fix the loader in src/load.py.', 'Fix the implementation and run the tests.',
            'Build an app, add tests and update the README.', 'Fix the parser then check whether the tests pass.',
            'Fix this app, but do not review its test coverage.', 'Review the source and then run the tests.',
            'Update my résumé and check the examples.', 'Build a birdhouse and review the setup guide.',
            'Explain how to fix the module and investigate its tests.', 'Can you teach me how to review source and tests?',
            'What are the differences between source and tests?', 'We often review the code and tests together.',
            'Review the source and tests pasted below.', 'Compare the source and tests on GitHub.',
            'Fix the app and inspect the online documentation.', 'Fix the module and investigate its tests without workers.',
        ):
            with self.subTest(text=text):
                self.assertFalse(workers.requests_project_workers(text))
                self.assertEqual(workers.plan_reviews(text),[])

    def test_chat_originated_fix_with_test_investigation_returns_both_reports_to_parent(self):
        events=[]
        def respond(payload):
            messages=payload['messages']
            if messages[0]['content'].startswith('You are a read-only project reviewer'):
                self.assertLessEqual({t['function']['name'] for t in payload.get('tools',[])},workers.READ_TOOLS)
                return {'content':'I reviewed the project excerpt.'}
            self.assertTrue({'investigator','test_planner'} <= {
                json.loads(message['content'])['role'] for message in messages if message.get('tool_name')=='delegate_review'})
            return {'content':'The two independent local reviews are available.'}
        with tempfile.TemporaryDirectory() as folder, fixture(respond) as (url, requests):
            Path(folder,'loader.py').write_text('def load(): return 1')
            Path(folder,'test_loader.py').write_text('def test_load(): assert True')
            prompt='Fix the loader in this project and investigate the missing unit test coverage.'
            self.assertTrue(workers.requests_project_workers(prompt))
            core.run_agent_zero(url,'fixture',[{'role':'user','content':prompt}],folder,False,threading.Event(),
                                lambda kind,value:events.append((kind,value)),task_kind='chat')
        self.assertEqual(sum(kind=='tool' and v.get('automatic') and v['name']=='delegate_review' for kind,v in events),2)
        self.assertEqual(sum(path=='/api/chat' for path,_ in requests),3)

    def test_automatic_compound_review_shares_one_budget_with_model_requested_worker(self):
        parent_count = 0
        def respond(payload):
            nonlocal parent_count
            history = payload['messages']
            if history[0]['content'].startswith('You are a read-only project reviewer'):
                return {'content':'Read the provided project excerpt.'}
            parent_count += 1
            offered = {t['function']['name'] for t in payload['tools']}
            if parent_count == 1:
                self.assertIn('delegate_review', offered)
                self.assertEqual(sum(m.get('tool_name')=='delegate_review' for m in history),2)
                return call('delegate_review', {'role':'documenter','task':'Check the README example'})
            self.assertNotIn('delegate_review', offered)
            return {'content':'Combined review complete.'}
        events = []
        with tempfile.TemporaryDirectory() as folder, fixture(respond) as (url, requests):
            for name in ('app.py','test_app.py','README.md'):
                Path(folder,name).write_text('fixture',encoding='utf8')
            core.run_agent_zero(url,'local-qwen',[{'role':'user','content':'Review the implementation and tests.'}],folder,False,
                                threading.Event(),lambda k,v:events.append((k,v)))
        self.assertEqual(parent_count,2)
        self.assertEqual(sum(k=='tool' and v['name']=='delegate_review' for k,v in events),3)
        self.assertEqual(sum(path=='/api/chat' for path,_ in requests),5)

    def test_private_files_shell_and_recursive_tools_are_blocked_at_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('.env','credentials.json','token.txt','code.py'):
                (root/name).write_text(name,encoding='utf8')
            tools=core._WorkerTools(folder,threading.Event())
            self.assertEqual(tools.files(), ['code.py'])
            for name in ('.env','credentials.json','token.txt','../outside'):
                with self.assertRaises((PermissionError,ValueError)):
                    tools.execute('read_file', {'path':name})
            for name in ('write_file','run_command','computer','browser','remote_run_command','mcp_plugin','delegate_review','enable_tools','run_checks'):
                with self.assertRaises(PermissionError):tools.execute(name,{})
            self.assertFalse(tools.observations)

    def test_metadata_and_route_must_identify_an_installed_local_model(self):
        for url in ('https://api.example.invalid/v1','http://127.0.0.1:11435','http://other.invalid:11434','http://user@127.0.0.1:11434'):
            with self.assertRaisesRegex(ValueError,'installed on this PC'):workers.verify_local_model(url,'fixture')
        for meta in ({'remote_model':'upstream','details':{'quantization_level':'Q4_K_M'}},
                     {'model_info':{'general.architecture':'qwen'},'remote_host':'https://ollama.com'},
                     {'details':[]}, {}):
            with self.subTest(meta=meta), fixture(lambda _: {},meta) as (url,req):
                with self.assertRaisesRegex(ValueError,'cloud-backed|verified'):
                    workers.verify_local_model(url,'ordinary-alias')
                self.assertEqual([path for path,_ in req], ['/api/show'])
        with fixture(lambda _: {}) as (url,requests):
            with self.assertRaisesRegex(ValueError,'installed on this PC'):workers.verify_local_model(url,'qwen:cloud')
            self.assertEqual(requests, [])
            workers.verify_local_model(url, 'qwen-local')
            self.assertEqual(requests[0][1]['model'], 'qwen-local')

    def test_simple_chat_and_disabled_local_workers_make_one_request(self):
        with tempfile.TemporaryDirectory() as folder, fixture(lambda _: {'content':'Hello!'}) as (url, requests):
            core.run_conversation(url, 'local-qwen', [{'role':'user','content':'Hello'}], threading.Event(), lambda *_:None)
            self.assertEqual([path for path,_ in requests], ['/api/chat'])
            requests.clear()
            core.run_agent_zero(url,'local-qwen',[{'role':'user','content':'Use 3 agents to review code and tests'}],folder,
                                False,threading.Event(),lambda *_:None,performance={'local_worker_limit':0})
            self.assertEqual([path for path,_ in requests], ['/api/chat'])
            self.assertNotIn('delegate_review',{t['function']['name'] for t in requests[0][1]['tools']})

    def test_named_file_shortcut_requires_unique_complete_unredacted_sources(self):
        root = ['src/app.py','tests/test_app.py']
        task = 'Explain src/app.py and tests/test_app.py.'
        read = {'path':'src/app.py','complete':True}
        self.assertTrue(workers.complete_named_files(root,[read],task))
        for observed, prompt, inventory in [
            ([dict(read,complete=False)], task, root),
            ([dict(read,redacted=True)], task, root),
            ([read], 'Check this project.', root),
            ([read], 'Check app.py.', [*root,'old/app.py']),
            ([], task, root),
        ]:
            self.assertFalse(workers.complete_named_files(inventory,observed,prompt))
        self.assertEqual(workers.starting_files(root,task,'test_planner'), ['tests/test_app.py'])
        self.assertEqual(workers.starting_files(root,task,'investigator'), ['src/app.py'])

    def test_completed_named_review_synthesis_uses_plain_text_with_actual_sources(self):
        calls=[]
        def respond(payload):
            calls.append(payload)
            if len(calls) <= 2:
                return {'content':'I reviewed the supplied file.'}
            self.assertNotIn('tools',payload)
            self.assertEqual([m['role'] for m in payload['messages']], ['system','user'])
            question=payload['messages'][-1]['content']
            self.assertIn('return 17',question)
            self.assertIn('price() == 19',question)
            self.assertIn('I reviewed the supplied file.',question)
            return {'content':'price.py returns 17, but test_price.py expects 19.'}
        events=[]
        with tempfile.TemporaryDirectory() as folder, fixture(respond) as (url,_), patch.object(core,'AUTO_CONTEXT',True):
            Path(folder,'price.py').write_text('def price():\n    return 17')
            Path(folder,'test_price.py').write_text('def test_price():\n    assert price() == 19')
            prompt='Use 2 agents to review the implementation in price.py and the tests in test_price.py. Explain the mismatch.'
            core.run_agent_zero(url,'fixture',[{'role':'user','content':prompt}],folder,False,threading.Event(),
                                lambda k,v:events.append((k,v)))
        self.assertEqual(len(calls),3)
        self.assertTrue(all(not c.get('tools') for c in calls))
        self.assertIn(('message',{'role':'assistant','content':'price.py returns 17, but test_price.py expects 19.'}),events)

    def test_large_model_receives_cold_allowance_only_for_confirmed_unloaded_state(self):
        url='http://127.0.0.1:11434'
        with patch.object(routing,'_json_request',return_value={'models':[]}) as ps:
            self.assertEqual(workers.cold_load_allowance(url,'large',{'parameter_size':'35.0B'}),120)
            self.assertEqual(workers.cold_load_allowance(url,'small',{'parameter_size':'1.7B'}),0)
            ps.assert_called_once()
        for inventory in ({'models':[{'name':'large:latest'}]},{'models':[{}]},{}):
            with patch.object(routing,'_json_request',return_value=inventory):
                self.assertEqual(workers.cold_load_allowance(url,'large',{'parameter_size':'35B'}),0)

    def test_continue_preserves_reviews_without_repeating_the_workers(self):
        with tempfile.TemporaryDirectory() as folder, fixture(lambda _: {'content':'Reviewed the supplied evidence.'}) as (url, requests):
            Path(folder,'a.py').write_text('return 4',encoding='utf8')
            messages=[{'role':'user','content':'Use a reviewer to explain a.py'}]
            events=[]
            core.run_agent_zero(url,'fixture',messages,folder,False,threading.Event(),lambda k,v:events.append((k,v)))
            messages.extend(data for kind,data in events if kind=='message')
            self.assertTrue(any(m.get('tool_name')=='delegate_review' for m in messages))
            requests.clear()
            core.run_agent_zero(url,'fixture',messages+[{'role':'user','content':'Continue'}],folder,False,
                                threading.Event(),lambda *_:None)
            self.assertEqual([path for path,_ in requests], ['/api/chat'])

    def test_worker_deadline_and_parent_stop_are_distinguished(self):
        with tempfile.TemporaryDirectory() as folder, fixture(lambda _: {}) as (url, _):
            for stop_parent in (False, True):
                parent=threading.Event()
                def slow(_url,_payload,cancel,*,local_only):
                    self.assertTrue(local_only)
                    if stop_parent:parent.set()
                    while not cancel.wait(.02):
                        pass
                    raise InterruptedError('Stopped')
                    yield
                with patch.object(workers,'WORKER_SECONDS', .1),patch.object(core,'stream_chat',side_effect=slow):
                    report=json.loads(core.run_subagent(url,'fixture',folder,'Inspect project','reviewer',parent,lambda *_:None))
                self.assertEqual(report['status'],'stopped' if stop_parent else 'timed_out')
                self.assertEqual(parent.is_set(),stop_parent)

    def test_cloud_alias_delegation_never_uses_worker_inference(self):
        replies=[]
        def parent(payload):
            replies.append(payload)
            return {'content':'I handled the request in the parent.'}
        with tempfile.TemporaryDirectory() as folder, fixture(parent, {'remote_host':'https://ollama.com'}) as (url, requests):
            core.run_agent_zero(url,'alias',[{'role':'user','content':'Use 3 agents to inspect the source and tests'}],
                                folder,False,threading.Event(),lambda *_:None)
        self.assertEqual(len(replies),1)
        result=next(m for m in replies[0]['messages'] if m.get('tool_name')=='delegate_review')
        self.assertEqual(json.loads(result['content'])['status'],'unavailable')


if __name__=='__main__':
    unittest.main()
