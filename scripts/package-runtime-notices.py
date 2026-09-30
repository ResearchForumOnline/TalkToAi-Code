"""Copy public dependency notices for this locally built direct payload."""
from importlib import metadata
from pathlib import Path
import json
import shutil
import sys
import urllib.request

stage = Path(__file__).resolve().parent.parent
payload = stage / 'dist-studio-v6' / 'TalkToAiCode'
notices = payload / 'LICENSES'
notices.mkdir(exist_ok=True)
for name in ['LICENSE', 'THIRD-PARTY-NOTICES.md']:
    shutil.copyfile(stage / name, payload / name)
packages = ['PySide6', 'PySide6_Essentials', 'shiboken6', 'Pillow', 'pypdf', 'playwright', 'pywinauto', 'comtypes', 'pywin32', 'PyInstaller']
inventory = []
for name in packages:
    dist = metadata.distribution(name)
    target = notices / name
    target.mkdir(exist_ok=True)
    copied = []
    for item in dist.files or []:
        rel = str(item).replace('\\', '/')
        if not any(word in rel.lower() for word in ['license', 'copying', 'notice']):
            continue
        source = Path(dist.locate_file(item))
        if not source.is_file():
            continue
        assert source.stat().st_size <= 2 * 1024 * 1024
        destination = target / rel
        assert destination.resolve().is_relative_to(target.resolve())
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied.append(rel)
    inventory.append({'package': name, 'version': dist.version, 'license': dist.metadata.get('License'), 'included_notices': copied})
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if python_license.is_file():
    shutil.copyfile(python_license, notices / 'Python-LICENSE.txt')
qt_licenses = ['LGPL-3.0-only.txt', 'GPL-3.0-only.txt', 'GPL-2.0-only.txt']
for name in qt_licenses:
    url = 'https://code.qt.io/cgit/pyside/pyside-setup.git/plain/LICENSES/' + name + '?h=v6.8.3'
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(128 * 1024)
    assert data and not data.lstrip().startswith(b'<!DOCTYPE')
    (notices / name).write_bytes(data)
(notices / 'DEPENDENCIES.json').write_text(json.dumps(inventory, indent=2) + '\n', encoding='utf-8')
print('Preserved notices for ' + str(len(inventory)) + ' dependency distributions plus Python and Qt license terms.')
