"""Resolve a real Python 3 for project tools without changing the user's environment."""
from functools import lru_cache
import os
from pathlib import Path
import subprocess
import sys
import time


def bundled_python():
    if os.name == 'nt' and getattr(sys, 'frozen', False):
        return Path(sys.executable).parent / 'project-python' / 'python.exe'
    return None


@lru_cache(maxsize=32)
def _probe(candidate, launcher, modified, time_bucket):
    # A Windows Store shortcut can exist on PATH without an installed interpreter.
    # This argument-based probe also resolves a working Store alias or py launcher.
    env = os.environ.copy()
    env['PYTHON_MANAGER_AUTOMATIC_INSTALL'] = 'false'
    for key in ('PYLAUNCHER_ALLOW_INSTALL', 'PYLAUNCHER_ALWAYS_INSTALL'):
        env.pop(key, None)
    args = [str(candidate)] + (['-3'] if launcher else [])
    args += ['-I', '-S', '-c', "import sys; sys.stdout.buffer.write(sys.executable.encode('utf-8'))"]
    try:
        result = subprocess.run(args, cwd=candidate.parent, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        path = Path(result.stdout.decode('utf-8'))
        if result.returncode == 0 and path.is_absolute() and path.is_file():
            return path
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def _path_executables():
    folders = []
    for entry in os.get_exec_path():
        path = Path(entry.strip('"'))
        if path.is_absolute():
            folders.append(path)
    names = ('python.exe', 'python3.exe', 'py.exe') if os.name == 'nt' else ('python3', 'python')
    for name in names:
        for folder in folders:
            yield folder / name


def _installed_python(candidate):
    try:
        modified = candidate.stat().st_mtime_ns
    except OSError:
        return None
    found = _probe(candidate, candidate.name.lower() == 'py.exe', modified, int(time.monotonic() // 60))
    return found if found and found.is_file() else None


def project_python(project):
    project = Path(project).absolute()
    relative = 'Scripts/python.exe' if os.name == 'nt' else 'bin/python'
    for name in ('.venv', 'venv'):
        venv = project / name
        candidate = venv / relative
        if (venv / 'pyvenv.cfg').is_file() and _installed_python(candidate):
            # Do not resolve the symlink: its location selects this project's packages.
            return candidate
    for candidate in dict.fromkeys(_path_executables()):
        found = _installed_python(candidate)
        if found:
            return found
    if not getattr(sys, 'frozen', False) and Path(sys.executable).is_file():
        return Path(sys.executable)
    bundled = bundled_python()
    return bundled if bundled and bundled.is_file() else None


def command_environment(project):
    env = os.environ.copy()
    interpreter = project_python(project)
    if interpreter:
        # Child processes (including a script launching another script) inherit this
        # PATH. The GUI and other programs on the computer keep their original PATH.
        env['PATH'] = str(interpreter.parent) + os.pathsep + env.get('PATH', '')
        env['TALKTOAI_PROJECT_PYTHON'] = str(interpreter)
        env['PYTHONIOENCODING'] = 'utf-8'
        if (interpreter.parent.parent / 'pyvenv.cfg').is_file():
            env['VIRTUAL_ENV'] = str(interpreter.parent.parent)
            env.pop('PYTHONHOME', None)
        bundled = bundled_python()
        if bundled and interpreter.parent == bundled.parent:
            # A Store payload is read-only. Scope imports and pip installs to this
            # project and CPython ABI. Outside tools and installed Python stay intact.
            root = Path(project).resolve()
            packages = root / '.talktoai-code' / 'python-packages' / 'cp314'
            if not packages.resolve().is_relative_to(root):
                raise ValueError('The project Python packages folder redirects outside the selected project.')
            env.pop('PYTHONHOME', None)
            env.pop('VIRTUAL_ENV', None)
            env['PYTHONNOUSERSITE'] = '1'
            env['PYTHONPATH'] = str(packages)
            env['PIP_TARGET'] = str(packages)
            env.setdefault('PIP_DISABLE_PIP_VERSION_CHECK', '1')
    return interpreter, env


def project_shell(command, project):
    interpreter, env = command_environment(project)
    if os.name == 'nt':
        header = ''
        if interpreter:
            # The python.org installer and many project venvs supply only python.exe.
            if not (interpreter.parent / 'python3.exe').is_file():
                header = "Set-Alias -Name python3 -Value $env:TALKTOAI_PROJECT_PYTHON\n"
            if not (interpreter.parent / 'python.exe').is_file():
                header += "Set-Alias -Name python -Value $env:TALKTOAI_PROJECT_PYTHON\n"
        return ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', header + command], env
    # Login profiles may replace PATH. Reapply the selected directory in the actual
    # shell, then supply the common name on platforms that ship only python3.
    header = ''
    if interpreter:
        header = 'PATH="${TALKTOAI_PROJECT_PYTHON%/*}:$PATH"; export PATH\n'
        if not (interpreter.parent / 'python').is_file():
            header += 'python() { "$TALKTOAI_PROJECT_PYTHON" "$@"; }\n'
        if not (interpreter.parent / 'python3').is_file():
            header += 'python3() { "$TALKTOAI_PROJECT_PYTHON" "$@"; }\n'
    return ['/bin/sh', '-lc', header + command], env
