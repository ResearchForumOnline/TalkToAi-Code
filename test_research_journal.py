import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import research_journal as journal


class ResearchJournalTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name).resolve()

    def record(self, **kwargs):
        return json.loads(journal.record_experiment(self.root, 'A faster loop retains accuracy', 'python experiment.py', 'Reported runtime improved', **kwargs))

    def test_append_read_hash_and_reported_metrics(self):
        evidence = self.root / 'results.csv'; evidence.write_bytes(b'error,runtime\n0.01,2.3\n')
        first = self.record(metrics='{"runtime_seconds":2.3}', evidence_paths='["results.csv"]', next_step='Repeat with three seeds')
        second = self.record()
        data = json.loads(journal.read_experiments(self.root))
        self.assertEqual(data['total_entries'], 2)
        entry = data['entries'][0]
        self.assertEqual(entry['id'], first['id']); self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(entry['reported_metrics'], {'runtime_seconds': 2.3})
        self.assertEqual(entry['reported_command'], 'python experiment.py')
        self.assertIn('not independently verified', entry['verification'])
        self.assertEqual(entry['evidence'][0]['sha256'], hashlib.sha256(evidence.read_bytes()).hexdigest())
        self.assertIn('+00:00', entry['created_utc'])
        self.assertEqual([e['sequence'] for e in data['entries']], [1, 2])
        self.assertEqual(json.loads(journal.read_experiments(self.root, 1))['entries'][0]['id'], second['id'])

    def test_reject_missing_private_and_outside_paths(self):
        for name in ('missing.csv', '../outside.txt', '.env', 'credentials.json', 'my_passwords.txt', 'private/data.csv', 'token.json'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.record(evidence_paths=json.dumps([name]))
        self.assertFalse((self.root / '.talktoai-code').exists())

    def test_limits_do_not_damage_existing_records(self):
        self.record()
        path = self.root / '.talktoai-code' / 'EXPERIMENTS.jsonl'
        original = path.read_bytes()
        with patch.object(journal, 'MAX_ENTRIES', 1):
            with self.assertRaises(ValueError): self.record()
        self.assertEqual(original, path.read_bytes())
        self.assertFalse(path.with_suffix('.lock').exists())
        for limit in (0, 21):
            with self.assertRaises(ValueError): journal.read_experiments(self.root, limit)
        for value in ('{"x":NaN}', '{"x":true}', '{"x":"verified"}'):
            with self.assertRaises(ValueError): self.record(metrics=value)

    def test_corruption_is_preserved_and_not_appended(self):
        path = self.root / '.talktoai-code' / 'EXPERIMENTS.jsonl'; path.parent.mkdir()
        path.write_text('broken-json\n')
        with self.assertRaisesRegex(ValueError, 'damaged'): self.record()
        self.assertEqual(path.read_text(), 'broken-json\n')

    def test_active_writer_lock_prevents_lost_updates(self):
        path = self.root / '.talktoai-code' / 'EXPERIMENTS.lock'; path.parent.mkdir(); path.touch()
        with self.assertRaisesRegex(ValueError, 'busy'): self.record()
        self.assertTrue(path.exists())

    def test_read_is_empty_without_creating_files(self):
        self.assertEqual(json.loads(journal.read_experiments(self.root))['entries'], [])
        self.assertFalse((self.root / '.talktoai-code').exists())

    def test_failed_replace_preserves_journal_and_releases_lock(self):
        self.record()
        path = self.root / '.talktoai-code' / 'EXPERIMENTS.jsonl'; original = path.read_bytes()
        with patch.object(journal.os, 'replace', side_effect=OSError('fixture disk error')):
            with self.assertRaises(OSError): self.record()
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(path.with_suffix('.lock').exists())
        self.assertEqual(list(path.parent.glob('experiments-*.tmp')), [])

    def test_byte_limits_apply_to_reads_and_evidence(self):
        self.record()
        with patch.object(journal, 'MAX_BYTES', 10):
            with self.assertRaisesRegex(ValueError, 'exceeds'): journal.read_experiments(self.root)
        (self.root / 'results.csv').write_bytes(b'123456789')
        with patch.object(journal, 'MAX_EVIDENCE_BYTES', 4):
            with self.assertRaisesRegex(ValueError, 'existing file'): self.record(evidence_paths='["results.csv"]')

    def test_core_dispatch_read_allowed_plan_record_requires_act(self):
        from agent_core import ProjectTools
        tools = ProjectTools(self.root, False, threading.Event())
        self.assertEqual(json.loads(tools.execute('read_experiments', {}))['total_entries'], 0)
        with self.assertRaises(PermissionError): tools.execute('record_experiment', {})
        tools.act = True
        result = json.loads(tools.execute('record_experiment', {'hypothesis':'A falsifiable prediction', 'result':'Not tested yet'}))
        self.assertEqual(result['sequence'], 1)

    def test_symlink_outside_project_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / 'evidence.txt'; target.write_text('outside')
            link = self.root / 'evidence.txt'
            try: link.symlink_to(target)
            except OSError: self.skipTest('Symlink privilege unavailable')
            with self.assertRaises(ValueError): self.record(evidence_paths='["evidence.txt"]')


if __name__ == '__main__': unittest.main()
