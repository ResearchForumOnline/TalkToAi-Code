"""Copy public dependency notices for this locally built direct payload."""
from importlib import metadata
from pathlib import Path
import json
import shutil
import sys
import urllib.request

stage = Path(__file__).resolve().parent.parent
payload = Path(sys.argv[1]).resolve() if len(sys.argv)>1 else stage / 'dist-studio-v6' / 'TalkToAiCode'
notices = payload / 'LICENSES'
notices.mkdir(exist_ok=True)
for name in ['LICENSE', 'THIRD-PARTY-NOTICES.md']:
    shutil.copyfile(stage / name, payload / name)
packages = ['PySide6', 'PySide6_Essentials', 'PySide6_Addons', 'shiboken6', 'Pillow', 'pypdf', 'playwright', 'pywinauto', 'comtypes', 'pywin32', 'PyInstaller']
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


def download_notice(url, destination):
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(128 * 1024 + 1)
    if not data or len(data) > 128 * 1024 or data.lstrip().lower().startswith((b'<!doctype', b'<html')):
        raise ValueError(f'Invalid license notice from {url}')
    destination.write_bytes(data)


for name in qt_licenses:
    url = 'https://code.qt.io/cgit/pyside/pyside-setup.git/plain/LICENSES/' + name + '?h=v6.8.3'
    download_notice(url, notices / name)

# This is the Qt Multimedia v6.8.3 commit used by the pinned PySide6 Addons wheel.
qt_multimedia = 'https://raw.githubusercontent.com/qt/qtmultimedia/2bf1135261bf0c346c88005c2c4d53efc0d2edd9/'
ffmpeg_paths = ['src/3rdparty/ffmpeg/qt_attribution.json', 'LICENSES/BSD-3-Clause.txt', 'LICENSES/MPL-2.0.txt']
for license_id in ('BSD-2-Clause', 'BSD-Source-Code', 'BSL-1.0', 'IJG', 'ISC', 'LGPL-2.1-or-later', 'MIT', 'Zlib'):
    ffmpeg_paths.append(f'src/3rdparty/ffmpeg/LICENSE.{license_id}.txt')
ffmpeg_notices = notices / 'FFmpeg'
ffmpeg_notices.mkdir(exist_ok=True)
for relative in ffmpeg_paths:
    download_notice(qt_multimedia + relative, ffmpeg_notices / Path(relative).name)
attributions = json.loads((ffmpeg_notices / 'qt_attribution.json').read_text(encoding='utf-8'))
ffmpeg = next(entry for entry in attributions if entry['Id'] == 'ffmpeg')
inventory.append({'package': 'FFmpeg', 'version': ffmpeg['Version'], 'license': ffmpeg['LicenseId'],
                  'source': ffmpeg['DownloadLocation'], 'attribution_source': qt_multimedia + ffmpeg_paths[0],
                  'included_notices': [Path(relative).name for relative in ffmpeg_paths]})
(notices / 'DEPENDENCIES.json').write_text(json.dumps(inventory, indent=2) + '\n', encoding='utf-8')
print('Preserved notices for ' + str(len(inventory)) + ' dependencies plus Python and Qt license terms.')
