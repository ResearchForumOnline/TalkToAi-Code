"""Install vetted game asset packs into the selected project."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import tempfile
from urllib.parse import urlsplit
import urllib.request
import zipfile

from asset_catalog import PACKS


MAX_BYTES = 20_000_000_000
LICENSE = 'https://creativecommons.org/publicdomain/zero/1.0/'
BASE = 'https://kenney.nl'
DATA_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.tga', '.dds',
                 '.obj', '.mtl', '.fbx', '.gltf', '.glb', '.bin', '.blend', '.vox', '.dae',
                 '.wav', '.ogg', '.mp3', '.flac', '.aif', '.aiff', '.txt', '.md', '.json',
                 '.xml', '.tmx', '.tsx', '.aseprite', '.ase', '.csv', '.pdf', '.ttf', '.otf'}


def pack_source(pack):
    return BASE + '/assets/' + pack['slug']


class KenneyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != 'https' or target.netloc not in ('kenney.nl', 'www.kenney.nl'):
            raise ValueError('Kenney redirected to an unexpected download host.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_cancel(cancel):
    if cancel.is_set():
        raise InterruptedError('Asset installation stopped.')


def _download(pack, archive, cancel, progress):
    source = pack_source(pack)
    opener = urllib.request.build_opener(KenneyRedirect)
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; TalkToAiCode/8.7)', 'Referer': source}
    identifier = ('assets/' + pack['slug']).encode('ascii').hex()
    request = urllib.request.Request(BASE+'/download-link/'+identifier, headers=headers | {'Accept': 'application/json'})
    with opener.open(request, timeout=20) as response:
        metadata = json.loads(response.read(32768))
    url = urlsplit(metadata.get('url', ''))
    if url.scheme != 'https' or url.netloc not in ('kenney.nl', 'www.kenney.nl') or not url.path.startswith('/download/'):
        raise ValueError('Kenney did not return a download link for this pack.')
    size = 0
    _check_cancel(cancel)
    request = urllib.request.Request(url.geturl(), headers=headers)
    with opener.open(request, timeout=20) as response, archive.open('xb') as handle:
        while chunk := response.read(1024 * 1024):
            _check_cancel(cancel)
            size += len(chunk)
            if size > pack['bytes']:
                raise ValueError('The creator has updated this asset pack. Open the source page for the current edition.')
            handle.write(chunk)
            progress(size, pack['bytes'])


def _validate_archive(path, pack, cancel):
    if path.stat().st_size != pack['bytes']:
        raise ValueError('Asset ZIP size does not match the catalog.')
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        while chunk := handle.read(1024 * 1024):
            _check_cancel(cancel)
            digest.update(chunk)
    if digest.hexdigest() != pack['sha256']:
        raise ValueError('Asset ZIP checksum differs from the catalog. Open the source page for the current edition.')


def _extract(source, target, cancel):
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > 30000 or sum(item.file_size for item in entries) > MAX_BYTES:
            raise ValueError('Asset archive exceeds the extraction limit.')
        if shutil.disk_usage(target).free < sum(item.file_size for item in entries):
            raise OSError('There is insufficient free space to extract this asset pack.')
        seen = set()
        count = 0
        for entry in entries:
            _check_cancel(cancel)
            relative = PurePosixPath(entry.filename)
            if relative.is_absolute() or not relative.parts or '..' in relative.parts or any(
                    PureWindowsPath(part).is_reserved() or any(ch in part for ch in '\\:*?"<>|')
                    or part.endswith((' ', '.')) for part in relative.parts):
                raise ValueError('Asset archive contains an invalid extraction path.')
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError('Asset archive contains a symbolic link.')
            if entry.is_dir() or relative.parts[0] == '__MACOSX' or relative.suffix.lower() not in DATA_SUFFIXES:
                continue
            key = relative.as_posix().casefold()
            if key in seen:
                raise ValueError('Asset archive contains conflicting filenames.')
            seen.add(key)
            destination = target.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as reader, destination.open('xb') as writer:
                while chunk := reader.read(1024 * 1024):
                    _check_cancel(cancel)
                    writer.write(chunk)
            count += 1
        if not count:
            raise ValueError('This ZIP contained no recognized game assets.')
        return count


def install_pack(project, pack, cancel, progress=lambda *_: None, local_archive=None):
    _check_cancel(cancel)
    project = Path(project).resolve(strict=True)
    library = (project / 'assets' / 'talktoai-library').resolve()
    if project not in library.parents:
        raise ValueError('The game-asset library path must stay inside the selected project.')
    target = library / pack['slug']
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"{pack['title']} already has a folder in this project.")
    library.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.talktoai-assets-', dir=library) as work:
        stage = Path(work)
        archive = Path(local_archive) if local_archive else stage / 'download.zip'
        if local_archive is None:
            if shutil.disk_usage(library).free < pack['bytes']:
                raise OSError('There is insufficient free space to download this asset pack.')
            _download(pack, archive, cancel, progress)
        content = stage / 'content'
        content.mkdir()
        _validate_archive(archive, pack, cancel)
        files = _extract(archive, content, cancel)
        receipt = {'title': pack['title'], 'source': pack_source(pack), 'publisher': 'Kenney',
                   'license': 'CC0 1.0', 'license_url': LICENSE, 'archive_sha256': pack['sha256'], 'files': files}
        with (content / 'talktoai-asset-source.json').open('x', encoding='utf-8') as out:
            json.dump(receipt, out, indent=2)
        _check_cancel(cancel)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"{pack['title']} already has a folder in this project.")
        os.rename(content, target)
    return files
