"""Install vetted game asset packs into the selected project."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import tempfile
import time
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
_RECEIPT = 'talktoai-asset-source.json'
_TEXTURES = {'.png', '.jpg', '.jpeg', '.bmp', '.tga', '.dds'}
_AUDIO = {'.wav', '.ogg', '.mp3'}
ENGINE_SUFFIXES = {
    'godot': _TEXTURES | _AUDIO | {'.glb', '.gltf', '.fbx', '.obj', '.dae', '.svg', '.webp', '.ttf', '.otf'},
    'unity': _TEXTURES | _AUDIO | {'.fbx', '.obj', '.dae', '.aif', '.aiff', '.ttf', '.otf'},
    'blender': _TEXTURES | {'.glb', '.gltf', '.fbx', '.obj', '.blend', '.svg'},
}


def asset_engine(project):
    project = Path(project)
    if (project / 'project.godot').is_file():
        return 'godot'
    if (project / 'ProjectSettings/ProjectVersion.txt').is_file():
        return 'unity'
    return 'general'


def game_asset_directory(project):
    project = Path(project).resolve(strict=True)
    folder = 'Assets' if asset_engine(project) == 'unity' else 'assets'
    directory = (project / folder / 'talktoai-library').resolve()
    if project not in directory.parents or directory != project / folder / 'talktoai-library':
        raise ValueError('The game-asset library must be an ordinary directory inside the selected project.')
    return directory


def _linked(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def find_game_assets(project, query='', engine='auto', cancel=None):
    """Discover eligible source assets and their engine paths without importing them."""
    project = Path(project).resolve(strict=True)
    library = game_asset_directory(project)
    detected = asset_engine(project)
    engine = str(engine or 'auto').lower().strip()
    if engine == 'auto':
        engine = detected
    if engine not in {'general', *ENGINE_SUFFIXES}:
        raise ValueError('Choose Auto, Godot, Unity or Blender.')
    if engine == 'unity' and detected != 'unity':
        raise ValueError('Select the Unity project folder containing ProjectSettings/ProjectVersion.txt.')
    query = str(query).strip().casefold()
    suffixes = ENGINE_SUFFIXES.get(engine, set().union(*ENGINE_SUFFIXES.values()))
    assets = []
    installed = []
    scanned = 0
    deadline = time.monotonic() + 3
    truncated = False
    for pack in PACKS:
        folder = library / pack['slug']
        receipt = folder / _RECEIPT
        if _linked(folder) or _linked(receipt) or not receipt.is_file():
            continue
        try:
            if receipt.stat().st_size > 16000:
                continue
            meta = json.loads(receipt.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or meta.get('archive_sha256') != pack['sha256']:
            continue
        installed.append(pack['title'])
        if truncated:
            continue
        for parent, dirs, files in os.walk(folder):
            if cancel and cancel.is_set():
                raise InterruptedError('Asset search stopped.')
            dirs[:] = sorted(name for name in dirs if not name.startswith('.') and not _linked(Path(parent, name)))
            for name in sorted(files):
                scanned += 1
                if scanned > 30000 or time.monotonic() > deadline or len(assets) >= 36:
                    truncated = True
                    break
                path = Path(parent, name)
                if path.suffix.lower() not in suffixes or _linked(path):
                    continue
                relative = path.relative_to(project).as_posix()
                if query and query not in (pack['title'] + ' ' + relative).casefold():
                    continue
                engine_path = ('res://' + relative if engine == 'godot' else
                               'Assets/' + path.relative_to(library.parent).as_posix() if engine == 'unity' else relative)
                assets.append({'pack': pack['title'], 'path': relative, 'engine_path': engine_path,
                               'format': path.suffix.lower()[1:], 'license': 'CC0 1.0'})
            if truncated:
                break
    result = {'engine': engine, 'library': library.relative_to(project).as_posix(),
              'installed_packs': installed, 'assets': assets, 'truncated': truncated}
    legacy = project / 'assets/talktoai-library'
    if detected == 'unity' and legacy.is_dir() and legacy.resolve() != library:
        result['legacy_library'] = 'An older lowercase assets folder exists. Reinstall a pack into Assets from Game assets to have Unity import it.'
    return result


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
    library = game_asset_directory(project)
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
            bundle = Path(__file__).resolve().parent / 'assets' / 'kenney-cc0.zip'
            if bundle.is_file():
                with zipfile.ZipFile(bundle) as collection:
                    catalog = json.loads(collection.read('catalog.json'))
                    entry = next((p for p in catalog['packs'] if p['slug'] == pack['slug']), None)
                    if entry is None or collection.getinfo(entry['archive']).file_size != pack['bytes']:
                        raise ValueError('The bundled asset pack does not match the catalog. Use Import verified ZIP to recover.')
                    with collection.open(entry['archive']) as src, archive.open('xb') as dst:
                        while chunk := src.read(1024 * 1024):
                            _check_cancel(cancel)
                            dst.write(chunk)
                            progress(dst.tell(), pack['bytes'])
            else:
                _download(pack, archive, cancel, progress)
        content = stage / 'content'
        content.mkdir()
        _validate_archive(archive, pack, cancel)
        files = _extract(archive, content, cancel)
        receipt = {'title': pack['title'], 'source': pack_source(pack), 'publisher': 'Kenney',
                   'license': 'CC0 1.0', 'license_url': LICENSE, 'archive_sha256': pack['sha256'], 'files': files}
        with (content / _RECEIPT).open('x', encoding='utf-8') as out:
            json.dump(receipt, out, indent=2)
        _check_cancel(cancel)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"{pack['title']} already has a folder in this project.")
        os.rename(content, target)
    return files
