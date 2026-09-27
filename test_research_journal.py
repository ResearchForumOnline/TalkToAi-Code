import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from contextlib import contextmanager
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

    def test_verification_detects_same_size_edits_missing_and_matches_without_mutation(self):
        for name in ('same.csv','edit.csv','gone.csv'):(self.root/name).write_bytes(b'original')
        self.record(evidence_paths=json.dumps(['same.csv','edit.csv','gone.csv']))
        path=self.root/'.talktoai-code'/'EXPERIMENTS.jsonl';original=path.read_bytes()
        (self.root/'edit.csv').write_bytes(b'modified');(self.root/'gone.csv').unlink()
        report=json.loads(journal.read_experiments(self.root,verify_evidence='true'))
        check=report['entries'][0]['evidence_check']
        self.assertEqual(check['status'],'needs_attention')
        self.assertEqual([item['status'] for item in check['files']],['match','changed','missing'])
        self.assertEqual(report['evidence_bytes_read'],16)
        self.assertIn('+00:00',report['evidence_checked_utc'])
        self.assertEqual(path.read_bytes(),original)

    def test_default_read_never_rehashes_and_empty_evidence_is_not_verified(self):
        self.record()
        with patch.object(journal,'_hash_evidence',side_effect=AssertionError('must not hash')):
            self.assertNotIn('evidence_check',json.loads(journal.read_experiments(self.root))['entries'][0])
            checked=json.loads(journal.read_experiments(self.root,verify_evidence=True))
        self.assertEqual(checked['entries'][0]['evidence_check']['status'],'no_evidence')

    def test_verification_budget_is_cumulative_and_prefers_recent_entries(self):
        (self.root/'result.csv').write_bytes(b'1234')
        self.record(evidence_paths='["result.csv"]');self.record(evidence_paths='["result.csv"]')
        with patch.object(journal,'MAX_VERIFY_BYTES',4):
            checked=json.loads(journal.read_experiments(self.root,verify_evidence=True))
        self.assertEqual(checked['evidence_bytes_read'],4)
        self.assertEqual(checked['entries'][1]['evidence_check']['status'],'all_match')
        self.assertEqual(checked['entries'][0]['evidence_check']['files'][0]['status'],'budget_exceeded')

    def test_tampered_evidence_reference_cannot_read_private_files(self):
        (self.root/'result.csv').write_bytes(b'1234');self.record(evidence_paths='["result.csv"]')
        path=self.root/'.talktoai-code'/'EXPERIMENTS.jsonl'
        entry=json.loads(path.read_text());entry['evidence'][0]['path']='credentials.json'
        path.write_text(json.dumps(entry)+'\n');original=path.read_bytes()
        with patch.object(journal,'_hash_evidence',side_effect=AssertionError('private path must be rejected')):
            report=json.loads(journal.read_experiments(self.root,verify_evidence=True))
        self.assertEqual(report['entries'][0]['evidence_check']['files'][0]['status'],'blocked')
        self.assertEqual(path.read_bytes(),original)

    def test_invalid_record_and_verification_argument_are_rejected_safely(self):
        self.record();path=self.root/'.talktoai-code'/'EXPERIMENTS.jsonl'
        entry=json.loads(path.read_text());entry['evidence']=[{'path':'result.csv','sha256':'fake','bytes':0}]
        path.write_text(json.dumps(entry)+'\n')
        checked=json.loads(journal.read_experiments(self.root,verify_evidence=True))
        self.assertEqual(checked['entries'][0]['evidence_check']['files'][0]['status'],'invalid_record')
        for value in ('yes',1,[],None):
            with self.subTest(value=value),self.assertRaises(ValueError):journal.read_experiments(self.root,verify_evidence=value)

    def test_file_changed_during_hash_is_not_accepted(self):
        evidence=self.root/'result.csv';evidence.write_bytes(b'1234')
        original_open=Path.open
        @contextmanager
        def mutate_after_read(path,*args,**kwargs):
            with original_open(path,*args,**kwargs) as stream:yield stream
            with original_open(path,'wb') as stream:stream.write(b'longer changed result')
        with patch.object(Path,'open',mutate_after_read):
            with self.assertRaisesRegex(ValueError,'changed while being read'):journal._hash_evidence(evidence)

    def test_evidence_replaced_by_external_link_is_not_rehashed(self):
        evidence=self.root/'result.csv';evidence.write_bytes(b'1234');self.record(evidence_paths='["result.csv"]')
        with tempfile.TemporaryDirectory() as outside:
            target=Path(outside)/'result.csv';target.write_bytes(b'1234');evidence.unlink()
            try:evidence.symlink_to(target)
            except OSError:self.skipTest('Symlink privilege unavailable')
            with patch.object(journal,'_hash_evidence',side_effect=AssertionError('link must not be read')):
                data=json.loads(journal.read_experiments(self.root,verify_evidence=True))
            self.assertEqual(data['entries'][0]['evidence_check']['files'][0]['status'],'blocked')

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

    def test_compare_reports_arithmetic_and_rechecks_both_evidence_files(self):
        evidence=self.root/'result.csv';evidence.write_text('baseline',encoding='utf-8')
        protocol=json.dumps({'dataset':'fixture v1','split':'held-out A','seed':'7',
                             'environment':'Python 3.12','controls':'same input and budget',
                             'budget':'20 seconds','metric_definition':'percent error on fixed cases',
                             'sample_size':'10 cases','source_urls':['https://example.org/method']})
        baseline=self.record(metrics='{"error_percent":20}',evidence_paths='["result.csv"]',protocol=protocol)
        evidence.write_text('candidate',encoding='utf-8')
        candidate=self.record(metrics='{"error_percent":12}',evidence_paths='["result.csv"]',protocol=protocol)
        before=(self.root/'.talktoai-code'/'EXPERIMENTS.jsonl').read_bytes()
        comparison=json.loads(journal.compare_experiments(self.root,baseline['id'],candidate['id'],'error_percent'))
        self.assertEqual(comparison['candidate_minus_baseline'],-8)
        self.assertTrue(comparison['reported_improvement'])
        self.assertEqual(comparison['reported_numeric_direction'],'lower')
        self.assertEqual(comparison['comparison_status'],'limitations_found')
        self.assertEqual(comparison['evidence_checks']['baseline']['status'],'needs_attention')
        self.assertEqual(comparison['evidence_checks']['candidate']['status'],'all_match')
        self.assertEqual(before,(self.root/'.talktoai-code'/'EXPERIMENTS.jsonl').read_bytes())

    def test_compare_requires_same_reported_protocol_for_comparable_label(self):
        (self.root/'base.txt').write_text('base',encoding='utf-8')
        (self.root/'new.txt').write_text('new',encoding='utf-8')
        protocol={'dataset':'same','split':'test','seed':'9','environment':'fixture',
                  'controls':'fixed','budget':'same','metric_definition':'exact score','sample_size':'10'}
        baseline=self.record(metrics={'score':4},evidence_paths=['base.txt'],protocol=protocol)
        candidate=self.record(metrics={'score':6},evidence_paths=['new.txt'],protocol=protocol)
        comparison=json.loads(journal.compare_experiments(self.root,baseline['id'],candidate['id'],'score','maximize'))
        self.assertEqual(comparison['comparison_status'],'comparable_as_reported')
        self.assertTrue(comparison['reported_improvement'])
        self.assertEqual(comparison['warnings'],[])
        self.assertIn('do not prove measurement validity',comparison['verification'])

    def test_compare_rejects_missing_metric_invalid_direction_and_credential_url(self):
        baseline=self.record(metrics={'runtime_seconds':2})
        candidate=self.record(metrics={'runtime_seconds':1})
        with self.assertRaisesRegex(ValueError,'lacks the requested metric'):
            journal.compare_experiments(self.root,baseline['id'],candidate['id'],'accuracy')
        with self.assertRaisesRegex(ValueError,'direction'):
            journal.compare_experiments(self.root,baseline['id'],candidate['id'],'runtime_seconds','sideways')
        with self.assertRaisesRegex(ValueError,'Source URLs'):
            self.record(protocol={'source_urls':['https://example.org/paper?token=secret']})

    def test_claim_to_source_uncertainty_is_recorded_and_flagged(self):
        protocol={'dataset':'same','split':'held-out','seed':'4','environment':'fixture',
                  'controls':'same','budget':'same','metric_definition':'seconds','sample_size':'5',
                  'source_claims':[{'claim':'The method is reproducible','url':'https://example.org/paper',
                                    'relationship':'unverified'}]}
        baseline=self.record(metrics={'runtime_seconds':3},protocol=protocol)
        candidate=self.record(metrics={'runtime_seconds':2},protocol=protocol)
        comparison=json.loads(journal.compare_experiments(self.root,baseline['id'],candidate['id'],'runtime_seconds'))
        self.assertTrue(any('unverified source claims' in warning for warning in comparison['warnings']))
        with self.assertRaisesRegex(ValueError,'relationship'):
            self.record(protocol={'source_claims':[{'claim':'x','url':'https://example.org',
                                                    'relationship':'certain'}]})

    def test_parseable_damage_is_rejected_without_rewriting_journal(self):
        baseline=self.record(metrics={'score':1})
        candidate=self.record(metrics={'score':2})
        path=self.root/'.talktoai-code'/'EXPERIMENTS.jsonl'
        original=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        cases=[('missing id', lambda row: row.pop('id')),
               ('missing hypothesis', lambda row: row.pop('hypothesis')),
               ('non-object protocol', lambda row: row.update(reported_protocol=['invalid'])),
               ('malformed source claim', lambda row: row.update(reported_protocol={
                   'source_claims':[{'claim':'x','url':'https://example.org','relationship':['invalid']}] }))]
        for label, mutate in cases:
            with self.subTest(label=label):
                rows=[dict(entry) for entry in original]
                mutate(rows[0])
                damaged=''.join(json.dumps(row)+'\n' for row in rows).encode('utf-8')
                path.write_bytes(damaged)
                with self.assertRaisesRegex(ValueError,'damaged'):
                    journal.read_experiments(self.root)
                with self.assertRaisesRegex(ValueError,'damaged'):
                    journal.compare_experiments(self.root,baseline['id'],candidate['id'],'score')
                with self.assertRaisesRegex(ValueError,'damaged'):
                    self.record()
                self.assertEqual(path.read_bytes(),damaged)
                self.assertFalse(path.with_suffix('.lock').exists())


if __name__ == '__main__': unittest.main()
