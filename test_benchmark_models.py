import threading
import unittest
from unittest.mock import patch
import benchmark_models as bench


class ModelComparisonTests(unittest.TestCase):
    def test_code_validation_rejects_arbitrary_programs(self):
        self.assertTrue(bench.valid_clamp('def clamp(value, low, high):\n    return max(low, min(value, high))'))
        self.assertFalse(bench.valid_clamp('import os\nos.system("bad")'))
        self.assertFalse(bench.valid_clamp('def clamp(value, low, high):\n    return value'))

    def test_same_fixtures_are_used_and_calls_are_not_executed(self):
        payloads=[]
        def stream(url,payload,cancel):
            payloads.append((url,payload))
            message=({'tool_calls':[{'function':{'name':'set_player_name','arguments':{'name':'Alex'}}}]}
                     if payload.get('tools') else {'content':'def clamp(value, low, high):\n    return max(low, min(value, high))'})
            yield {'message':message,'done':True,'done_reason':'stop','eval_count':20,'eval_duration':2000000000}
        config={'server_model':'server30','local_large_model':'local27'}
        with patch.object(bench,'inventory',return_value=['server30','local27']),patch.object(bench,'_http_stream',side_effect=stream):
            report=bench.compare_routes(config,threading.Event())
        self.assertEqual(len(payloads),4)
        self.assertTrue(all(r['quality_pass'] for r in report['results']))
        self.assertTrue(all(not r['agent_pass'] for r in report['results']))
        self.assertEqual(payloads[0][1]['messages'],payloads[2][1]['messages'])
        self.assertEqual(payloads[1][1]['messages'],payloads[3][1]['messages'])
        self.assertEqual(config,{'server_model':'server30','local_large_model':'local27'})

    def test_missing_models_do_not_download_and_cancel_stops(self):
        with patch.object(bench,'inventory',return_value=[]),patch.object(bench,'_http_stream') as transport:
            report=bench.compare_routes({'server_model':'missing','local_large_model':'missing'},threading.Event())
            self.assertTrue(all(not r['success'] for r in report['results']));transport.assert_not_called()
            cancel=threading.Event();cancel.set()
            self.assertEqual(bench.compare_routes({},cancel)['results'],[])

    def test_wrong_tool_and_truncated_code_fail_quality_gate(self):
        def stream(url,payload,cancel):
            yield {'message':{'content':'def clamp(value, low, high):\n    return max(low, min(value, high))'},'done':True,'done_reason':'length'}
        with patch.object(bench,'inventory',return_value=['m']),patch.object(bench,'_http_stream',side_effect=stream):
            report=bench.compare_routes({'server_model':'m','local_large_model':'m'},threading.Event())
        self.assertTrue(all(not r['quality_pass'] for r in report['results']))
