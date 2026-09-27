"""Focused tests for audited agent changes to non-secret app preferences."""

import json
from pathlib import Path
import tempfile
import unittest

from app_preferences import AppPreferenceManager


class AppPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.config = {'keep_going': False, 'num_ctx': 8192,
                       'gmail_client_id': 'PRIVATE_CLIENT_MARKER',
                       'approval_policy': 'ask_remote'}
        self.config_file = self.base / 'config.json'
        self.audit_file = self.base / 'app-preference-audit.jsonl'
        self.persist_calls = 0

        def persist():
            self.persist_calls += 1
            self.config_file.write_text(json.dumps(self.config), encoding='utf-8')

        self.manager = AppPreferenceManager(self.config, persist, self.audit_file)

    def test_change_persists_allowed_values_and_audit_excludes_private_config(self):
        result = self.manager.change({'keep_going': True, 'num_ctx': 16384, 'web_browser': 'firefox'})
        self.assertTrue(result['changed'])
        self.assertEqual(1, self.persist_calls)
        self.assertEqual(16384, json.loads(self.config_file.read_text())['num_ctx'])
        self.assertEqual('firefox', self.manager.inspect()['preferences']['web_browser'])
        self.assertEqual(result['change_id'], self.manager.history()[0]['id'])
        audit = self.audit_file.read_text()
        self.assertNotIn('PRIVATE_CLIENT_MARKER', audit)
        self.assertNotIn('gmail_client_id', audit)
        self.assertNotIn('approval_policy', audit)
        self.assertIn('Future requests', result['applies'])

    def test_rejects_permission_paid_search_and_bad_types_without_writing(self):
        for changes in ({'pc_pilot': True}, {'remote_pilot': True},
                        {'approval_policy': 'auto_remote'}, {'web_search': 'serper'},
                        {'num_ctx': True}, {'keep_going': 1}, {'work_session_minutes': 999},
                        {'web_browser': 'unknown'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.manager.change(changes)
        self.assertFalse(self.audit_file.exists())
        self.assertEqual(0, self.persist_calls)

    def test_rollback_survives_manager_reload_and_restores_missing_key(self):
        result = self.manager.change({'keep_going': True, 'web_browser': 'edge'})
        reloaded = AppPreferenceManager(self.config, self.manager.persist, self.audit_file)
        undone = reloaded.rollback(result['change_id'])
        self.assertTrue(undone['changed'])
        self.assertEqual(result['change_id'], undone['rollback_of'])
        self.assertFalse(self.config['keep_going'])
        self.assertNotIn('web_browser', self.config)
        self.assertEqual('auto', reloaded.inspect()['preferences']['web_browser'])
        self.assertEqual(2, self.persist_calls)
        with self.assertRaisesRegex(ValueError, 'already been rolled back'):
            reloaded.rollback(result['change_id'])

    def test_rollback_refuses_to_overwrite_later_settings_change(self):
        first = self.manager.change({'num_ctx': 16384})
        self.config['num_ctx'] = 32768  # user changed Settings after the agent's edit
        with self.assertRaisesRegex(ValueError, 'changed since'):
            self.manager.rollback(first['change_id'])
        self.assertEqual(32768, self.config['num_ctx'])
        self.assertEqual(1, self.persist_calls)

    def test_rollback_refuses_later_audited_change_even_if_value_matches(self):
        first = self.manager.change({'num_ctx': 16384})
        self.manager.change({'num_ctx': 32768})
        self.manager.change({'num_ctx': 16384})
        with self.assertRaisesRegex(ValueError, 'later audited change'):
            self.manager.rollback(first['change_id'])

    def test_no_op_does_not_write_audit_or_config(self):
        result = self.manager.change({'keep_going': False})
        self.assertFalse(result['changed'])
        self.assertFalse(self.audit_file.exists())
        self.assertEqual(0, self.persist_calls)

    def test_failed_persist_restores_live_config_and_leaves_uncommitted_audit(self):
        def fail():
            raise OSError('disk unavailable')

        broken = AppPreferenceManager(self.config, fail, self.audit_file)
        with self.assertRaises(OSError):
            broken.change({'keep_going': True})
        self.assertFalse(self.config['keep_going'])
        self.assertEqual([], broken.history())
        self.assertEqual(0, broken.inspect()['pending_audit_records'])

    def test_corrupt_audit_is_preserved_and_blocks_changes(self):
        self.audit_file.write_text('{not valid json\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'damaged'):
            self.manager.change({'keep_going': True})
        self.assertEqual('{not valid json\n', self.audit_file.read_text())
        self.assertFalse(self.config['keep_going'])

    def test_unsupported_current_value_never_leaks_into_audit_or_inspection(self):
        self.config['web_browser'] = 'PRIVATE_CLIENT_MARKER'
        self.assertNotIn('PRIVATE_CLIENT_MARKER', str(self.manager.inspect()))
        with self.assertRaisesRegex(ValueError, 'unsupported saved value'):
            self.manager.change({'web_browser': 'auto'})
        self.assertFalse(self.audit_file.exists())


if __name__ == '__main__':
    unittest.main()
