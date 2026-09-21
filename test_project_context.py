from pathlib import Path
import tempfile
import unittest
from agent_core import ProjectTools

class ContextTests(unittest.TestCase):
    def test_map_and_search_are_bounded_and_exclude_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'player.gd').write_text('extends Node2D\nfunc move_player():\n    pass\n',encoding='utf-8')
            (root/'score.py').write_text('def add_score(a, b):\n    return a + b\n',encoding='utf-8')
            (root/'credentials.json').write_text('{"fake":"secret_marker"}',encoding='utf-8')
            (root/'build').mkdir();(root/'build'/'generated.py').write_text('secret_marker = 1',encoding='utf-8')
            tools=ProjectTools(root)
            result=tools.execute('project_map',{'query':'player'})
            self.assertIn('func move_player',result)
            self.assertIn('def add_score',result)
            self.assertNotIn('credentials',result)
            self.assertNotIn('generated.py',result)
            result=tools.execute('search_code',{'query':'move_player'})
            self.assertIn('player.gd:2',result)
            self.assertIn('No matches',tools.execute('search_code',{'query':'secret_marker'}))
            with self.assertRaises(PermissionError):tools.execute('read_file',{'path':'credentials.json'})

    def test_git_handles_nonrepository(self):
        with tempfile.TemporaryDirectory() as folder:
            result=ProjectTools(folder).execute('git_changes',{})
            self.assertTrue('not inside a Git repository' in result or 'not installed' in result)

if __name__=='__main__':unittest.main()
