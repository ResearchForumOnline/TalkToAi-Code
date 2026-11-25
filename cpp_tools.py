"""Find and use the C and C++ tools that are already on this computer.

Detection only looks for programs; it runs none of them. Building is done by
the caller through the app's managed process runner, with literal arguments
and no shell, inside the selected project.
"""
import os
from pathlib import Path
import re
import subprocess

from engine_install import find_command


C_SUFFIXES = {'.c'}
CXX_SUFFIXES = {'.cc', '.cpp', '.cxx', '.c++'}
HEADER_SUFFIXES = {'.h', '.hh', '.hpp', '.hxx'}
SOURCE_SUFFIXES = C_SUFFIXES | CXX_SUFFIXES
ALL_SUFFIXES = SOURCE_SUFFIXES | HEADER_SUFFIXES
_STANDARDS = {'c89', 'c90', 'c99', 'c11', 'c17', 'c23', 'gnu11', 'gnu17', 'c++11', 'c++14', 'c++17', 'c++20', 'c++23',
              'gnu++17', 'gnu++20'}


def _visual_studio():
    """Path of an installed MSVC toolset, found without running anything."""
    if os.name != 'nt':
        return None
    roots = [os.environ.get(key) for key in ('ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432')]
    for root in dict.fromkeys(filter(None, roots)):
        base = Path(root) / 'Microsoft Visual Studio'
        for candidate in sorted(base.glob('*/*/VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe'), reverse=True):
            if candidate.is_file():
                return str(candidate)
    return None


def find_toolchain():
    """Which compilers and build tools are installed. Missing entries are None."""
    cc = find_command(('gcc', 'clang', 'cc'))
    cxx = find_command(('g++', 'clang++', 'c++'))
    kind = None
    for path in (cxx, cc):
        if path:
            name = Path(path).name.lower()
            kind = 'clang' if 'clang' in name else 'gcc'
            break
    msvc = _visual_studio()
    return {'cc': cc, 'cxx': cxx, 'kind': kind or ('msvc' if msvc else None), 'msvc': msvc,
            'cmake': find_command(('cmake',)), 'make': find_command(('make', 'gmake', 'mingw32-make')),
            'ninja': find_command(('ninja',)), 'meson': find_command(('meson',))}


def describe_toolchain(chain=None):
    chain = chain or find_toolchain()
    found = [f'{label} {Path(chain[key]).name}' for key, label in (
        ('cc', 'C compiler'), ('cxx', 'C++ compiler'), ('cmake', 'CMake'), ('make', 'Make'), ('ninja', 'Ninja'),
        ('meson', 'Meson')) if chain.get(key)]
    if chain.get('msvc'):
        found.append('Visual Studio C++ (used through CMake)')
    return ', '.join(found) if found else 'no C or C++ build tools found'


def has_sources(root, limit=400):
    """True when the project folder holds C or C++ source near its top."""
    root = Path(root)
    seen = 0
    for folder in (root, root / 'src', root / 'source', root / 'app', root / 'lib'):
        if not folder.is_dir():
            continue
        try:
            for entry in folder.iterdir():
                seen += 1
                if seen > limit:
                    return False
                if entry.suffix.lower() in SOURCE_SUFFIXES and entry.is_file():
                    return True
        except OSError:
            continue
    return False


def detect_native_checks(root):
    """Checks for a C or C++ project, in the shape project_checks.detect_checks returns. None when not one."""
    root = Path(root)
    cmake = root / 'CMakeLists.txt'
    if cmake.is_file():
        commands = ['cmake -S . -B build', 'cmake --build build']
        try:
            text = cmake.read_text(encoding='utf-8', errors='replace')[:200000] if cmake.stat().st_size < 400000 else ''
        except OSError:
            text = ''
        if re.search(r'^\s*(?:enable_testing\s*\(|add_test\s*\(|include\s*\(\s*CTest\s*\))', text, re.I | re.M):
            commands.append('ctest --test-dir build --output-on-failure')
        return {'engine': 'C / C++ (CMake)', 'commands': commands,
                'note': 'Configures and builds in the build folder with the installed compiler. Dependencies are not downloaded.'}
    if (root / 'meson.build').is_file():
        setup = 'meson setup build --reconfigure' if (root / 'build' / 'build.ninja').is_file() else 'meson setup build'
        return {'engine': 'C / C++ (Meson)', 'commands': [setup, 'meson compile -C build', 'meson test -C build'],
                'note': 'Builds in the build folder with the installed compiler.'}
    makefile = next((root / name for name in ('Makefile', 'makefile', 'GNUmakefile') if (root / name).is_file()), None)
    if makefile and has_sources(root) and not any((root / name).exists() for name in ('package.json', 'pyproject.toml')):
        commands = ['make']
        try:
            text = makefile.read_text(encoding='utf-8', errors='replace')[:200000] if makefile.stat().st_size < 400000 else ''
        except OSError:
            text = ''
        target = next((name for name in ('test', 'check') if re.search(r'^' + name + r'\s*:', text, re.M)), None)
        if target:
            commands.append('make ' + target)
        return {'engine': 'C / C++ (Make)', 'commands': commands, 'note': 'Runs the project Makefile with the installed compiler.'}
    if has_sources(root) and not any((root / name).exists() for name in ('package.json', 'pyproject.toml', 'Cargo.toml')):
        return {'engine': 'C / C++', 'commands': [],
                'note': 'No build file found. Use build_native to compile and run a C or C++ source file.'}
    return None


def language(path):
    suffix = Path(path).suffix.lower()
    if suffix in C_SUFFIXES:
        return 'c'
    if suffix in CXX_SUFFIXES or suffix in ('.hh', '.hpp', '.hxx'):
        return 'c++'
    if suffix == '.h':
        folder = Path(path).parent
        try:
            return 'c++' if any(entry.suffix.lower() in CXX_SUFFIXES for entry in folder.iterdir()) else 'c'
        except OSError:
            return 'c'
    raise ValueError('Choose a C or C++ source file (.c, .cc, .cpp, .cxx) or header.')


def _standard(value, kind):
    value = str(value or '').strip().lower()
    if not value:
        return 'c17' if kind == 'c' else 'c++17'
    if value not in _STANDARDS or value.startswith(('c++', 'gnu++')) != (kind == 'c++'):
        raise ValueError('Use a language standard such as c17 for C or c++20 for C++.')
    return value


def compile_command(source, output, chain=None, *, standard='', syntax_only=False):
    """Literal arguments to compile one file with GCC or Clang. Raises when neither is installed."""
    chain = chain or find_toolchain()
    kind = language(source)
    compiler = chain.get('cc') if kind == 'c' else chain.get('cxx')
    if not compiler:
        if chain.get('msvc'):
            raise FileNotFoundError('Only Visual Studio C++ was found. Build this file through a CMakeLists.txt, or install '
                                    'GCC with install_tool cpp for single-file builds.')
        raise FileNotFoundError('No C or C++ compiler was found. Use install_tool with name cpp, then try again.')
    arguments = ['-std=' + _standard(standard, kind), '-Wall', '-Wextra']
    if Path(source).suffix.lower() in HEADER_SUFFIXES:
        arguments += ['-x', 'c++' if kind == 'c++' else 'c']
    if syntax_only:
        return compiler, arguments + ['-fsyntax-only', str(source)]
    libraries = ['-lm'] if kind == 'c' and os.name != 'nt' else []
    return compiler, arguments + ['-O2', '-g', str(source), '-o', str(output)] + libraries


def syntax_check(path, *, timeout=60):
    """Return (state, text): state is 'passed', 'failed' or 'unverified' when no compiler is installed."""
    path = Path(path)
    try:
        compiler, arguments = compile_command(path, None, syntax_only=True)
    except FileNotFoundError as exc:
        return 'unverified', f'{path.name}: C/C++ syntax not checked. {exc}'
    try:
        result = subprocess.run([compiler, *arguments], cwd=path.parent, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 'unverified', f'{path.name}: the compiler could not check this file ({type(exc).__name__}).'
    output = result.stdout.decode('utf-8', errors='replace').strip()
    if result.returncode:
        return 'failed', f'{path.name}: {Path(compiler).name} reported errors.\n' + output[-3000:]
    warnings = len(re.findall(r'\bwarning:', output))
    return 'passed', (f'{path.name}: C/C++ syntax checked with {Path(compiler).name}'
                      + (f' ({warnings} warning' + ('s' if warnings != 1 else '') + ')' if warnings else '')
                      + '. The program was not built or run.')


def project_build_commands(root, chain=None):
    """(label, executable, arguments) steps that build a CMake, Meson or Make project without a shell."""
    root = Path(root)
    chain = chain or find_toolchain()
    if (root / 'CMakeLists.txt').is_file():
        if not chain.get('cmake'):
            raise FileNotFoundError('CMake is not installed. Use install_tool with name cmake, then try again.')
        if not (chain.get('cxx') or chain.get('cc') or chain.get('msvc')):
            raise FileNotFoundError('No C or C++ compiler was found. Use install_tool with name cpp, then try again.')
        configure = ['-S', '.', '-B', 'build']
        if chain.get('ninja') and not (root / 'build' / 'CMakeCache.txt').is_file():
            configure += ['-G', 'Ninja']
        steps = [('Configure', chain['cmake'], configure), ('Build', chain['cmake'], ['--build', 'build'])]
        return steps
    if (root / 'meson.build').is_file():
        if not chain.get('meson'):
            raise FileNotFoundError('Meson is not installed. Install it with: python -m pip install meson ninja')
        setup = ['setup', 'build'] + (['--reconfigure'] if (root / 'build' / 'build.ninja').is_file() else [])
        return [('Configure', chain['meson'], setup), ('Build', chain['meson'], ['compile', '-C', 'build'])]
    if any((root / name).is_file() for name in ('Makefile', 'makefile', 'GNUmakefile')):
        if not chain.get('make'):
            raise FileNotFoundError('Make is not installed. Install build tools, or add a CMakeLists.txt.')
        return [('Build', chain['make'], [])]
    raise ValueError('No CMakeLists.txt, meson.build or Makefile here. Give build_native the path of a source file.')
