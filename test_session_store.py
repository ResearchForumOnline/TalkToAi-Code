import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from session_store import load_tasks, save_tasks, decode_tasks, matches_task


def task(title='Game task'):
    return {'id': 'one', 'title': title, 'project': 'C:/projects/game',
            'messages': [{'role': 'user', 'content': 'Fix the inventory menu'}], 'changes': []}


class SessionStoreTests(unittest.TestCase):
    def test_round_trip_previous_save_and_noop(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'studio.json'
            save_tasks(path,[task()]);save_tasks(path,[task('Updated')])
            backup=path.with_suffix('.json.bak')
            self.assertEqual(decode_tasks(backup.read_bytes())[0]['title'],'Game task')
            previous=backup.read_bytes()
            save_tasks(path,[task('Updated')])
            self.assertEqual(backup.read_bytes(),previous)
            tasks,notice=load_tasks(path)
            self.assertEqual(tasks[0]['title'],'Updated');self.assertEqual(notice,'')
            self.assertFalse(list(Path(folder).glob('*.tmp')))

    def test_corrupt_primary_is_preserved_and_backup_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'studio.json'
            save_tasks(path,[task()]);save_tasks(path,[task('Updated')])
            path.write_bytes(b'{bad json')
            tasks,notice=load_tasks(path)
            self.assertIn('recovered',notice)
            self.assertEqual(tasks[0]['title'],'Game task')
            self.assertEqual(json.loads(path.read_text())[0]['title'],'Game task')
            preserved=list(Path(folder).glob('*.corrupt-*'))
            self.assertEqual(len(preserved),1);self.assertEqual(preserved[0].read_bytes(),b'{bad json')
            save_tasks(path,tasks)

    def test_both_invalid_files_are_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'studio.json';path.write_bytes(b'\xff')
            path.with_suffix('.json.bak').write_text('{bad')
            tasks,notice=load_tasks(path)
            self.assertEqual(tasks,[]);self.assertIn('No valid recovery',notice)
            self.assertEqual(len(list(Path(folder).glob('*.corrupt-*'))),2)
            save_tasks(path,[])

    def test_missing_primary_recovers_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'studio.json'
            path.with_suffix('.json.bak').write_text(json.dumps([task()]))
            tasks,notice=load_tasks(path)
            self.assertEqual(len(tasks),1);self.assertIn('recovered',notice)
            self.assertTrue(path.exists())

    def test_bad_in_memory_data_does_not_touch_history(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'studio.json';save_tasks(path,[task()]);raw=path.read_bytes()
            for invalid in ({},[None],[task(),task()]):
                with self.assertRaises(ValueError):save_tasks(path,invalid)
                self.assertEqual(path.read_bytes(),raw)

    def test_failed_replace_leaves_original_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'studio.json';save_tasks(path,[task()]);raw=path.read_bytes()
            with patch('pathlib.Path.replace',side_effect=PermissionError('locked')):
                with self.assertRaises(PermissionError):save_tasks(path,[task('new')])
            self.assertEqual(path.read_bytes(),raw)
            self.assertFalse(list(Path(folder).glob('*.tmp')))

    def test_corrupted_disk_copy_is_not_rotated_over_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'studio.json';save_tasks(path,[task()]);save_tasks(path,[task('new')])
            backup=path.with_suffix('.json.bak').read_bytes();path.write_text('{broken')
            with self.assertRaises(ValueError):save_tasks(path,[task()])
            self.assertEqual(path.with_suffix('.json.bak').read_bytes(),backup)
            self.assertEqual(path.read_text(),'{broken')

    def test_legacy_defaults_and_full_text_search(self):
        data=decode_tasks(json.dumps([task()]))[0]
        self.assertFalse(data['archived']);self.assertFalse(data['pinned'])
        self.assertTrue(matches_task(data,'INVENTORY game'))
        data['draft']='unfinished movement controller'
        self.assertTrue(matches_task(data,'movement'))
        self.assertFalse(matches_task(data,'missing term'))

    def test_malformed_nested_records_rejected(self):
        for key,value in [('messages',[None]),('changes',[{}]),('artifacts',[{}]),('draft',None),('activity',[1])]:
            data=copy.deepcopy(task());data[key]=value
            with self.assertRaises(ValueError):decode_tasks(json.dumps([data]))


if __name__=='__main__':unittest.main()
