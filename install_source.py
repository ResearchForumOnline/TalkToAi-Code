"""Copy only public runtime files into a per-user installation."""
import shutil
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
if len(sys.argv)!=2:
    raise SystemExit('Usage: python install_source.py DESTINATION')
target=Path(sys.argv[1]).expanduser().resolve()
if target==ROOT or ROOT in target.parents:
    raise SystemExit('Install destination must be outside the source folder.')
target.mkdir(parents=True,exist_ok=True)
for source in ROOT.glob('*.py'):
    if source.name.startswith('test_') or source.name in {'install_source.py','installer.py','release_checks.py','automation_smoke.py','render_workspace_preview.py'}:
        continue
    shutil.copy2(source,target/source.name)
shutil.copy2(ROOT/'requirements-runtime.txt',target/'requirements-runtime.txt')
for relative in ('project.godot','main.gd','main.gd.uid','main.tscn','tests/score_test.gd'):
    source=ROOT/'samples'/'score-arena'/relative
    destination=target/'samples'/'score-arena'/relative
    destination.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,destination)
print('Copied runtime source to',target)
