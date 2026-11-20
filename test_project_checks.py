import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from project_checks import detect_checks
from agent_core import ProjectTools


class CheckDetectionTests(unittest.TestCase):
    def test_web_game_checks_skip_placeholder_and_watch(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'package.json').write_text(json.dumps({'scripts':{'test':'echo "Error: no test specified" && exit 1','lint':'eslint . --watch','build':'vite build'}}))
            self.assertEqual(detect_checks(root)['commands'],['npm run build'])
            (root/'package.json').write_text(json.dumps({'scripts':{'test':'vitest','build':'vite build'}}))
            self.assertEqual(detect_checks(root)['commands'],['npm run test -- --run','npm run build'])

    def test_pytest_configuration_takes_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'tests').mkdir()
            (root/'pyproject.toml').write_text('[tool.pytest.ini_options]\ntestpaths=["tests"]')
            self.assertEqual(detect_checks(root)['commands'],['python -m pytest -q'])

    def test_zero_discovery_is_not_reported_as_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'tests').mkdir()
            tools=ProjectTools(folder,True)
            original=tools.execute
            def execute(name,args):
                return 'Exit 0\nRan 0 tests\nOK' if name=='run_command' else original(name,args)
            with patch.object(tools,'execute',side_effect=execute):
                self.assertIn('UNVERIFIED',tools.execute('run_checks',{}))

    def test_unity_uses_native_batch_import_and_explicit_project_path(self):
        with tempfile.TemporaryDirectory(prefix='Unity test project ') as folder:
            root=Path(folder).resolve()
            (root/'ProjectSettings').mkdir()
            (root/'ProjectSettings/ProjectVersion.txt').write_text('m_EditorVersion: 6000.3.1f1\n')
            tools=ProjectTools(root,True)
            executable=str(root/'Unity Hub'/'Unity.exe')
            with patch('engine_discovery.find_unity',return_value=executable), \
                 patch.object(tools,'_engine_command',return_value='Exit 0\nImport completed') as invoke:
                self.assertEqual(tools.execute('run_checks',{}),'Exit 0\nImport completed')
                invoke.assert_called_once_with('Unity',executable,
                    ['-batchmode','-nographics','-quit','-projectPath',str(root),'-logFile','-'],600)
            with self.assertRaises(PermissionError):
                ProjectTools(root,False).execute('run_checks',{})
