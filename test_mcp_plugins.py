import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest

from agent_core import ProjectTools
from mcp_plugins import McpPlugins


SERVER = """
import json
import os
import sys

for line in sys.stdin:
    message = json.loads(line)
    if 'id' not in message:
        continue
    method = message['method']
    if method == 'initialize':
        result = {'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'echo', 'inputSchema': {'type': 'object'}}]}
    else:
        args = message['params']['arguments']
        assert 'TALKTOAI_PLUGIN_TEST_SECRET' not in os.environ
        result = {'content': [{'type': 'text', 'text': args.get('text', '')}],
                  'isError': args.get('fail', False)}
    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
"""


class IntegrationTests(unittest.TestCase):
    def test_real_stdio_round_trip_and_act_permissions(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'TALKTOAI_PLUGIN_TEST_SECRET': 'test-value'}):
            path = Path(directory) / 'server.py'
            path.write_text(SERVER, encoding='utf-8')
            plugins = McpPlugins(directory, {'echo': {'command': sys.executable, 'args': [str(path)], 'enabled': True}}, threading.Event())
            try:
                plan = ProjectTools(directory, False)
                plan.plugins = plugins
                with self.assertRaises(PermissionError):
                    plan.execute('mcp_plugin', {'action': 'tools', 'plugin': 'echo'})
                act = ProjectTools(directory, True)
                act.plugins = plugins
                self.assertEqual(json.loads(act.execute('mcp_plugin', {'action': 'plugins'}))['enabled_plugins'], ['echo'])
                with self.assertRaises(PermissionError):
                    plugins.execute('call', 'echo', 'echo', '{}')
                self.assertEqual(plugins.execute('tools', 'echo', '', '{}')['tools'][0]['name'], 'echo')
                self.assertEqual(plugins.execute('tools', 'echo', 'echo', '{}')['input_schema']['type'], 'object')
                self.assertEqual(plugins.execute('call', 'echo', 'echo', '{"text":"verified"}')['content'], 'verified')
                with self.assertRaisesRegex(RuntimeError, 'plugin error'):
                    plugins.execute('call', 'echo', 'echo', '{"text":"plugin error","fail":true}')
                proc = plugins.sessions['echo'].process
            finally:
                plugins.close()
            self.assertIsNotNone(proc.poll())

    def test_project_data_plan_previews_csv_and_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data.csv').write_text('name,value\nalpha,3\n', encoding='utf-8')
            connection = sqlite3.connect(root / 'data.sqlite')
            try:
                connection.execute('CREATE TABLE measurements (sample TEXT, value REAL)')
                connection.commit()
            finally:
                connection.close()
            plan = ProjectTools(directory, False)
            csv_data = json.loads(plan.execute('inspect_project_data', {'path': 'data.csv'}))
            self.assertEqual(csv_data['preview'][1], ['alpha', '3'])
            db = json.loads(plan.execute('inspect_project_data', {'path': 'data.sqlite'}))
            self.assertEqual(db['objects'][0]['name'], 'measurements')
            with self.assertRaises(ValueError):
                plan.execute('inspect_project_data', {'path': '../data.csv'})
