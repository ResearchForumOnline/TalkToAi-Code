"""Prepare the two pinned Inkling 2 Mini exports for upstream llama.cpp."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

from model_library import (
    HF_URL, ModelFile, _check_cancel, _disk_check, _hash_existing, download_files,
)
from model_storage import display_path, library_root, native_path, storage_lock, transfer_file, _plain


INKLING_REPO = 'unsloth/Inkling-2-Mini-GGUF'
INKLING_REVISION = 'ae9327c5fbc7333116ba32eee78bb5d2bbefae4f'
_HEADER_SIZE = 11_979_744
_TRAILER_SIZE = 442_368
_TRAILER_SHA = '583b63f469b4174ffc14e7daaef0c6bc21e29b7e2e36d5847a6878b411cbfe47'
_COPY_CHUNK = 4 * 1024 * 1024


@dataclass(frozen=True)
class InklingPreset:
    label: str
    legacy: ModelFile
    mainline: ModelFile
    header_sha256: str
    legacy_spans: tuple[tuple[int, int], ...]


# At this revision Mainline_Support renames the loop controllers and repacks
# the query tensors. The spans below are absolute offsets and lengths in the
# original files. All 732 copied tensor extents match the Mainline tensor table
# after the controller renames. Only the header and twelve final query tensors
# need to be fetched. A full publisher checksum is required before activation.
INKLING_PRESETS = {
    'q4': InklingPreset(
        'Inkling 2 Mini Q4_K_M',
        ModelFile('Inkling-2-Mini-UD-Q4_K_M.gguf', 16_610_543_328,
                  'f90c5a4df44d335ac9f9eea2a15c31d6c5a377b1b3cd9ff7fc2cf123f3cdf7e1'),
        ModelFile('Mainline_Support/Inkling-2-Mini-UD-Q4_K_M.gguf_file', 16_610_544_608,
                  '78fcedcf93c5d1bfe83e225513b71392bb52259be0eaa5f8899c856a2e5834ae'),
        '0aabe10e7a0c4e894b8205fe5c8fe94c3b4da6098c402e1be19fbe6633ef4f66',
        ((12_420_832, 16_598_122_496),),
    ),
    'q8': InklingPreset(
        'Inkling 2 Mini Q8_K_XL',
        ModelFile('Inkling-2-Mini-UD-Q8_K_XL.gguf', 22_867_451_616,
                  'dfb04bf37bee457f98800989dedf93ec442ede8636dbea9433992f1e527e9a41'),
        ModelFile('Mainline_Support/Inkling-2-Mini-UD-Q8_K_XL.gguf_file', 22_867_452_896,
                  '2da51788d43a17e1f033bec420fa83ba9cb87ac1f0130dc9135a4d2ed2678b68'),
        'd06c9fdc5ded0ae70c7a709ed6184e60233b19cfab0b35385852213ed5733bb5',
        ((11_978_464, 21_928_702_976), (21_941_123_808, 926_327_808)),
    ),
}


def preset_for_hub(repo, files):
    """Recognise the supported quant from a single selection in the publisher's repo."""
    if repo.casefold() != INKLING_REPO.casefold() or len(files) != 1:
        return None
    name = files[0].name.casefold()
    return next((key for key, preset in INKLING_PRESETS.items()
                 if name in (preset.legacy.name.casefold(), preset.mainline.name.casefold())), None)


def preset_for_path(path):
    """Recognise the publisher's compatible and original filenames."""
    name = os.fspath(path).replace('\\', '/').rsplit('/', 1)[-1].casefold()
    return next((key for key, preset in INKLING_PRESETS.items()
                 if name in (preset.legacy.name.casefold(), Path(preset.mainline.name).name.casefold())), None)


def _paths(root, preset):
    repo_root = root.joinpath(*INKLING_REPO.split('/'), INKLING_REVISION)
    remote = _plain(repo_root / preset.mainline.name)
    return _plain(remote.with_suffix('.gguf')), remote, _plain(repo_root / preset.legacy.name)


def _usable(path):
    plain = display_path(path)
    return path if os.name == 'nt' and len(str(plain)) >= 248 else plain


def _identity(path):
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Choose a regular Inkling model file.')
    return [info.st_size, info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns]


def _receipt_path(root, key):
    return _plain(root / '.inkling-ready' / (key + '.json'))


def _remember(root, key, preset, target):
    receipt = _receipt_path(root, key)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    stage = _plain(receipt.with_suffix('.tmp'))
    stage.write_text(json.dumps([preset.mainline.sha256, _identity(target)]), encoding='utf-8')
    stage.replace(receipt)


def prepared_path(root, key):
    """Quickly check that the app's verified model still has the same file identity."""
    if not isinstance(key, str) or key not in INKLING_PRESETS:
        return None
    try:
        root = library_root(root)
        preset = INKLING_PRESETS[key]
        target, _, _ = _paths(root, preset)
        identity = _identity(target)
        if identity[0] == preset.mainline.size:
            with _receipt_path(root, key).open(encoding='utf-8') as receipt:
                if json.loads(receipt.read(2048)) == [preset.mainline.sha256, identity]:
                    return _usable(target)
    except (OSError, ValueError, RuntimeError):
        pass
    return None


def _read_range(file, start, size, checksum, cancel):
    last = start + size - 1
    name = quote(file.name)
    url = f'{HF_URL}/{INKLING_REPO}/resolve/{INKLING_REVISION}/{name}?download=true'
    request = Request(url, headers={
        'Range': f'bytes={start}-{last}', 'Accept-Encoding': 'identity',
        'User-Agent': 'TalkToAiCode/Inkling',
    })
    _check_cancel(cancel)
    with urlopen(request, timeout=25) as reply:
        if reply.status != 206 or reply.headers.get('Content-Range') != f'bytes {start}-{last}/{file.size}':
            raise ValueError('Hugging Face did not return the exact Inkling compatibility range. The original is still saved.')
        data = bytearray()
        while len(data) < size:
            _check_cancel(cancel)
            chunk = reply.read(min(_COPY_CHUNK, size - len(data)))
            if not chunk:
                break
            data.extend(chunk)
    if len(data) != size or hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError('Inkling compatibility data failed its checksum. The original is still saved.')
    return data


def _matches(path, file, cancel, notify):
    if not path.is_file() or path.stat().st_size != file.size:
        return False
    identity = _identity(path)
    digest = hashlib.sha256()
    notify('Checking ' + path.name, 0, 0)
    _hash_existing(path, digest, cancel)
    return digest.hexdigest() == file.sha256 and _identity(path) == identity


def _prepare_existing(root, preset, source, target, cancel, notify):
    identity = _identity(source)
    total = preset.mainline.size
    copy_size = identity[0]
    if copy_size not in (preset.legacy.size, total):
        raise ValueError('This Inkling file has a different size from the supported download. Check the quant and use Recommended to try again.')
    _disk_check(root, total)
    header = trailer = b''
    if copy_size == preset.legacy.size:
        notify('Fetching 12.4 MB of Inkling compatibility data', 0, 0)
        header = _read_range(preset.mainline, 0, _HEADER_SIZE, preset.header_sha256, cancel)
        trailer = _read_range(preset.mainline, total - _TRAILER_SIZE, _TRAILER_SIZE, _TRAILER_SHA, cancel)
        spans = preset.legacy_spans
    else:
        spans = ((0, total),)
    stage = transfer_file(root, INKLING_REPO, INKLING_REVISION, 'inkling-compatible/' + preset.legacy.name)
    stage.unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    done = len(header)
    activity = 'Preparing ' + preset.label + ' from your saved weights'
    try:
        with source.open('rb') as original, stage.open('xb') as output:
            output.write(header)
            digest.update(header)
            next_update = 0
            for offset, remaining in spans:
                original.seek(offset)
                while remaining:
                    _check_cancel(cancel)
                    chunk = original.read(min(_COPY_CHUNK, remaining))
                    if not chunk:
                        raise ValueError('The source model ended early. Download the matching Inkling preset again.')
                    output.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if now >= next_update:
                        notify(activity, done, total)
                        next_update = now + .25
            output.write(trailer)
            digest.update(trailer)
            output.flush()
            os.fsync(output.fileno())
        _check_cancel(cancel)
        if done + len(trailer) != total or digest.hexdigest() != preset.mainline.sha256 or _identity(source) != identity:
            raise ValueError('The saved Inkling weights did not match the publisher checksum. The original is still saved.')
        stage.replace(_plain(target))
    finally:
        stage.unlink(missing_ok=True)


def prepare_inkling(root, key, cancel, notify, *, source=None):
    """Reuse verified weights or prepare the chosen exact compatible preset."""
    preset = INKLING_PRESETS.get(key) if isinstance(key, str) else None
    if preset is None:
        raise ValueError('Choose a supported Inkling preset from Recommended.')
    root = library_root(root)
    target, remote, legacy = _paths(root, preset)
    with storage_lock(root):
        _check_cancel(cancel)
        ready = prepared_path(root, key)
        if ready is not None:
            return ready
        if _matches(target, preset.mainline, cancel, notify):
            _remember(root, key, preset, target)
            return _usable(target)
        if source is not None:
            candidate = native_path(source).resolve(strict=True)
            if candidate == remote and _matches(candidate, preset.mainline, cancel, notify):
                _check_cancel(cancel)
                candidate.replace(target)
            else:
                _prepare_existing(root, preset, candidate, target, cancel, notify)
        elif _matches(remote, preset.mainline, cancel, notify):
            _check_cancel(cancel)
            remote.replace(target)
        elif legacy.is_file() and legacy.stat().st_size == preset.legacy.size:
            _prepare_existing(root, preset, legacy, target, cancel, notify)
        else:
            target = None
        if target is not None:
            _remember(root, key, preset, target)
            notify('Verified ' + preset.label, preset.mainline.size, preset.mainline.size)
            return _usable(target)

    _, (download,) = download_files(INKLING_REPO, INKLING_REVISION, [preset.mainline],
                                     root, '', cancel, notify)
    with storage_lock(root):
        _check_cancel(cancel)
        target, remote, _ = _paths(root, preset)
        ready = prepared_path(root, key)
        if ready is not None:
            return ready
        if native_path(download) != remote or _identity(remote)[0] != preset.mainline.size:
            raise ValueError('The prepared Inkling download changed. Retry setup.')
        remote.replace(target)
        _remember(root, key, preset, target)
        return _usable(target)
