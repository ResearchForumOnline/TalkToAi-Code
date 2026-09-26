"""Opt-in real browser checks against a disposable loopback HTTP fixture only."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from browser_tools import BrowserTools


class Fixture(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path.startswith('/report'):
            html='<title>Report</title><h1>Report ready</h1><button onclick="window.close()">Close report</button>'
        else:
            html='''<title>Fixture</title><h1>Original fixture</h1>
<button aria-label="Save project" onclick="document.querySelector('#status').textContent='Saved successfully'">+</button>
<p id="status">Waiting</p>
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


if __name__=='__main__':unittest.main()
