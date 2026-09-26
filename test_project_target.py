"""Project targeting regressions: no model, private files, network or UI needed."""
from pathlib import Path
import tempfile
import unittest
from project_context import resolve_project_target, requested_runtime


class ProjectTargetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()

    def game(self, relative, title):
        root=self.root/relative;root.mkdir(parents=True,exist_ok=True)
        (root/'project.godot').write_text('[application]\nconfig/name="'+title+'"\n',encoding='utf-8')
        return root

    def test_nightfall_exact_engine_name_beats_unrelated_root_and_parent(self):
        (self.root/'package.json').write_text('{"name":"airr-cloud"}')
        self.game('blacksite_nightfall','BLACKSITE: NIGHTFALL')
        native=self.game('blacksite_nightfall/native','NIGHTFALL')
        result=resolve_project_target(self.root,f'use AMD always, {self.root} game: NIGHTFALL. make it better')
        self.assertEqual(Path(result['root']),native)
        self.assertNotIn('error',result)

    def test_explicit_path_with_spaces_overrides_old_selected_project(self):
        named=self.game('my games/Nightfall','NIGHTFALL')
        old=self.root/'old';old.mkdir()
        result=resolve_project_target(old,f'Improve "{named}" please')
        self.assertEqual(Path(result['root']),named)

    def test_tied_projects_ask_for_target_instead_of_guessing(self):
        self.game('nightfall_a','NIGHTFALL');self.game('nightfall_b','NIGHTFALL')
        result=resolve_project_target(self.root,'game: NIGHTFALL. improve it')
        self.assertIn('Multiple projects',result['error'])
        self.assertEqual(len(result['candidates']),2)

    def test_missing_target_does_not_wander_into_other_directories(self):
        self.game('other','OTHER')
        result=resolve_project_target(self.root,'game: MISSING. improve it')
        self.assertIn('Could not locate',result['error'])
        self.assertEqual(result['root'],str(self.root))

    def test_generated_and_hidden_directories_are_excluded(self):
        self.game('build/nightfall','NIGHTFALL');self.game('.private/nightfall','NIGHTFALL')
        result=resolve_project_target(self.root,'game: NIGHTFALL.')
        self.assertEqual(result['candidates'],[])

    def test_bounded_search_does_not_guess_when_incomplete(self):
        self.game('nightfall_a','NIGHTFALL');self.game('nightfall_b','NIGHTFALL')
        result=resolve_project_target(self.root,'game: NIGHTFALL.',max_directories=1)
        self.assertTrue(result['truncated']);self.assertIn('error',result)

    def test_existing_project_without_named_target_is_kept(self):
        self.assertEqual(resolve_project_target(self.root,'Add a pause menu')['root'],str(self.root))

    def test_embedded_runtime_instruction_is_affirmative_only(self):
        self.assertEqual(requested_runtime('use AMD always, make my game better'),'server')
        self.assertEqual(requested_runtime('Please use AMD. Improve this app'),'server')
        self.assertIsNone(requested_runtime('Do not use AMD; use local'))
        self.assertIsNone(requested_runtime('Does this work with AMD?'))


if __name__=='__main__':unittest.main()
