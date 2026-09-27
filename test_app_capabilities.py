"""End-to-end tool routing for app preferences and public research evaluation."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import agent_core
from app_preferences import AppPreferenceManager


class AppCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {'work_session_minutes': 120}
        self.saved = []
        self.manager = AppPreferenceManager(self.config, lambda: self.saved.append(dict(self.config)),
                                            self.root / 'preference-audit.jsonl')

    def test_agent_can_change_and_restore_audited_future_default(self):
        tools = agent_core.ProjectTools(self.root, True)
        tools.app_preferences = self.manager
        allowed = json.loads(tools.execute('inspect_app_preferences', {}))
        self.assertEqual(allowed['preferences']['work_session_minutes'], 120)
        changed = json.loads(tools.execute('set_app_preferences',
                                           {'changes':'{"work_session_minutes":240}'}))
        self.assertEqual(self.config['work_session_minutes'], 240)
        self.assertEqual(self.saved[-1]['work_session_minutes'], 240)
        restored = json.loads(tools.execute('rollback_app_preferences',
                                            {'change_id':changed['change_id']}))
        self.assertTrue(restored['changed'])
        self.assertEqual(self.config['work_session_minutes'], 120)
        plan = agent_core.ProjectTools(self.root, False)
        plan.app_preferences = self.manager
        with self.assertRaises(PermissionError):
            plan.execute('set_app_preferences', {'changes':'{"work_session_minutes":240}'})
        with self.assertRaisesRegex(ValueError, 'cannot be changed'):
            tools.execute('set_app_preferences', {'changes':'{"access_mode":"full_user"}'})

    def test_settings_request_exposes_change_tool_only_in_act(self):
        for act in (False, True):
            payloads = []
            def stream(_url, payload, _cancel):
                payloads.append(payload)
                yield {'message': {'content': 'Ready.'}, 'done': True,
                       'eval_count': 1, 'eval_duration': 1000000000}
            with patch.object(agent_core,'stream_chat',side_effect=stream):
                agent_core.run_agent('fixture','fixture',
                    [{'role':'user','content':'Change my app settings for future tasks.'}],
                    self.root,act,threading.Event(),lambda *_:None,rounds=1,
                    performance={'num_ctx':32768},app_preferences=self.manager)
            names = {tool['function']['name'] for tool in payloads[0]['tools']}
            self.assertIn('inspect_app_preferences',names)
            self.assertEqual('set_app_preferences' in names,act)


if __name__ == '__main__':
    unittest.main()
