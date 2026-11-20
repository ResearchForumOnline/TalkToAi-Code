"""Opt-in real browser checks against a disposable loopback HTTP fixture only."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.request import urlopen
from browser_tools import BrowserTools, browser_port, find_browser_program


class Fixture(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path.startswith('/report'):
            html='<title>Report</title><h1>Report ready</h1><button onclick="window.close()">Close report</button>'
        else:
            html='''<title>Fixture</title><h1>Original fixture</h1>
<button aria-label="Save project" onclick="document.querySelector('#status').textContent='Saved successfully'">+</button>
<p id="status">Waiting</p>
<label>Player name <input oninput="document.querySelector('#status').textContent=this.value"></label>
<label>Private field <input type="password"></label>
<button aria-label="Duplicate" onclick="document.querySelector('#status').textContent='WRONG'">A</button>
<button aria-label="Duplicate" onclick="document.querySelector('#status').textContent='WRONG'">B</button>
<a target="_blank" href="/report">Open report</a>
<button onclick="window.open('/report?1');window.open('/report?2')">Open two reports</button>'''
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
        self.assertNotIn('WRONG',json.loads(self.tools.execute('inspect'))['page'])
    def test_popup_follow_and_closed_popup_recovery(self):
        result=json.loads(self.tools.execute('click','Open report'))
        self.assertTrue(result['url'].endswith('/report'));self.assertIn('Report ready',result['page'])
        self.assertEqual(len(self.tools.context.pages),2)
        result=json.loads(self.tools.execute('click','Close report'))
        self.assertIn('Original fixture',result['page']);self.assertEqual(result['url'],self.url)
        self.assertEqual(len(self.tools.context.pages),1)
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
    def test_cancel_during_target_resolution_does_not_click(self):
        locator=MagicMock();locator.filter.return_value=locator
        def count():self.tools.cancel.set();return 1
        locator.count.side_effect=count
        with patch.object(self.tools.page,'get_by_role',return_value=locator):
            with self.assertRaises(InterruptedError):self.tools.execute('click','Save project')
        locator.click.assert_not_called()

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
