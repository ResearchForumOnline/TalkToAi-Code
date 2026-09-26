"""User-initiated GitHub release updates; no model inference or API billing."""
import hashlib
import json
import re
import urllib.request
import ctypes
import sys
from pathlib import Path

VERSION = '0.4.1'
REPO = 'https://github.com/ResearchForumOnline/TalkToAi-Code'
API = 'https://api.github.com/repos/ResearchForumOnline/TalkToAi-Code/releases?per_page=100'

def is_store_package():
    """True only when Windows actually launched this process with package identity."""
    if sys.platform != 'win32':
        return False
    try:
        length = ctypes.c_uint(0)
        result = ctypes.windll.kernel32.GetCurrentPackageFullName(
            ctypes.byref(length), None)
        return result == 0 or result == 122  # success or ERROR_INSUFFICIENT_BUFFER
    except (AttributeError, OSError):
        return False

def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:-preview)?', value)
    if not match:
        raise ValueError('Unrecognized release version')
    return tuple(map(int, match.groups()))

def select_release(data):
    tag = data['tag_name']
    newer = version_tuple(tag) > version_tuple(VERSION)
    version = '.'.join(map(str, version_tuple(tag)))
    name = f'TalkToAi-Code-{version}-Windows-Setup.exe'
    asset = next((a for a in data.get('assets', []) if a['name'] == name), None)
    if not asset:
        raise ValueError('This release has no Windows installer yet')
    expected_url = f'{REPO}/releases/download/{tag}/{name}'
    if asset['browser_download_url'] != expected_url:
        raise ValueError('Unexpected installer download location')
    return dict(newer=newer, version=version, url=expected_url, name=name,
                digest=asset.get('digest', ''), notes=data.get('body', ''),
                page=f'{REPO}/releases/tag/{tag}')

def check_release():
    request = urllib.request.Request(API, headers={'User-Agent':'TalkToAi-Code/'+VERSION})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)
    if not isinstance(data, list):
        raise ValueError('Unexpected release listing')
    releases = []
    for item in data:
        if not isinstance(item, dict) or item.get('draft'):
            continue
        try:
            releases.append(select_release(item))
        except (ValueError, KeyError, TypeError):
            continue
    if not releases:
        raise ValueError('No published Windows installer is available yet')
    return max(releases, key=lambda release: version_tuple(release['version']))

def download_release(release, folder):
    digest = release.get('digest') or ''
    if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest):
        raise ValueError('GitHub did not supply a SHA-256 digest. Open the release page to review it.')
    # Revalidate externally supplied metadata before downloading executable code.
    tag = release['page'].rsplit('/', 1)[-1]
    select_release({'tag_name':tag,'assets':[dict(name=release['name'], browser_download_url=release['url'])]})
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    destination = folder / release['name']
    partial = destination.with_suffix('.part')
    checksum = hashlib.sha256()
    try:
        with urllib.request.urlopen(release['url'], timeout=30) as response, partial.open('wb') as output:
            while chunk := response.read(1024*1024):
                checksum.update(chunk); output.write(chunk)
        if checksum.hexdigest() != digest.split(':',1)[1].lower():
            raise ValueError('Installer checksum mismatch; download discarded')
        partial.replace(destination)
        return str(destination)
    finally:
        partial.unlink(missing_ok=True)
