"""Copy a bundled example into persistent user storage without overwriting work."""
from pathlib import Path
import shutil
import tempfile

SCORE_ARENA_FILES = ('project.godot', 'main.gd', 'main.gd.uid', 'main.tscn', 'tests/score_test.gd')


def ensure_score_arena(source_root, projects_root):
    source=Path(source_root)/'samples'/'score-arena'
    projects=Path(projects_root)
    target=projects/'score-arena'
    if target.exists():
        if (target/'project.godot').is_file():
            return target
        raise ValueError('The example folder already exists but has no project.godot. Move it aside yourself or open a different project; nothing was overwritten.')
    if not all((source/name).is_file() for name in SCORE_ARENA_FILES):
        raise FileNotFoundError('The bundled Score Arena example is incomplete. Reinstall the latest app.')
    projects.mkdir(parents=True,exist_ok=True)
    # Only copy the explicit public source files, never caches or local settings.
    with tempfile.TemporaryDirectory(prefix='score-arena-setup-',dir=projects) as folder:
        stage=Path(folder)/'project';stage.mkdir()
        for name in SCORE_ARENA_FILES:
            destination=stage/name;destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source/name,destination)
        stage.rename(target)
    return target
