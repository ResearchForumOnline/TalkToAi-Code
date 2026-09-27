from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from agent_core import ProjectTools
from project_context import git_changes

class ContextTests(unittest.TestCase):
    def test_map_and_search_are_bounded_and_exclude_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'player.gd').write_text('extends Node2D\nfunc move_player():\n    pass\n',encoding='utf-8')
            (root/'score.py').write_text('def add_score(a, b):\n    return a + b\n',encoding='utf-8')
            (root/'credentials.json').write_text('{"fake":"secret_marker"}',encoding='utf-8')
            (root/'secrets.py').write_text('def player_secret():\n    pass\n',encoding='utf-8')
            (root/'build').mkdir();(root/'build'/'generated.py').write_text('secret_marker = 1',encoding='utf-8')
            tools=ProjectTools(root)
            result=tools.execute('project_map',{'query':'player'})
            self.assertIn('func move_player',result)
            self.assertIn('def add_score',result)
            self.assertNotIn('credentials',result)
            self.assertNotIn('secrets.py',result)
            self.assertNotIn('generated.py',result)
            result=tools.execute('search_code',{'query':'move_player'})
            self.assertIn('player.gd:2',result)
            self.assertIn('No matches',tools.execute('search_code',{'query':'secret_marker'}))
            with self.assertRaises(PermissionError):tools.execute('read_file',{'path':'credentials.json'})

    def test_map_finds_declared_symbol_beyond_path_only_output_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for index in range(70):
                (root/f'generic_{index:02}.py').write_text('def unrelated():\n    pass\n',encoding='utf-8')
            (root/'z_engine.py').write_text('def render_nightfall_scene():\n    pass\n',encoding='utf-8')
            result=ProjectTools(root).execute('project_map',{'query':'nightfall'})
            self.assertTrue(result.startswith('z_engine.py\n'),result[:250])
            self.assertIn('def render_nightfall_scene',result)
            self.assertLessEqual(len(result.split('\n\n')),61)

    def test_map_symbol_hit_outranks_incidental_path_hit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'nightfall_notes.py').write_text('def unrelated():\n    pass\n',encoding='utf-8')
            (root/'engine.py').write_text('def nightfall_animation():\n    pass\n',encoding='utf-8')
            result=ProjectTools(root).execute('project_map',{'query':'nightfall'})
            self.assertTrue(result.startswith('engine.py\n'),result[:250])

    def test_git_handles_nonrepository(self):
        with tempfile.TemporaryDirectory() as folder:
            result=ProjectTools(folder).execute('git_changes',{})
            self.assertTrue('not inside a Git repository' in result or 'not installed' in result)

    def test_git_change_command_reports_nonzero_status(self):
        with tempfile.TemporaryDirectory() as folder:
            answers=[SimpleNamespace(returncode=0,stdout=folder,stderr=''),
                     SimpleNamespace(returncode=128,stdout='',stderr='fatal: fixture failure')]
            with patch('project_context.subprocess.run',side_effect=answers):
                result=git_changes(ProjectTools(folder))
            self.assertIn('Git query failed: exit 128 for git status',result)

if __name__=='__main__':unittest.main()
