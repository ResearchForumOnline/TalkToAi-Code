from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sample_projects import ensure_score_arena, SCORE_ARENA_FILES


class SampleProjectTests(unittest.TestCase):
    def test_copies_every_bundled_file_and_preserves_user_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            project=ensure_score_arena(Path(__file__).parent,folder)
            self.assertTrue(all((project/name).is_file() for name in SCORE_ARENA_FILES))
            (project/'main.gd').write_text('my edits')
            self.assertEqual(ensure_score_arena(Path(__file__).parent,folder),project)
            self.assertEqual((project/'main.gd').read_text(),'my edits')

    def test_missing_source_does_not_leave_partial_project(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):ensure_score_arena(folder,Path(folder)/'projects')
            self.assertFalse((Path(folder)/'projects/score-arena').exists())

    def test_existing_unknown_folder_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'score-arena';target.mkdir();(target/'work.txt').write_text('keep')
            with self.assertRaises(ValueError):ensure_score_arena(Path(__file__).parent,folder)
            self.assertEqual((target/'work.txt').read_text(),'keep')

    def test_copy_failure_cleans_only_disposable_staging(self):
        with tempfile.TemporaryDirectory() as folder,patch('sample_projects.shutil.copyfile',side_effect=OSError('copy failed')):
            with self.assertRaises(OSError):ensure_score_arena(Path(__file__).parent,folder)
            self.assertEqual(list(Path(folder).iterdir()),[])


if __name__=='__main__':unittest.main()
