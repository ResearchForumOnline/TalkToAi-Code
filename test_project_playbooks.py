import json
from pathlib import Path
import tempfile
import unittest

from project_playbooks import find_playbooks, save_playbook


class PlaybookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        (self.root / 'test_result.txt').write_text('all passed', encoding='utf-8')

    def save(self, **overrides):
        values = dict(title='Godot import repair', when_to_use='Fix Godot parse and import errors',
                      steps=['Read the parse error.', 'Fix the source and rerun the import check.'],
                      verification='Import returned zero after repair.', evidence_paths=['test_result.txt'])
        values.update(overrides)
        return save_playbook(self.root, **values)

    def test_evidence_change_invalidates_previous_workflow_observation(self):
        self.save()
        self.assertEqual(find_playbooks(self.root, 'Godot import')['entries'][0]['evidence_status'], 'current')
        (self.root / 'test_result.txt').write_text('failure', encoding='utf-8')
        self.assertEqual(find_playbooks(self.root, 'Godot import')['entries'][0]['evidence_status'], 'needs_review')

    def test_search_does_not_inject_unrelated_workflows(self):
        self.save()
        self.assertEqual(find_playbooks(self.root, 'mail oauth')['entries'], [])

    def test_same_title_updates_in_place_and_increments_revision(self):
        first = self.save()
        second = self.save(steps=['Read current files before retrying.'])
        records = find_playbooks(self.root)['entries']
        self.assertEqual(len(records), 1)
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(records[0]['revision'], 2)

    def test_private_and_outside_evidence_is_rejected(self):
        for name in ['../test_result.txt', '.env', 'credentials.json', 'private/data.txt']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.save(evidence_paths=[name])

    def test_corrupt_library_is_preserved(self):
        folder = self.root / '.talktoai-code'; folder.mkdir()
        target = folder / 'PLAYBOOKS.json'; target.write_text('{broken')
        with self.assertRaises(ValueError): self.save()
        self.assertEqual(target.read_text(), '{broken')
        self.assertFalse((folder / 'PLAYBOOKS.lock').exists())

    def test_linked_library_is_rejected(self):
        folder = self.root / '.talktoai-code'
        outside = self.root / 'elsewhere'; outside.mkdir()
        try: folder.symlink_to(outside, target_is_directory=True)
        except OSError: self.skipTest('Directory links unavailable')
        with self.assertRaises(ValueError): self.save()

    def test_no_evidence_does_not_claim_validation_or_execute(self):
        marker = self.root / 'should-not-exist'
        self.save(steps=[f'Create {marker}'], evidence_paths=[])
        result = find_playbooks(self.root)['entries'][0]
        self.assertEqual(result['evidence_status'], 'no_evidence')
        self.assertFalse(marker.exists())

    def test_literal_credential_is_rejected(self):
        for text in ['key sk-' + 'a' * 30, 'password=secret123', 'api_key:123456', 'https://user:pass@example.org']:
            with self.subTest(text=text), self.assertRaises(ValueError): self.save(verification=text)


if __name__ == '__main__': unittest.main()
