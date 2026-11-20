"""Resolve a user-selected engine without executing discovery candidates."""
import json
import os
import re
import shutil
import sys
from pathlib import Path
from platform_paths import state_dir
from updates import is_store_package


def configured_engine(engine):
    explicit = os.environ.get('TALKTOAI_' + engine.upper(), '').strip()
    if not explicit:
        try:
            settings = json.loads((state_dir(is_store_package()) / 'config.json').read_text(encoding='utf-8'))
            explicit = str(settings.get(engine + '_executable', '') or '').strip()
        except (OSError, ValueError, TypeError):
            pass
    if not explicit:
        return None
    path = Path(explicit).expanduser()
    if not path.is_file():
        raise ValueError(f'The configured {engine.title()} executable does not exist. Choose its executable in Settings or correct TALKTOAI_{engine.upper()}.')
    return str(path.resolve())


def configured_godot():
    return configured_engine('godot')


def find_godot(root, home):
    explicit = configured_godot()
    if explicit:
        return explicit
    on_path = shutil.which('godot') or shutil.which('godot4')
    if on_path:
        return on_path
    # Preserve earlier app-local toolchain compatibility.
    for path in (Path(root) / 'tools/godot-4.7.2/Godot_v4.7.2-stable_win64.exe',
                 Path(home).parent / 'ouroboros/tools/godot-4.7.2/Godot_v4.7.2-stable_win64.exe'):
        if path.is_file():
            return str(path)
    if os.name == 'nt':
        folders = [Path(os.environ[key]) / (name + 'Godot') for key, name in (
            ('LOCALAPPDATA', 'Programs/'), ('ProgramW6432', ''), ('ProgramFiles', ''),
            ('ProgramFiles(x86)', '')) if os.environ.get(key)]
        for folder in folders:
            for path in (folder / 'godot.exe', folder / 'godot4.exe', *sorted(folder.glob('Godot_v*-stable_win*.exe'), reverse=True)):
                if not path.stem.endswith('_console') and path.is_file():
                    return str(path)
    elif sys.platform == 'darwin':
        for folder in (Path('/Applications'), Path.home() / 'Applications'):
            path = folder / 'Godot.app/Contents/MacOS/Godot'
            if path.is_file():
                return str(path)
    return None


def find_unity(root):
    explicit = configured_engine('unity')
    if explicit:
        return explicit
    marker = Path(root) / 'ProjectSettings/ProjectVersion.txt'
    try:
        if marker.stat().st_size > 8192:
            return None
        content = marker.read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError):
        return None
    version = re.search(r'^m_EditorVersion:\s*(\d+\.\d+\.\d+[A-Za-z0-9]+)\s*$', content, re.M)
    if version is None:
        return None
    if os.name == 'nt':
        roots = [Path(os.environ[key]) / 'Unity/Hub/Editor' for key in ('ProgramW6432', 'ProgramFiles', 'ProgramFiles(x86)')
                 if os.environ.get(key)]
        executable = 'Editor/Unity.exe'
    elif sys.platform == 'darwin':
        roots = [Path('/Applications/Unity/Hub/Editor'), Path.home() / 'Applications/Unity/Hub/Editor']
        executable = 'Unity.app/Contents/MacOS/Unity'
    else:
        roots = [Path.home() / 'Unity/Hub/Editor', Path('/opt/unity/Hub/Editor')]
        executable = 'Editor/Unity'
    for base in roots:
        path = base / version.group(1) / executable
        if path.is_file():
            return str(path)
    return None


def find_blender():
    explicit = configured_engine('blender')
    if explicit:
        return explicit
    on_path = shutil.which('blender')
    if on_path:
        return on_path
    if os.name == 'nt':
        candidates = []
        for key in ('ProgramW6432', 'ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
            location = os.environ.get(key)
            if not location:
                continue
            root = Path(location)
            if key == 'LOCALAPPDATA':
                root /= 'Programs'
            candidates.extend((root / 'Blender Foundation').glob('Blender*/blender.exe'))
            candidates.append(root / 'Blender' / 'blender.exe')
        candidates.sort(key=lambda path: tuple(map(int, re.findall(r'\d+', path.parent.name))), reverse=True)
    elif sys.platform == 'darwin':
        candidates = [Path('/Applications/Blender.app/Contents/MacOS/Blender'),
                      Path.home() / 'Applications/Blender.app/Contents/MacOS/Blender']
    else:
        candidates = [Path('/snap/bin/blender'), Path('/opt/blender/blender')]
    return next((str(path) for path in candidates if path.is_file()), None)
