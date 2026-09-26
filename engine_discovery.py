"""Resolve a user-selected engine without executing discovery candidates."""
import json
import os
import shutil
from pathlib import Path
from platform_paths import state_dir
from updates import is_store_package


def configured_godot():
    explicit = os.environ.get('TALKTOAI_GODOT', '').strip()
    if not explicit:
        try:
            settings = json.loads((state_dir(is_store_package()) / 'config.json').read_text(encoding='utf-8'))
            explicit = str(settings.get('godot_executable', '')).strip()
        except (OSError, ValueError, TypeError):
            pass
    if not explicit:
        return None
    path = Path(explicit).expanduser()
    if not path.is_file():
        raise ValueError('The configured Godot executable does not exist. Choose its executable in Settings or correct TALKTOAI_GODOT.')
    return str(path.resolve())


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
    return None
