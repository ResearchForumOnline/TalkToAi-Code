import io
import json
import unittest
from unittest.mock import patch
from runtime_diagnostics import probe, diagnose, report, MAX_BYTES


class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_valid_inventory(self):
        with patch('runtime_diagnostics.urllib.request.urlopen', return_value=io.BytesIO(b'{"models":[{"name":"a:latest"}]}')):
            self.assertEqual(probe(11434)['models'], ['a:latest'])

    def test_invalid_inventory(self):
        for raw in [b'<html>no</html>', b'[]', b'{"models":[{}]}', b'{"models":null}', b'x'*(MAX_BYTES+1)]:
            with self.subTest(raw=raw[:30]), patch('runtime_diagnostics.urllib.request.urlopen', return_value=io.BytesIO(raw)):
                self.assertEqual(probe(11435)['state'], 'invalid')

    def test_offline_has_no_raw_error(self):
        with patch('runtime_diagnostics.urllib.request.urlopen', side_effect=OSError('private detail')):
            self.assertEqual(probe(11434), {'state':'unreachable','models':[]})

    def test_shared_local_probe_and_latest_alias(self):
        with patch('runtime_diagnostics.probe', return_value={'state':'reachable','models':['a:latest']}) as p:
            rows=diagnose({'local_model':'a','local_large_model':'missing','server_model':'a'})
        self.assertEqual(p.call_count,2)
        self.assertEqual([r['installed'] for r in rows],[True,False,True])
        self.assertIn('Settings', rows[1]['action'])

    def test_offline_actions_and_custom_label(self):
        with patch('runtime_diagnostics.probe', return_value={'state':'unreachable','models':[]}):
            text=report({'server_label':'AMD'})
        self.assertIn('AMD',text);self.assertIn('SSH host alias',text);self.assertIn('Start or install Ollama',text)
        self.assertIn('does not load or download',text)

    def test_empty_model_requires_setting(self):
        with patch('runtime_diagnostics.probe', return_value={'state':'reachable','models':[]}):
            self.assertIn('Set this model ID',diagnose({})[0]['action'])

    def test_truncated_and_deep_json_are_invalid(self):
        from http.client import IncompleteRead
        for error in [IncompleteRead(b'partial'),RecursionError()]:
            with patch('runtime_diagnostics.urllib.request.urlopen',side_effect=error):
                self.assertEqual(probe(11434)['state'],'invalid')

    def test_deadline_stops_trickling_response(self):
        with patch('runtime_diagnostics.urllib.request.urlopen',return_value=io.BytesIO(b' ' * 20000)), patch('runtime_diagnostics.time.monotonic',side_effect=[0,0,4]):
            self.assertEqual(probe(11434)['state'],'unreachable')
