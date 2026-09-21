import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from agent_core import ProjectTools, restore_checkpoint, run_agent, stream_chat, context_window, image_for_model, set_active_remote, set_agent_preferences, load_project_instructions
from ssh_tools import SSHProfile, SSHSession
from routing import choose_route

class CoreTests(unittest.TestCase):
    def test_workspace_agents_instructions_are_loaded_and_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'AGENTS.md';p.write_text('# Game rules\nRun the Godot import check after edits.\n',encoding='utf-8')
            self.assertIn('Godot import check',load_project_instructions(folder))
            p.write_text('x'*25000,encoding='utf-8')
            self.assertEqual(load_project_instructions(folder),'')

    def test_router_filters_stale_benchmarks_and_falls_back(self):
        config={'local_model':'new','server_model':'coder'}
        with patch('routing.inventory',side_effect=lambda port:['new:latest'] if port==11434 else []):
            self.assertEqual(choose_route(config)['route'],'local')
        with patch('routing.inventory',side_effect=lambda port:['new:latest'] if port==11434 else ['coder:latest']):
            result=choose_route(config,benchmarks={'results':[{'route':'local','model':'old','elapsed_seconds':1,'success':True}]})
            self.assertEqual(result['route'],'server')

    def test_targeted_edit_and_check(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'game.py';p.write_text('score = 1\n',encoding='utf-8')
            t=ProjectTools(folder,True)
            t.execute('edit_file',{'path':'game.py','old_text':'score = 1','new_text':'score = 2'})
            self.assertEqual(p.read_text(),'score = 2\n')
            with self.assertRaises(ValueError):t.execute('edit_file',{'path':'game.py','old_text':'missing','new_text':'new'})
            self.assertEqual(t.info()['engine'],'Python')

    def test_windows_newlines_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'score.py';original=b'def score():\r\n    return 1\r\n';p.write_bytes(original)
            tools=ProjectTools(folder,True)
            old=tools.execute('read_file',{'path':'score.py'})
            tools.execute('edit_file',{'path':'score.py','old_text':old,'new_text':old.replace('return 1','return 2')})
            self.assertEqual(p.read_bytes(),original.replace(b'return 1',b'return 2'))
            restore_checkpoint(tools.changes[-1]['checkpoint']);self.assertEqual(p.read_bytes(),original)

    def test_plan_blocks_game_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            t=ProjectTools(folder)
            for name in ('capture_screenshot','run_checks','launch_game','edit_file','run_blender_script'):
                with self.assertRaises(PermissionError):t.execute(name,{})

    def test_cancel_during_model_load(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_POST(self):time.sleep(2)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        cancel=threading.Event();timer=threading.Timer(.2,cancel.set);timer.start();started=time.monotonic()
        try:
            with self.assertRaises(InterruptedError):list(stream_chat(f'http://127.0.0.1:{server.server_port}',{},cancel))
            self.assertLess(time.monotonic()-started,1.5)
        finally:timer.cancel();server.shutdown();server.server_close()

    def test_context_keeps_tool_chain(self):
        messages=[{'role':'system','content':'system'},{'role':'user','content':'old '*1000},{'role':'assistant','content':'old reply'},
          {'role':'user','content':'new'},{'role':'assistant','content':'','tool_calls':[{'function':{'name':'list_files','arguments':{}}}]},
          {'role':'tool','tool_name':'list_files','content':'files'}]
        compact=context_window(messages,budget=500)
        self.assertEqual(compact[1]['content'],'new')
        self.assertEqual(compact[-2]['role'],'assistant')
        self.assertEqual(compact[-1]['role'],'tool')

    def test_boundaries_and_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            t=ProjectTools(folder)
            with self.assertRaises(ValueError):t.path('../outside')
            with self.assertRaises(PermissionError):t.execute('write_file',{'path':'x','content':'test'})
            with self.assertRaises(PermissionError):t.execute('run_command',{'command':'echo x'})

    def test_checkpoint_preserves_bytes_and_newer_edits(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'code.py';p.write_bytes(b'old\r\n')
            t=ProjectTools(folder,True);t.execute('write_file',{'path':'code.py','content':'new\n'})
            checkpoint=t.changes[-1]['checkpoint'];p.write_text('user edit',encoding='utf-8')
            with self.assertRaises(ValueError):restore_checkpoint(checkpoint)
            p.write_bytes(b'new\n');restore_checkpoint(checkpoint)
            self.assertEqual(p.read_bytes(),b'old\r\n')

    def test_checked_whole_file_write_rejects_stale_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'code.py';p.write_text('before',encoding='utf-8')
            tools=ProjectTools(folder,True)
            fingerprint=json.loads(tools.execute('file_fingerprint',{'path':'code.py'}))
            tools.execute('write_file_checked',{'path':'code.py','content':'after','expected_sha256':fingerprint['sha256']})
            self.assertEqual(p.read_text(),'after')
            with self.assertRaises(ValueError):tools.execute('write_file_checked',{'path':'code.py','content':'lost','expected_sha256':fingerprint['sha256']})
            self.assertEqual(p.read_text(),'after')

    def test_pilot_tool_is_available_for_act_desktop_tasks(self):
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                raw=(json.dumps({'message':{'role':'assistant','content':'Ready.'},'done':True})+'\n').encode()
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            import agent_core
            agent_core.set_agent_preferences(desktop_access=True,pc_pilot=True)
            with tempfile.TemporaryDirectory() as folder,patch.object(agent_core,'model_supports_vision',return_value=False):
                run_agent(f'http://127.0.0.1:{server.server_port}','fixture',[{'role':'user','content':'Build the game'}],folder,True,threading.Event(),lambda *_:None)
            self.assertIn('computer',{tool['function']['name'] for tool in received[-1]['tools']})
        finally:
            agent_core.set_agent_preferences()
            server.shutdown();server.server_close()

    def test_remote_pilot_status_and_discovery_are_read_only_tools(self):
        profile=SSHProfile('AMD OpenZero server','amd-box','~/code')
        def fake_run(command, cwd='', cancel=None):
            self.assertIn('printf',command)
            return 'Exit 0\nTALKTOAI_REMOTE_OK\nHOST=amd\nPWD=/home/zero/code\n'
        try:
            set_active_remote(profile);set_agent_preferences(remote_allowed=True,remote_pilot=True)
            with tempfile.TemporaryDirectory() as folder,patch.object(SSHSession,'resolve',return_value={'hostname':'amd.example','user':'zero','port':'22'}),patch.object(SSHSession,'run',side_effect=fake_run):
                tools=ProjectTools(folder,False)
                status=json.loads(tools.execute('remote_status',{}))
                self.assertEqual(status['alias'],'amd-box')
                self.assertIn('TALKTOAI_REMOTE_OK',status['verification'])
                discovery=tools.execute('remote_project_info',{'cwd':'~/code'})
                self.assertIn('PWD=/home/zero/code',discovery)
        finally:
            set_active_remote(None);set_agent_preferences()

    def test_remote_pilot_roster_keeps_write_commands_out_of_plan(self):
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                raw=(json.dumps({'message':{'role':'assistant','content':'Ready.'},'done':True})+'\n').encode()
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            set_active_remote(SSHProfile('AMD','amd-box',''))
            set_agent_preferences(remote_allowed=True,remote_pilot=True)
            with tempfile.TemporaryDirectory() as folder,patch('agent_core.model_supports_vision',return_value=False):
                run_agent(f'http://127.0.0.1:{server.server_port}','fixture',[{'role':'user','content':'Use my AMD server'}],folder,False,threading.Event(),lambda *_:None)
            names={tool['function']['name'] for tool in received[-1]['tools']}
            self.assertIn('remote_status',names);self.assertIn('remote_project_info',names);self.assertNotIn('remote_run_command',names)
        finally:
            set_active_remote(None);set_agent_preferences()
            server.shutdown();server.server_close()

    def test_local_vision_attaches_screenshot_only_for_next_turn(self):
        import agent_core
        from PIL import Image
        calls=[]
        def stream(_url,payload,_cancel):
            calls.append(payload)
            if len(calls)==1:
                return iter([{'message':{'tool_calls':[{'function':{'name':'capture_screenshot','arguments':{}}}]},'done':True}])
            return iter([{'message':{'content':'I inspected the screenshot.','role':'assistant'},'done':True}])
        with tempfile.TemporaryDirectory() as folder,patch.object(agent_core,'stream_chat',side_effect=stream),patch.object(agent_core,'model_supports_vision',return_value=True),patch('PIL.ImageGrab.grab',return_value=Image.new('RGB',(32,32),(20,40,60))):
            run_agent('http://fixture','vision-fixture',[{'role':'user','content':'Take a screenshot and inspect it'}],folder,True,threading.Event(),lambda *_:None)
        attached=[m for m in calls[1]['messages'] if m.get('images')]
        self.assertEqual(len(attached),1)
        self.assertTrue(attached[0]['images'][0])
        self.assertNotIn('images',context_window(calls[1]['messages'])[0])

    def test_image_attachment_is_bounded(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'large.png';Image.new('RGB',(2400,1600),(1,2,3)).save(p)
            self.assertLess(len(image_for_model(p)),2_000_000)

    def test_actual_tool_round_trip(self):
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])));received.append(payload)
                if len(received)==1:
                    message={'role':'assistant','content':'Creating the file.', 'tool_calls':[{'function':{'name':'write_file','arguments':{'path':'hello.txt','content':'hello'}}}]}
                else:message={'role':'assistant','content':'Created hello.txt.'}
                raw=(json.dumps({'message':message,'done':True})+'\n').encode()
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder,patch('agent_core.model_supports_vision',return_value=False):
                events=[];run_agent(f'http://127.0.0.1:{server.server_port}','fixture', [{'role':'user','content':'create hello'}],folder,True,threading.Event(),lambda k,v:events.append((k,v)))
                self.assertEqual((Path(folder)/'hello.txt').read_text(),'hello')
                self.assertTrue(any(k=='change' for k,v in events))
                self.assertEqual(received[1]['messages'][-1]['role'],'tool')
        finally:server.shutdown();server.server_close()

if __name__=='__main__':unittest.main()
