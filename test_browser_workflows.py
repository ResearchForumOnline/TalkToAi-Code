"""Opt-in real browser checks against a disposable loopback HTTP fixture only."""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen
from browser_tools import BrowserTools, browser_port, find_browser_program


class Fixture(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path.startswith('/report'):
            if self.path == '/report?delayed':
                time.sleep(.5)
            html='<title>Report</title><h1>Report ready</h1><button onclick="window.close()">Close report</button>'
        elif self.path.startswith('/panel'):
            html='''<label>Nested player <input></label><p id="inside">Nested panel ready</p>
<button aria-label="Nested save" onclick="document.querySelector('#inside').textContent='Nested action confirmed'">Save</button>'''
        else:
            html='''<title>Fixture</title><h1>Original fixture</h1>
<button aria-label="Save project" onclick="document.querySelector('#status').textContent='Saved successfully'">+</button>
<p id="status">Waiting</p>
<label>Player name <input oninput="document.querySelector('#status').textContent=this.value"></label>
<label>Private field <input type="password"></label>
<button aria-label="Duplicate" onclick="document.querySelector('#status').textContent='Selected first duplicate'">A</button>
<button aria-label="Duplicate" onclick="document.querySelector('#status').textContent='Selected second duplicate'">B</button>
<a target="_blank" href="/report">Open report</a>
<a target="_blank" href="/report?delayed">Open delayed report</a>
<button onclick="window.open('/report?1');window.open('/report?2')">Open two reports</button>'''
            if self.path.startswith('/nested'):
                html+=f'<iframe title="Nested panel" src="http://localhost:{self.server.server_port}/panel"></iframe>'
        body=html.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)


@unittest.skipUnless(os.environ.get('TALKTOAI_BROWSER_ACCEPTANCE')=='1','Opt-in disposable localhost browser acceptance')
class BrowserWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}/'
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(2)
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.tools=BrowserTools(self.folder.name,threading.Event());self.addCleanup(self.tools.close)
        self.tools.execute('open',self.url)
    def test_accessible_name_click(self):
        result=json.loads(self.tools.execute('click','Save project'))
        self.assertIn('Saved successfully',result['page'])
    def test_ambiguous_name_never_clicks(self):
        with self.assertRaisesRegex(ValueError,'2 visible controls'):
            self.tools.execute('click','Duplicate')
        self.assertIn('Waiting',self.tools.page.locator('#status').inner_text())
    def test_unique_reference_targets_one_duplicate(self):
        snapshot=json.loads(self.tools.execute('inspect'))['page']
        refs=re.findall(r'button "Duplicate" \[ref=([^\]]+)\]',snapshot)
        self.assertEqual(len(refs),2)
        result=json.loads(self.tools.execute('click',refs[1]))
        self.assertIn('Selected second duplicate',result['page'])
        self.assertNotIn('Selected first duplicate',result['page'])
    def test_reference_cannot_act_on_replacement_or_next_observation(self):
        snapshot=json.loads(self.tools.execute('inspect'))['page']
        old_ref=re.search(r'button "Save project" \[ref=([^\]]+)\]',snapshot).group(1)
        self.tools.page.get_by_role('button',name='Save project').evaluate(
            'element => element.replaceWith(element.cloneNode(true))')
        with self.assertRaisesRegex(ValueError,'changed or was replaced'):
            self.tools.execute('click',old_ref)
        self.assertEqual(self.tools.page.locator('#status').inner_text(),'Waiting')
        self.tools.execute('inspect')
        with self.assertRaisesRegex(ValueError,'latest browser inspection'):
            self.tools.execute('click',old_ref)
    def test_same_dom_node_with_changed_name_requires_reinspection(self):
        self.tools.page.get_by_role('button',name='Save project').evaluate(
            "element => element.setAttribute('aria-label', 'Save elsewhere')")
        with self.assertRaisesRegex(ValueError,'changed or was replaced'):
            self.tools.execute('click','Save project')
        self.assertEqual(self.tools.page.locator('#status').inner_text(),'Waiting')
    def test_cross_origin_frame_references_support_edit_and_click(self):
        snapshot=json.loads(self.tools.execute('open',self.url+'nested'))['page']
        field=re.search(r'textbox "Nested player" \[ref=([^\]]+)\]',snapshot).group(1)
        edited=json.loads(self.tools.execute('fill',f'[ref={field}]','Tester'))['page']
        found=self.tools.page.frame_locator('iframe').get_by_label('Nested player')
        self.assertEqual(found.input_value(),'Tester')
        button=re.search(r'button "Nested save" \[ref=([^\]]+)\]',edited).group(1)
        result=json.loads(self.tools.execute('click',button))
        self.assertIn('Nested action confirmed',result['page'])
    def test_editable_values_are_not_exposed_by_aria_or_visible_text(self):
        marker='synthetic-form-fixture'
        self.tools.page.set_content(f'''<p>Ordinary visible page text</p>
<label>Private <input type=password role=button value="{marker}"></label>
<textarea>{marker}</textarea><div contenteditable>{marker}</div>''')
        result=self.tools.execute('inspect')
        self.assertFalse(marker in result,'A synthetic form value reached the tool observation.')
        self.assertIn('Ordinary visible page text',result)
        with self.assertRaisesRegex(ValueError,'password field'):
            self.tools.execute('fill','Private','typed fixture')
    def test_popup_follow_and_closed_popup_recovery(self):
        result=json.loads(self.tools.execute('click','Open report'))
        self.assertTrue(result['url'].endswith('/report'));self.assertIn('Report ready',result['page'])
        self.assertEqual(len(self.tools.context.pages),2)
        result=json.loads(self.tools.execute('click','Close report'))
        self.assertIn('Original fixture',result['page']);self.assertEqual(result['url'],self.url)
        self.assertEqual(len(self.tools.context.pages),1)
    def test_new_tab_waits_for_its_initial_response(self):
        report = json.loads(self.tools.execute('click', 'Open delayed report'))
        self.assertEqual(report['url'], self.url + 'report?delayed')
        self.assertIn('Report ready', report['page'])
        self.assertEqual(len(report['tabs']), 2)

    def test_multiple_popups_not_guessed(self):
        original=self.tools.page
        with self.assertRaisesRegex(ValueError,'multiple browser pages'):
            self.tools.execute('click','Open two reports')
        self.assertIs(self.tools.page,original);self.assertEqual(len(self.tools.context.pages),3)
        with self.assertRaisesRegex(ValueError,'Inspect the current'):
            self.tools.execute('click','Save project')
    def test_new_url_requires_fresh_observation(self):
        self.tools.page.goto(self.url+'report')
        with self.assertRaisesRegex(ValueError,'Inspect the current'):
            self.tools.execute('click','Save project')
    def test_reload_at_same_url_invalidates_old_observation(self):
        self.tools.page.reload()
        with self.assertRaisesRegex(ValueError,'Inspect the current'):
            self.tools.execute('click','Save project')
        self.assertEqual(self.tools.page.locator('#status').inner_text(),'Waiting')
    def test_cancel_during_target_resolution_does_not_click(self):
        resolve=self.tools._target
        def stop_after_resolution(target):
            element=resolve(target)
            self.tools.cancel.set()
            return element
        with patch.object(self.tools,'_target',side_effect=stop_after_resolution):
            with self.assertRaises(InterruptedError):self.tools.execute('click','Save project')
        self.assertEqual(self.tools.page.locator('#status').inner_text(),'Waiting')

    def test_unrelated_tab_is_not_selected_as_action_popup(self):
        original=self.tools.page
        resolve=self.tools._target
        def open_unrelated_tab(target):
            element=resolve(target)
            background=self.tools.context.new_page()
            background.goto(self.url+'report')
            return element
        with patch.object(self.tools,'_target',side_effect=open_unrelated_tab):
            result=json.loads(self.tools.execute('click','Save project'))
        self.assertIs(self.tools.page,original)
        self.assertIn('Saved successfully',result['page'])

    def test_browser_errors_are_scoped_and_omit_script_payloads(self):
        original=self.tools.page
        background=self.tools.context.new_page()
        background.evaluate("() => setTimeout(() => { throw Error('Other page fixture marker'); }, 0)")
        background.wait_for_timeout(100)
        isolated=json.loads(self.tools.execute('inspect'))
        self.assertEqual(isolated['errors'],[])
        original.evaluate("() => setTimeout(() => { throw Error('Selected page fixture marker'); }, 0)")
        original.wait_for_timeout(100)
        result=json.loads(self.tools.execute('inspect'))
        self.assertEqual(result['errors'],['Page script reported an error.'])

    def test_tab_switch_and_labelled_fill(self):
        home=json.loads(self.tools.execute('open',self.url))
        home_id=next(tab['id'] for tab in home['tabs'] if tab['current'])
        opened=json.loads(self.tools.execute('new_tab',self.url+'report'))
        self.assertEqual(len(opened['tabs']),2)
        self.assertIn('Report ready',opened['page'])
        reopened=json.loads(self.tools.execute('switch',home_id))
        self.assertEqual(reopened['url'],self.url)
        filled=json.loads(self.tools.execute('fill','Player name','Builder'))
        self.assertIn('Builder',filled['page'])
        with self.assertRaisesRegex(ValueError,'password field'):
            self.tools.execute('fill','Private field','fake fixture text')

    def test_installed_chromium_browsers_launch(self):
        self.tools.close()
        tested=0
        for name in ('brave','chrome','edge'):
            if not find_browser_program(name):continue
            tested+=1
            with self.subTest(browser=name):
                tools=BrowserTools(self.folder.name,threading.Event(),name,strict_browser=True)
                try:
                    result=json.loads(tools.execute('open',self.url))
                    self.assertEqual(result['browser'],name)
                    self.assertIn('Original fixture',result['page'])
                    ref=re.search(r'button "Save project" \[ref=([^\]]+)\]',result['page']).group(1)
                    self.assertIn('Saved successfully',json.loads(tools.execute('click',ref))['page'])
                finally:
                    tools.close()
        if not tested:self.skipTest('No installed Chromium browser for launch')

    def test_local_attachment_selects_tab_without_closing_browser(self):
        self.tools.close()
        executable=find_browser_program('brave') or find_browser_program('chrome') or find_browser_program('edge')
        if not executable:self.skipTest('No installed Chromium browser for CDP attachment')
        profile=Path(self.folder.name)/'test-browser-profile'
        process=subprocess.Popen([executable,f'--user-data-dir={profile}','--headless',
                                  '--remote-debugging-port=0','--disable-gpu','--no-first-run',self.url],
                                 stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.addCleanup(self._stop_browser,process)
        endpoint_file=profile/'DevToolsActivePort'
        deadline=time.monotonic()+15
        while not endpoint_file.is_file() and time.monotonic()<deadline and process.poll() is None:
            time.sleep(.1)
        self.assertTrue(endpoint_file.is_file(),'Disposable browser failed to start its CDP endpoint')
        port=browser_port(endpoint_file.read_text(encoding='ascii').splitlines()[0])
        tools=BrowserTools(self.folder.name,threading.Event(),mode='attach',cdp_port=port)
        try:
            with self.assertRaisesRegex(ValueError,'List the connected browser tabs'):
                tools.execute('inspect')
            deadline=time.monotonic()+7
            tab=None
            while time.monotonic()<deadline and tab is None:
                tabs=json.loads(tools.execute('tabs'))['tabs']
                tab=next((item for item in tabs if item['url']==self.url),None)
                if tab is None:time.sleep(.1)
            self.assertIsNotNone(tab,'Fixture tab was not recognized')
            self.assertIn('Original fixture',json.loads(tools.execute('switch',tab['id']))['page'])
            self.assertIn('Saved successfully',json.loads(tools.execute('click','Save project'))['page'])
        finally:
            tools.close()
        with urlopen(f'http://127.0.0.1:{port}/json/list',timeout=5) as response:
            self.assertTrue(any(item['url']==self.url for item in json.load(response)))

    @staticmethod
    def _stop_browser(process):
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)


if __name__=='__main__':unittest.main()
