import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from project_checks import check_edited_files, detect_checks
from agent_core import ProjectTools


class CheckDetectionTests(unittest.TestCase):
    def test_standalone_checks_catch_unclosed_scripts_and_python_syntax(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            page=root/'art.html'
            page.write_text('<html><title>Test</title><script>unfinished',encoding='utf-8')
            self.assertIn('Exit 1',check_edited_files([page]))
            page.write_text('<html><body><canvas></canvas></body></html>',encoding='utf-8')
            script=root/'art.py'
            script.write_text('if True print(1)',encoding='utf-8')
            self.assertIn('Exit 1',check_edited_files([page,script]))
            script.write_text("raise RuntimeError('never run')",encoding='utf-8')
            result=check_edited_files([page,script])
            self.assertTrue(result.startswith('Exit 0'))
            self.assertIn('HTML structure checked',result)
            self.assertIn('Python syntax checked',result)
            self.assertIsNone(check_edited_files([page,root/'unknown.game']))
            svg=root/'orbit.svg'
            svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><circle r="5"/></svg>',encoding='utf-8')
            self.assertIn('SVG XML and root element checked',check_edited_files([svg]))
            svg.write_text('<picture/>',encoding='utf-8')
            self.assertIn('Exit 1',check_edited_files([svg]))
            config=root/'project.json'
            config.write_text('{"ok":true}',encoding='utf-8')
            self.assertIn('JSON parsed successfully',check_edited_files([config]))
            config.write_text('{"ok":',encoding='utf-8')
            self.assertIn('Exit 1',check_edited_files([config]))

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
