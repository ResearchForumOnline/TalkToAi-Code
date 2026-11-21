#!/usr/bin/env python3
"""Build a reproducible per-user installer kit for macOS or Linux."""

import argparse
import gzip
import io
from pathlib import Path
import re
import runpy
import tarfile
from tempfile import NamedTemporaryFile


ROOT = Path(__file__).resolve().parent.parent
EXCLUDE = {'installer.py', 'release_checks.py', 'automation_smoke.py', 'render_workspace_preview.py'}
SAMPLE_FILES = ('project.godot', 'main.gd', 'main.gd.uid', 'main.tscn', 'tests/score_test.gd')


def build(platform, destination):
    version = runpy.run_path(str(ROOT / 'updates.py'))['VERSION']
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid release version.')
    label = {'macos': 'macOS', 'linux': 'Linux'}[platform]
    prefix = f'TalkToAi-Code-{version}-{label}'
    script = f'install-{platform}.sh'
    sources = ['LICENSE', 'requirements-runtime.txt', script, 'assets/store/icon-150.png']
    sources += sorted(file.name for file in ROOT.glob('*.py')
                      if not file.name.startswith('test_') and file.name not in EXCLUDE)
    sources += [f'samples/score-arena/{name}' for name in SAMPLE_FILES]
    guide = (f'# TalkToAi Code {version} for {label}\n\n'
             f'Open a terminal in this folder and run `bash {script}`.\n\n'
             'Python 3.10 or newer and internet access for the first dependency setup are required. '
             'Install Ollama from https://ollama.com/download to run models on your computer. '
             'Conversation history and settings are stored in your own account.\n\n'
             'Guide: https://github.com/ResearchForumOnline/TalkToAi-Code#install\n').encode('utf-8')
    contents = {'INSTALL.md': guide}
    contents.update({name:(ROOT / name).read_bytes() for name in sources})
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    package = destination / (prefix + '.tar.gz')
    temporary = None
    try:
        with NamedTemporaryFile(dir=destination, prefix=prefix, suffix='.tmp', delete=False) as raw:
            temporary = Path(raw.name)
            with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode='w') as archive:
                    for name, data in sorted(contents.items()):
                        entry = tarfile.TarInfo(prefix + '/' + name)
                        entry.size = len(data)
                        entry.mode = 0o755 if name == script else 0o644
                        archive.addfile(entry, io.BytesIO(data))
        temporary.replace(package)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return package


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('platform', choices=('macos', 'linux'))
    parser.add_argument('destination')
    options = parser.parse_args()
    print(build(options.platform, options.destination))
