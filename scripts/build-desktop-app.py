#!/usr/bin/env python3
"""Build the standalone TalkToAi Code desktop app for Linux or macOS with PyInstaller.

Run it on the platform being built (cross-building is not supported):

    python -m pip install -r requirements-runtime.txt pyinstaller
    python scripts/build-desktop-app.py linux dist-desktop
    python scripts/build-desktop-app.py macos dist-desktop

Linux:  TalkToAi-Code-<version>-Linux-x86_64.tar.gz (app folder, desktop entry,
        icon and install.sh), plus an AppImage when appimagetool is on PATH.
macOS:  TalkToAi Code.app inside TalkToAi-Code-<version>-macOS-<arch>.dmg.
        The app is ad-hoc signed only. Distribution outside your own Mac needs
        a Developer ID signature and notarization with your Apple account:
        set MACOS_SIGN_IDENTITY to sign during the build.

The Windows payload and Store package are built by store/build-payload.ps1
and store/build-msix.ps1.
"""
import argparse
import os
from pathlib import Path
import platform
import re
import runpy
import shutil
import subprocess
import sys
import tarfile


ROOT = Path(__file__).resolve().parent.parent
NAME = 'TalkToAiCode'
TITLE = 'TalkToAi Code'
BUNDLE_ID = 'org.talktoai.code'
SAMPLES = ('project.godot', 'main.gd', 'main.gd.uid', 'main.tscn', 'tests/score_test.gd')
# Imported inside functions; listed so every build includes them even if analysis changes.
HIDDEN = ('api_router', 'attachment_ingest', 'anthropic_stream', 'activity_bar', 'auto_tune', 'cpp_tools', 'effort',
          'engine_install', 'file_draft', 'hardware_probe', 'scene_kit', 'scene_lint', 'keyring.backends')
EXCLUDE = ('numpy', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'pywinauto', 'comtypes', 'win32crypt')


def version():
    value = runpy.run_path(str(ROOT / 'updates.py'))['VERSION']
    if not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise SystemExit('updates.VERSION is not a release version.')
    return value


def run(command, **options):
    print('+', ' '.join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], check=True, **options)


def pyinstaller(target, work, icon=None):
    separator = os.pathsep
    data = [f'{ROOT / "samples/score-arena" / name}{separator}samples/score-arena' + ('/tests' if name.startswith('tests/') else '')
            for name in SAMPLES]
    data.append(f'{ROOT / "assets/store/icon-150.png"}{separator}assets/store')
    data.append(f'{ROOT / "assets/zero-avatar"}{separator}assets/zero-avatar')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed', '--name', NAME,
               '--distpath', target, '--workpath', work / 'build', '--specpath', work,
               '--collect-all', 'playwright']
    for module in HIDDEN:
        command += ['--hidden-import', module]
    for module in EXCLUDE:
        command += ['--exclude-module', module]
    for item in data:
        command += ['--add-data', item]
    if icon:
        command += ['--icon', icon]
    if sys.platform == 'darwin':
        command += ['--osx-bundle-identifier', BUNDLE_ID]
        if os.environ.get('MACOS_SIGN_IDENTITY'):
            command += ['--codesign-identity', os.environ['MACOS_SIGN_IDENTITY']]
    command.append(ROOT / 'studio.py')
    run(command, cwd=ROOT)


def macos_icon(work):
    """Make an .icns from the master PNG with the system's own sips and iconutil."""
    source = ROOT / 'assets/store/talktoai-code-icon-master.png'
    iconset = work / f'{NAME}.iconset'
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            pixels = size * scale
            name = f'icon_{size}x{size}' + ('@2x' if scale == 2 else '') + '.png'
            run(['sips', '-z', pixels, pixels, source, '--out', iconset / name], stdout=subprocess.DEVNULL)
    icns = work / f'{NAME}.icns'
    run(['iconutil', '-c', 'icns', iconset, '-o', icns])
    return icns


def build_macos(destination, work, release):
    if sys.platform != 'darwin':
        raise SystemExit('Build the macOS app on macOS.')
    target = work / 'dist'
    pyinstaller(target, work, macos_icon(work))
    app = target / f'{NAME}.app'
    plist = app / 'Contents' / 'Info.plist'
    for key, value in (('CFBundleName', TITLE), ('CFBundleDisplayName', TITLE), ('CFBundleShortVersionString', release),
                       ('CFBundleVersion', release), ('LSMinimumSystemVersion', '12.0'),
                       ('NSHighResolutionCapable', 'true')):
        kind = 'bool' if value == 'true' else 'string'
        subprocess.run(['/usr/libexec/PlistBuddy', '-c', f'Delete :{key}', plist], stderr=subprocess.DEVNULL)
        run(['/usr/libexec/PlistBuddy', '-c', f'Add :{key} {kind} {value}', plist])
    named = target / f'{TITLE}.app'
    app.rename(named)
    if not os.environ.get('MACOS_SIGN_IDENTITY'):
        run(['codesign', '--force', '--deep', '--sign', '-', named])
    image = destination / f'TalkToAi-Code-{release}-macOS-{platform.machine()}.dmg'
    staging = work / 'dmg'
    staging.mkdir()
    shutil.move(str(named), staging / named.name)
    (staging / 'Applications').symlink_to('/Applications')
    run(['hdiutil', 'create', '-volname', TITLE, '-srcfolder', staging, '-ov', '-format', 'UDZO', image])
    return image


DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=TalkToAi Code
Comment=Local-first AI coding studio
Exec={exec}
Icon={icon}
Terminal=false
Categories=Development;IDE;
StartupWMClass=TalkToAiCode
"""

INSTALL_SH = """#!/usr/bin/env bash
# Install TalkToAi Code for this user: ~/.local/opt, a launcher in ~/.local/bin and a menu entry.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="$HOME/.local/opt/talktoai-code"
rm -rf "$target.new"
mkdir -p "$HOME/.local/opt" "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/256x256/apps"
cp -a "$here/TalkToAiCode" "$target.new"
rm -rf "$target"
mv "$target.new" "$target"
ln -sf "$target/TalkToAiCode" "$HOME/.local/bin/talktoai-code"
cp "$here/talktoai-code.png" "$HOME/.local/share/icons/hicolor/256x256/apps/talktoai-code.png"
sed "s|@EXEC@|$target/TalkToAiCode|" "$here/talktoai-code.desktop" > "$HOME/.local/share/applications/talktoai-code.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" || true
echo "TalkToAi Code is installed. Open it from your applications menu or run: talktoai-code"
"""


def build_linux(destination, work, release):
    if not sys.platform.startswith('linux'):
        raise SystemExit('Build the Linux app on Linux.')
    target = work / 'dist'
    pyinstaller(target, work)
    folder = work / f'TalkToAi-Code-{release}-Linux'
    folder.mkdir()
    shutil.move(str(target / NAME), folder / NAME)
    shutil.copy(ROOT / 'assets/store/talktoai-code-icon-master.png', folder / 'talktoai-code.png')
    (folder / 'talktoai-code.desktop').write_text(DESKTOP_ENTRY.format(exec='@EXEC@', icon='talktoai-code'), encoding='utf-8')
    installer = folder / 'install.sh'
    installer.write_text(INSTALL_SH, encoding='utf-8')
    installer.chmod(0o755)
    shutil.copy(ROOT / 'LICENSE', folder / 'LICENSE')
    archive = destination / f'TalkToAi-Code-{release}-Linux-{platform.machine()}.tar.gz'
    with tarfile.open(archive, 'w:gz') as bundle:
        bundle.add(folder, arcname=folder.name)
    made = [archive]
    tool = shutil.which('appimagetool')
    if tool:
        appdir = work / 'AppDir'
        shutil.copytree(folder / NAME, appdir / 'usr' / 'bin')
        shutil.copy(folder / 'talktoai-code.png', appdir / 'talktoai-code.png')
        (appdir / 'talktoai-code.desktop').write_text(DESKTOP_ENTRY.format(exec=NAME, icon='talktoai-code'), encoding='utf-8')
        launcher = appdir / 'AppRun'
        launcher.write_text('#!/bin/sh\nexec "$(dirname "$(readlink -f "$0")")/usr/bin/TalkToAiCode" "$@"\n', encoding='utf-8')
        launcher.chmod(0o755)
        image = destination / f'TalkToAi-Code-{release}-Linux-{platform.machine()}.AppImage'
        run([tool, appdir, image], env=dict(os.environ, ARCH=platform.machine()))
        made.append(image)
    return made


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('platform', choices=('linux', 'macos'))
    parser.add_argument('destination')
    options = parser.parse_args()
    release = version()
    destination = Path(options.destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    work = destination / f'.work-{options.platform}'
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    try:
        made = build_macos(destination, work, release) if options.platform == 'macos' else build_linux(destination, work, release)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for item in made if isinstance(made, list) else [made]:
        print(item)


if __name__ == '__main__':
    main()
