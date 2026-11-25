"""Install a short list of well-known free tools with the system package manager.

Only fixed package identifiers are used, through Windows Package Manager,
Homebrew, Flatpak (Flathub) or Snap. This module never downloads from an
arbitrary URL, never unpacks or runs an archive, and never asks for an
administrator password itself: if the operating system needs approval it shows
its own prompt. Every attempt is reported to the caller and written to
``install-log.jsonl`` in the app's data folder.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time


@dataclass(frozen=True)
class Tool:
    key: str
    label: str
    winget: str = ''
    brew: str = ''
    cask: str = ''
    flatpak: str = ''
    snap: tuple = ()
    commands: tuple = ()        # names to look for on PATH
    linux_packages: str = ''    # what a user would install with their distribution's manager
    note: str = ''


TOOLS = {tool.key: tool for tool in (
    Tool('blender', 'Blender', winget='BlenderFoundation.Blender', cask='blender', flatpak='org.blender.Blender',
         snap=('blender', '--classic'), commands=('blender',), linux_packages='blender'),
    Tool('godot', 'Godot', winget='GodotEngine.GodotEngine', cask='godot', flatpak='org.godotengine.Godot',
         commands=('godot', 'godot4'), linux_packages='godot'),
    Tool('git', 'Git', winget='Git.Git', brew='git', commands=('git',), linux_packages='git'),
    Tool('cmake', 'CMake', winget='Kitware.CMake', brew='cmake', snap=('cmake', '--classic'), commands=('cmake',),
         linux_packages='cmake'),
    Tool('ninja', 'Ninja', winget='Ninja-build.Ninja', brew='ninja', commands=('ninja',), linux_packages='ninja-build'),
    Tool('cpp', 'C and C++ compiler', winget='BrechtSanders.WinLibs.POSIX.UCRT', commands=('g++', 'clang++', 'c++'),
         linux_packages='build-essential (Debian/Ubuntu), gcc-c++ make (Fedora) or base-devel (Arch)',
         note='On macOS the compiler comes with Apple\'s Command Line Tools: run xcode-select --install.'),
    Tool('node', 'Node.js', winget='OpenJS.NodeJS.LTS', brew='node', snap=('node', '--classic'), commands=('node',),
         linux_packages='nodejs npm'),
    Tool('python', 'Python 3', winget='Python.Python.3.12', brew='python@3.12', commands=('python3', 'python'),
         linux_packages='python3 python3-venv python3-pip'),
    Tool('ffmpeg', 'FFmpeg', winget='Gyan.FFmpeg', brew='ffmpeg', snap=('ffmpeg',), commands=('ffmpeg',),
         linux_packages='ffmpeg'),
    Tool('rust', 'Rust', winget='Rustlang.Rustup', brew='rustup', commands=('cargo',), linux_packages='rustup'),
)}
ALIASES = {'gcc': 'cpp', 'g++': 'cpp', 'clang': 'cpp', 'c++': 'cpp', 'c': 'cpp', 'compiler': 'cpp', 'mingw': 'cpp',
           'nodejs': 'node', 'npm': 'node', 'python3': 'python', 'cargo': 'rust', 'rustup': 'rust', 'blender3d': 'blender',
           'godot4': 'godot'}
_INSTALL_SECONDS = 1800
_install_lock = threading.Lock()


def resolve(name):
    key = str(name or '').strip().lower()
    key = ALIASES.get(key, key)
    if key not in TOOLS:
        raise ValueError('Unknown tool. Choose one of: ' + ', '.join(sorted(TOOLS)) + '.')
    return TOOLS[key]


def _winget():
    # The per-user App Installer alias works even when the app inherited a stale PATH.
    local = os.environ.get('LOCALAPPDATA')
    if local:
        alias = Path(local) / 'Microsoft' / 'WindowsApps' / 'winget.exe'
        if alias.is_file():
            return str(alias)
    return None


def _brew():
    found = shutil.which('brew')
    if found:
        return found
    return next((str(path) for path in (Path('/opt/homebrew/bin/brew'), Path('/usr/local/bin/brew'),
                                        Path('/home/linuxbrew/.linuxbrew/bin/brew')) if path.is_file()), None)


def _windows_search_path():
    """PATH plus folders an installer may have added while the app was open."""
    folders = [entry.strip('"') for entry in os.environ.get('PATH', '').split(os.pathsep) if entry.strip('"')]
    local = os.environ.get('LOCALAPPDATA')
    if local:
        folders.append(str(Path(local) / 'Microsoft' / 'WinGet' / 'Links'))
        # A ZIP package such as WinLibs (GCC) unpacks here; its compilers are in mingw64\bin.
        packages = Path(local) / 'Microsoft' / 'WinGet' / 'Packages'
        try:
            folders += [str(folder) for folder in sorted(packages.glob('BrechtSanders.WinLibs*/mingw64/bin'))]
        except OSError:
            pass
    try:
        import winreg
        for root, key in ((winreg.HKEY_CURRENT_USER, 'Environment'),
                          (winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment')):
            try:
                with winreg.OpenKey(root, key) as handle:
                    value, _ = winreg.QueryValueEx(handle, 'Path')
                folders += [part.strip('"') for part in winreg.ExpandEnvironmentStrings(value).split(os.pathsep) if part.strip('"')]
            except OSError:
                continue
    except ImportError:
        pass
    return os.pathsep.join(dict.fromkeys(folders))


def find_command(names):
    """Locate a program, also after an installer changed the account PATH."""
    path = _windows_search_path() if os.name == 'nt' else None
    for name in names:
        found = shutil.which(name, path=path) if path else shutil.which(name)
        if found:
            return found
    return None


def tool_path(tool, project=None):
    """Return where an installed tool is, or None."""
    tool = tool if isinstance(tool, Tool) else resolve(tool)
    if tool.key == 'blender':
        from engine_discovery import discover_blender
        return discover_blender()
    if tool.key == 'godot':
        from engine_discovery import find_godot
        try:
            return find_godot(project or Path.cwd(), Path(__file__).resolve().parent)
        except ValueError:
            return None
    if tool.key == 'cpp':
        from cpp_tools import find_toolchain
        chain = find_toolchain()
        return chain.get('cxx') or chain.get('cc')
    return find_command(tool.commands)


def install_plan(tool):
    """The commands this computer would try, in order. Empty when none applies."""
    tool = tool if isinstance(tool, Tool) else resolve(tool)
    plan = []
    if sys.platform == 'win32':
        winget = _winget()
        if winget and tool.winget:
            plan.append(('Windows Package Manager', [winget, 'install', '--id', tool.winget, '--exact', '--source', 'winget',
                                                     '--silent', '--accept-source-agreements', '--disable-interactivity']))
    elif sys.platform == 'darwin':
        brew = _brew()
        if brew and tool.cask:
            plan.append(('Homebrew', [brew, 'install', '--cask', tool.cask]))
        elif brew and tool.brew:
            plan.append(('Homebrew', [brew, 'install', tool.brew]))
    else:
        flatpak = shutil.which('flatpak')
        if flatpak and tool.flatpak:
            plan.append(('Flatpak', [flatpak, 'install', '--user', '--noninteractive', '-y', 'flathub', tool.flatpak]))
        snap = shutil.which('snap')
        if snap and tool.snap:
            plan.append(('Snap', [snap, 'install', *tool.snap]))
        brew = _brew()
        if brew and tool.brew:
            plan.append(('Homebrew', [brew, 'install', tool.brew]))
    return plan


def manual_steps(tool):
    """What to tell the user when nothing here can install the tool."""
    tool = tool if isinstance(tool, Tool) else resolve(tool)
    if sys.platform == 'win32':
        return (f'Install it with: winget install --id {tool.winget} --exact' if tool.winget
                else f'Install {tool.label} from its official website.')
    if sys.platform == 'darwin':
        if tool.key == 'cpp':
            return 'Run this in Terminal and accept Apple\'s prompt: xcode-select --install'
        name = ('--cask ' + tool.cask) if tool.cask else tool.brew
        return f'Install Homebrew from https://brew.sh, then run: brew install {name}'
    return (f'Install it with your distribution\'s package manager, for example: sudo apt install '
            f'{tool.linux_packages.split(" (")[0]}' if tool.linux_packages else f'Install {tool.label} from its official website.')


def _log(record):
    try:
        from platform_paths import state_dir
        from updates import is_store_package
        folder = state_dir(is_store_package())
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / 'install-log.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    except OSError:
        pass


def _run(label, command, tool, cancel, notify):
    """Run one package-manager command. Returns (exit code or None when stopped, tail of its output)."""
    notify(f'Installing {tool.label} with {label}…')
    started = time.monotonic()
    with tempfile.TemporaryFile() as output:
        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as exc:
            return 127, str(exc)
        next_update = 10
        try:
            while process.poll() is None:
                if cancel.wait(0.5):
                    return None, 'Stopped.'
                elapsed = int(time.monotonic() - started)
                if elapsed >= _INSTALL_SECONDS:
                    return 124, f'{label} was still working after {_INSTALL_SECONDS // 60} minutes.'
                if elapsed >= next_update:
                    notify(f'{label} is downloading or installing {tool.label} · {elapsed // 60}m {elapsed % 60:02d}s')
                    next_update = elapsed + 10
        finally:
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
        output.seek(0, 2)
        size = output.tell()
        output.seek(max(0, size - 1500))
        return process.returncode, output.read().decode('utf-8', errors='replace').strip()


def ensure_tool(name, cancel=None, notify=None, *, project=None):
    """Find a tool, installing it when a package manager on this computer can.

    Returns a dict: tool, label, installed (bool), path, action
    ('already', 'installed', 'failed', 'unavailable' or 'stopped') and detail.
    It does not raise for a tool that cannot be installed.
    """
    tool = resolve(name)
    cancel = cancel if cancel is not None else threading.Event()
    notify = notify or (lambda text: None)
    result = {'tool': tool.key, 'label': tool.label, 'installed': False, 'path': None, 'action': 'unavailable', 'detail': ''}
    path = tool_path(tool, project)
    if path:
        return dict(result, installed=True, path=path, action='already', detail=f'{tool.label} is already installed.')
    plan = install_plan(tool)
    if not plan:
        detail = f'{tool.label} is not installed and no package manager on this computer can add it automatically. '
        return dict(result, detail=detail + manual_steps(tool) + (' ' + tool.note if tool.note else ''))
    while not _install_lock.acquire(timeout=0.25):
        if cancel.is_set():
            return dict(result, action='stopped', detail='Stopped before the install started.')
    try:
        path = tool_path(tool, project)  # another task may have installed it while this one waited
        if path:
            return dict(result, installed=True, path=path, action='already', detail=f'{tool.label} is already installed.')
        problems = []
        for label, command in plan:
            code, tail = _run(label, command, tool, cancel, notify)
            _log({'time': datetime.now(timezone.utc).isoformat(), 'tool': tool.key, 'manager': label,
                  'command': [Path(command[0]).name, *command[1:]], 'exit_code': code})
            if code is None:
                return dict(result, action='stopped', detail=f'The {tool.label} install was stopped.')
            path = tool_path(tool, project)
            if path:
                notify(f'{tool.label} is ready.')
                return dict(result, installed=True, path=path, action='installed',
                            detail=f'{tool.label} was installed with {label}.')
            if code == 0:
                problems.append(f'{label} reported success, but {tool.label} was not found afterwards. '
                                'It may need a new terminal or sign-in before it is on PATH.')
            else:
                shown = (code & 0xffffffff) if os.name == 'nt' else code
                problems.append(f'{label} could not install it (exit {shown:#x}).' if os.name == 'nt' else
                                f'{label} could not install it (exit {shown}).')
                if tail:
                    problems.append(tail.splitlines()[-1][:300])
        notify(f'{tool.label} could not be installed automatically.')
        return dict(result, action='failed', detail=' '.join(problems) + ' ' + manual_steps(tool))
    finally:
        _install_lock.release()


def ensure_engine(name, cancel, notify):
    """Path to an engine after trying to install it, or None. Used when a task needs it to continue."""
    outcome = ensure_tool(name, cancel, notify)
    if not outcome['installed'] and outcome['action'] != 'stopped':
        notify(outcome['detail'])
    return outcome['path']
