"""Exercise project Python through the command paths that desktop agents use."""
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent_core import ProjectTools
from desktop_tools import DesktopTools
from process_jobs import ProcessJobs
import project_python


class ProjectPythonTests(unittest.TestCase):
    def test_off_path_python_creates_project_art_even_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix='TalkToAi project ') as temp:
            root = Path(temp).resolve()
            scripts = root / 'drawing scripts'
            scripts.mkdir()
            (scripts / 'palette.py').write_text("BACKGROUND = '#235781'\n", encoding='utf-8')
            (scripts / 'draw.py').write_text(
                "from pathlib import Path\nfrom palette import BACKGROUND\n"
                "Path('gallery.html').write_text('<body style=background:' + BACKGROUND + '>', "
                "encoding='utf-8')\n", encoding='utf-8')
            tools = ProjectTools(root, act=True)
            original_path = os.environ.get('PATH')
            with patch('project_python._path_executables', return_value=()):
                result = tools.execute('run_command', {'command': "python 'drawing scripts/draw.py'"})
            self.assertEqual(result.strip(), 'Exit 0', result)
            self.assertEqual((root / 'gallery.html').read_text(), '<body style=background:#235781>')
            self.assertEqual(os.environ.get('PATH'), original_path)

    def test_project_virtualenv_beats_system_and_is_kept_for_children(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            venv = root / '.venv'
            subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv)], check=True)
            (root / 'environment.py').write_text(
                "import shutil, subprocess, sys\nfrom pathlib import Path\n"
                "print(Path(sys.prefix).name)\n"
                "subprocess.run([shutil.which('python'), '-c', 'import sys; print(sys.prefix)'], check=True)\n",
                encoding='utf-8')
            jobs = ProcessJobs(root)
            try:
                job = jobs.start('python3', ['environment.py'], timeout_seconds=15)
                while job['state'] == 'running':
                    job = jobs.status(job['id'], wait_seconds=1)
                self.assertEqual(job['exit_code'], 0, job)
                self.assertIn(str(venv), job['output'])
                self.assertIn('.venv', job['output'].splitlines())
            finally:
                jobs.close()

    def test_desktop_command_uses_same_resolver(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            tools = DesktopTools(True, threading.Event(), state=root / 'history')
            tools.root = root
            with patch('project_python._path_executables', return_value=()):
                result = tools.execute('desktop_run_command', {'command': 'python3 --version'})
            self.assertTrue(result.startswith('Exit 0\nPython 3.'), result)
            with self.assertRaises(PermissionError):
                ProjectTools(root, act=False).execute('run_command', {'command': 'python --version'})

    def test_broken_alias_falls_back_to_bundled_cli_on_frozen_windows(self):
        if os.name != 'nt':
            self.skipTest('Windows executable aliases')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            frozen_exe = root / 'TalkToAiCode.exe'
            bundled = root / 'project-python' / 'python.exe'
            bundled.parent.mkdir()
            bundled.touch()
            alias = root / 'WindowsApps' / 'python.exe'
            alias.parent.mkdir()
            alias.touch()
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(frozen_exe)), \
                 patch('project_python._path_executables', return_value=(alias,)):
                self.assertEqual(project_python.project_python(root), bundled)

    def test_missing_virtualenv_interpreter_falls_back_to_working_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            venv = root / '.venv'
            candidate = venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            candidate.parent.mkdir(parents=True)
            (venv / 'pyvenv.cfg').write_text('home = /missing\n')
            candidate.touch()
            with patch('project_python._path_executables', return_value=()):
                self.assertEqual(project_python.project_python(root), Path(sys.executable))

    def test_bundled_pip_and_imports_have_a_separate_target_per_project(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            interpreter = root / 'app' / 'project-python' / 'python.exe'
            with patch('project_python.bundled_python', return_value=interpreter), \
                 patch('project_python.project_python', return_value=interpreter), \
                 patch.dict(os.environ, {'PIP_TARGET': 'user-specified', 'VIRTUAL_ENV': 'old'}):
                for name in ('one', 'two'):
                    project = root / name
                    _, env = project_python.command_environment(project)
                    self.assertEqual(Path(env['PIP_TARGET']), project / '.talktoai-code/python-packages/cp314')
                    self.assertEqual(env['PIP_TARGET'], env['PYTHONPATH'])
                    self.assertNotIn('VIRTUAL_ENV', env)
                    self.assertFalse(Path(env['PIP_TARGET']).exists())
                self.assertEqual(os.environ['PIP_TARGET'], 'user-specified')

    def test_installed_windows_launcher_resolves_concrete_python(self):
        if os.name != 'nt':
            self.skipTest('Windows Python launcher')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            launcher = root / 'py.exe'
            launcher.touch()
            reply = subprocess.CompletedProcess([], 0, stdout=sys.executable.encode('utf-8'))
            with patch('project_python._path_executables', return_value=(launcher,)), \
                 patch('project_python.subprocess.run', return_value=reply) as probe:
                self.assertEqual(project_python.project_python(root), Path(sys.executable))
            args = probe.call_args
            self.assertEqual(args.args[0][:2], [str(launcher), '-3'])
            self.assertEqual(args.kwargs['env']['PYTHON_MANAGER_AUTOMATIC_INSTALL'], 'false')

    def test_build_refuses_unverified_python_before_extracting(self):
        package = runpy.run_path(str(Path(__file__).parent / 'scripts' / 'package-project-python.py'))['package']
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bad = root / 'python.zip'
            bad.write_bytes(b'wrong archive')
            with self.assertRaisesRegex(ValueError, 'pinned release'):
                package(root, bad)
            self.assertFalse((root / 'project-python').exists())


if __name__ == '__main__':
    unittest.main()
