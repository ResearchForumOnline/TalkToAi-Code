"""C and C++ builds, scripts and automatic tool installs. Installs are simulated; nothing is installed."""
import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_core as core
import cpp_tools
import engine_install
from project_checks import check_edited_files, detect_checks

HAVE_GCC = bool(shutil.which('gcc') and shutil.which('g++'))


class NativeDetectionTests(unittest.TestCase):
    def test_build_files_decide_the_project_kind(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / 'tests').mkdir()
            (root / 'tests' / 'data.txt').write_text('x', encoding='utf-8')
            (root / 'main.cpp').write_text('int main(){}\n', encoding='utf-8')
            self.assertEqual(detect_checks(root)['engine'], 'C / C++')
            (root / 'Makefile').write_text('all:\n\tc++ main.cpp\ntest:\n\t./a.out\n', encoding='utf-8')
            self.assertEqual(detect_checks(root), {'engine': 'C / C++ (Make)', 'commands': ['make', 'make test'],
                                                   'note': 'Runs the project Makefile with the installed compiler.'})
            (root / 'CMakeLists.txt').write_text('project(x CXX)\nenable_testing()\n', encoding='utf-8')
            self.assertEqual(detect_checks(root)['commands'][-1], 'ctest --test-dir build --output-on-failure')
            (root / 'package.json').write_text('{"scripts": {}}', encoding='utf-8')
            self.assertEqual(detect_checks(root)['engine'], 'JavaScript / TypeScript')

    def test_compile_arguments(self):
        chain = {'cc': '/usr/bin/gcc', 'cxx': '/usr/bin/g++', 'msvc': None}
        self.assertEqual(cpp_tools.compile_command(Path('a.c'), Path('build/a'), chain)[1][:3], ['-std=c17', '-Wall', '-Wextra'])
        compiler, arguments = cpp_tools.compile_command(Path('a.cpp'), Path('build/a'), chain, standard='c++20')
        self.assertEqual((compiler, arguments[0]), ('/usr/bin/g++', '-std=c++20'))
        self.assertIn('-fsyntax-only', cpp_tools.compile_command(Path('a.hpp'), None, chain, syntax_only=True)[1])
        with self.assertRaisesRegex(FileNotFoundError, 'install_tool with name cpp'):
            cpp_tools.compile_command(Path('a.c'), Path('a'), {'cc': None, 'cxx': None, 'msvc': None})

    @unittest.skipUnless(HAVE_GCC, 'GCC is not installed here')
    def test_edited_c_and_cpp_files_are_compiled_for_syntax_only(self):
        with tempfile.TemporaryDirectory() as root:
            good, bad = Path(root, 'good.c'), Path(root, 'bad.cpp')
            good.write_text('#include <stdio.h>\nint main(void){puts("ok");return 0;}\n', encoding='utf-8')
            bad.write_text('int main(){ return missing; }\n', encoding='utf-8')
            self.assertTrue(check_edited_files([good]).startswith('Exit 0\nStandalone file checks\ngood.c: C/C++ syntax checked'))
            self.assertIn('missing', check_edited_files([bad]))
            self.assertTrue(check_edited_files([bad]).startswith('Exit 1\n'))
            self.assertEqual(sorted(path.name for path in Path(root).iterdir()), ['bad.cpp', 'good.c'])
        with tempfile.TemporaryDirectory() as root, patch.object(cpp_tools, 'find_toolchain',
                                                                 return_value={'cc': None, 'cxx': None, 'msvc': None}):
            source = Path(root, 'x.c')
            source.write_text('int main(void){return 0;}\n', encoding='utf-8')
            result = check_edited_files([source])
            self.assertIn('UNVERIFIED: x.c: C/C++ syntax not checked.', result)
            self.assertEqual(core.check_evidence(result)['status'], 'unverified')


@unittest.skipUnless(HAVE_GCC, 'GCC is not installed here')
class NativeToolTests(unittest.TestCase):
    def tools(self, root):
        tools = core.ProjectTools(root, True)
        tools.effort = core.effort_from({'effort': 'high'})
        return tools

    def test_build_native_compiles_runs_and_reports_compiler_errors(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'hello.c').write_text('#include <stdio.h>\nint main(int c,char**v){printf("hi %d\\n",c);return 0;}\n')
            Path(root, 'bad.cpp').write_text('int main(){ return x; }\n')
            tools = self.tools(root)
            ran = tools.execute('build_native', {'path': 'hello.c', 'run': 'true', 'arguments': '["one", "two"]'})
            self.assertIn('Run: hello', ran)
            self.assertIn('hi 3', ran)
            self.assertTrue(Path(root, 'build', 'hello').is_file() or Path(root, 'build', 'hello.exe').is_file())
            failed = tools.execute('build_native', {'path': 'bad.cpp'})
            self.assertIn('Exit 1', failed)
            self.assertIn("'x' was not declared", failed.replace('‘', "'").replace('’', "'"))
            with self.assertRaisesRegex(ValueError, r'existing project C or C\+\+ source'):
                tools.execute('build_native', {'path': 'absent.c'})
            with self.assertRaises(ValueError):
                tools.execute('build_native', {'path': '../outside.c'})

    def test_run_script_runs_python_and_shell_then_removes_the_script(self):
        with tempfile.TemporaryDirectory() as root:
            tools = self.tools(root)
            self.assertEqual(tools.execute('run_script', {'language': 'python', 'code': 'print(6 * 7)'}), 'Exit 0\n42\n')
            if sys.platform != 'win32':
                self.assertTrue(tools.execute('run_script', {'language': 'bash', 'code': 'echo $((2 + 3))'}).startswith('Exit 0\n5'))
            self.assertEqual(list(Path(root, '.talktoai-code', 'scripts').iterdir()), [])
            with self.assertRaisesRegex(ValueError, 'timeout_seconds'):
                tools.execute('run_script', {'language': 'python', 'code': 'print(1)', 'timeout_seconds': 4000})


class InstallTests(unittest.TestCase):
    def test_install_plans_use_fixed_package_ids(self):
        blender = engine_install.resolve('blender')
        with patch.object(engine_install.sys, 'platform', 'win32'), patch.object(engine_install, '_winget', return_value='winget.exe'):
            plan = engine_install.install_plan(blender)
        self.assertEqual(plan, [('Windows Package Manager', ['winget.exe', 'install', '--id', 'BlenderFoundation.Blender', '--exact',
                                                             '--source', 'winget', '--silent', '--accept-source-agreements',
                                                             '--disable-interactivity'])])
        with patch.object(engine_install.sys, 'platform', 'darwin'), patch.object(engine_install, '_brew', return_value='brew'):
            self.assertEqual(engine_install.install_plan(blender), [('Homebrew', ['brew', 'install', '--cask', 'blender'])])
        with patch.object(engine_install.sys, 'platform', 'linux'), patch.object(engine_install, '_brew', return_value=None), \
                patch.object(engine_install.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name if name == 'flatpak' else None):
            self.assertEqual(engine_install.install_plan(blender)[0][1][-1], 'org.blender.Blender')
        self.assertEqual(engine_install.resolve('g++').key, 'cpp')
        with self.assertRaises(ValueError):
            engine_install.resolve('anything; rm -rf /')

    def outcome(self, *, found_after, codes, plan=(('Manager', ['manager', 'install', 'x']),), before=None):
        found = iter([before] + list(found_after))
        notices = []
        with patch.object(engine_install, 'tool_path', side_effect=lambda *a: next(found)), \
                patch.object(engine_install, 'install_plan', return_value=list(plan)), \
                patch.object(engine_install, '_run', side_effect=[(code, 'last line of output') for code in codes]) as run, \
                patch.object(engine_install, '_log'):
            result = engine_install.ensure_tool('blender', threading.Event(), notices.append)
        return result, run, notices

    def test_ensure_tool_outcomes(self):
        result, run, _ = self.outcome(found_after=[], codes=[], before='/opt/blender')
        self.assertEqual((result['action'], result['path']), ('already', '/opt/blender'))
        run.assert_not_called()
        result, _, notices = self.outcome(found_after=[None, '/opt/blender'], codes=[0])
        self.assertEqual((result['action'], result['installed']), ('installed', True))
        self.assertIn('Blender is ready.', notices)
        result, _, _ = self.outcome(found_after=[None, None], codes=[1])
        self.assertEqual(result['action'], 'failed')
        self.assertIn('last line of output', result['detail'])
        result, _, _ = self.outcome(found_after=[None], codes=[None])
        self.assertEqual(result['action'], 'stopped')
        result, _, _ = self.outcome(found_after=[], codes=[], plan=())
        self.assertEqual(result['action'], 'unavailable')
        self.assertIn('Install', result['detail'])

    def test_the_agent_installs_only_when_the_setting_allows_it(self):
        with tempfile.TemporaryDirectory() as root:
            tools = core.ProjectTools(root, True)
            with self.assertRaisesRegex(PermissionError, 'Install missing tools automatically'):
                tools.execute('install_tool', {'name': 'cmake'})
            tools.auto_install = True
            with patch('engine_install.ensure_tool', return_value={'tool': 'cmake', 'installed': True, 'action': 'installed'}) as ensure:
                self.assertEqual(json.loads(tools.execute('install_tool', {'name': 'cmake'}))['action'], 'installed')
            self.assertEqual(ensure.call_args.args[0], 'cmake')


if __name__ == '__main__':
    unittest.main()
