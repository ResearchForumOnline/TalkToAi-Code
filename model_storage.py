"""Manage Studio's Hugging Face exports and recover incomplete downloads."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from itertools import islice
import os
from pathlib import Path
import re
import stat
import time


_LEGACY_HASH = re.compile(r'[a-f0-9]{64}')
_TRANSFER = re.compile(r'[a-f0-9]{32}\.part')
_COMMIT = re.compile(r'[a-f0-9]{40}')
_REPO_PART = re.compile(r'[\w.-]+', re.ASCII)
_SCAN_LIMIT = 12000
PARTIAL_RETENTION_DAYS = 30


def native_path(path):
    """Use Win32 Unicode paths even on PCs with long path policy disabled."""
    path = Path(os.path.abspath(path))
    if os.name != 'nt' or str(path).startswith('\\\\?\\'):
        return path
    value = str(path)
    return Path('\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value)


def display_path(path):
    value = str(path)
    if os.name == 'nt' and value.startswith('\\\\?\\UNC\\'):
        return Path('\\\\' + value[8:])
    if os.name == 'nt' and value.startswith('\\\\?\\'):
        return Path(value[4:])
    return Path(path)


def library_root(folder):
    path = native_path(folder)
    if _linked(path):
        raise ValueError('The model library folder is redirected. Choose its actual location.')
    # Resolve short AppData paths through Windows' packaged-app redirection
    # before carrying the physical package directory into long-path operations.
    plain = display_path(path)
    if os.name == 'nt' and len(str(plain)) < 248:
        return native_path(plain.resolve())
    return path.resolve()


def _linked(path):
    if os.name == 'nt':
        try:
            return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        except FileNotFoundError:
            return False
    return path.is_symlink()


def _plain(path):
    if _linked(path) or path.resolve() != path:
        raise ValueError('The model library contains a redirected path. Check its location and retry.')
    return path


@contextmanager
def storage_lock(root):
    """Serialize setup and cleanup across Studio windows and app processes."""
    root.mkdir(parents=True, exist_ok=True)
    lock = _plain(root / '.transfer.lock')
    with lock.open('a+b') as handle:
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                # Locking an unwritten byte is supported. Closing releases it on exit or crash.
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise BlockingIOError('Another model download or storage operation is active. Retry when it finishes.') from exc
        yield


def transfer_file(root, repo, revision, name):
    """Adopt an 8.8 partial into the shorter path before resuming the same file."""
    work = _plain(root / '.partial')
    work.mkdir(exist_ok=True)
    key = hashlib.blake2b('\0'.join((repo, revision, name)).encode(), digest_size=16).hexdigest()
    current = _plain(work / (key + '.part'))
    legacy_folder = work / hashlib.sha256(f'{repo}@{revision}'.encode()).hexdigest()
    previous = _plain(legacy_folder / hashlib.sha256(name.encode()).hexdigest())
    if not current.exists() and previous.is_file():
        previous.replace(current)
        try:
            legacy_folder.rmdir()
        except OSError:
            pass  # Other files in the same revision may still be resumable.
    return current


def _partial_files(work):
    """Recognize only the two cache layouts written by Studio."""
    budget = _SCAN_LIMIT
    with os.scandir(work) as top:
        for entry in top:
            budget -= 1
            if budget <= 0:
                return
            path = Path(entry.path)
            if _linked(path):
                continue
            if _TRANSFER.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                yield path
            elif _LEGACY_HASH.fullmatch(entry.name) and entry.is_dir(follow_symlinks=False):
                with os.scandir(path) as children:
                    for child in children:
                        budget -= 1
                        if budget <= 0:
                            return
                        item = Path(child.path)
                        if _LEGACY_HASH.fullmatch(child.name) and child.is_file(follow_symlinks=False) and not _linked(item):
                            yield item


def clean_stale_partials(folder):
    """Best-effort maintenance; a startup scan never interrupts an active transfer."""
    removed = 0
    try:
        root = library_root(folder)
        work = _plain(root / '.partial')
        if not work.is_dir():
            return 0
        with storage_lock(root):
            cutoff = time.time() - PARTIAL_RETENTION_DAYS * 86400
            for path in _partial_files(work):
                try:
                    info = path.lstat()
                    if stat.S_ISREG(info.st_mode) and info.st_mtime < cutoff:
                        _plain(path).unlink()
                        removed += info.st_size
                        if path.parent != work:
                            try:
                                path.parent.rmdir()
                            except OSError:
                                pass
                except (OSError, ValueError):
                    continue
    except (OSError, ValueError, RuntimeError):
        pass
    return removed


@dataclass(frozen=True)
class ExportFile:
    relative: str
    size: int
    modified_ns: int
    device: int
    inode: int


def _export_file(root, path):
    info = path.lstat()
    if _linked(path) or not stat.S_ISREG(info.st_mode):
        raise ValueError('The selected download is no longer a regular file.')
    return ExportFile(path.relative_to(root).as_posix(), info.st_size, info.st_mtime_ns,
                      info.st_dev, info.st_ino)


def partial_inventory(folder):
    root = library_root(folder)
    work = _plain(root / '.partial')
    records = []
    if work.is_dir():
        for path in _partial_files(work):
            try:
                records.append(_export_file(root, path))
            except (OSError, ValueError):
                pass
    return records


def clear_partials(folder, reviewed):
    """A manual cleanup may only delete partials included in the previous inventory."""
    root = library_root(folder)
    selected = set(reviewed)
    removed = 0
    with storage_lock(root):
        for item in partial_inventory(root):
            if item in selected:
                path = _plain(root / item.relative)
                if _export_file(root, path) == item:
                    path.unlink()
                    removed += 1
    return removed


def saved_exports(folder):
    """List individual files within Studio's owner/repo/commit or repo/commit layouts."""
    root = library_root(folder)
    if not root.is_dir():
        return [], False
    pending = [(root, 0, False)]
    files = []
    remaining = _SCAN_LIMIT
    incomplete = False
    while pending and remaining > 0:
        parent, depth, in_revision = pending.pop()
        try:
            if _linked(parent):
                incomplete = True
                continue
            with os.scandir(parent) as listing:
                for entry in islice(listing, remaining):
                    remaining -= 1
                    path = Path(entry.path)
                    if _linked(path):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        revision = depth in (1, 2) and bool(_COMMIT.fullmatch(entry.name))
                        repo_segment = depth < 2 and not entry.name.startswith('.') and _REPO_PART.fullmatch(entry.name)
                        if in_revision or revision or repo_segment:
                            pending.append((path, depth + 1, in_revision or revision))
                    elif in_revision and entry.is_file(follow_symlinks=False):
                        files.append(_export_file(root, path))
        except (OSError, ValueError):
            incomplete = True
    files.sort(key=lambda item: (-item.size, item.relative.casefold()))
    return files, incomplete or remaining <= 0


def reviewed_export(root, selected):
    """Resolve only the same regular file that the user saw in the inventory."""
    relative = Path(selected.relative)
    parts = relative.parts
    if (relative.is_absolute() or relative.drive or '..' in parts
            or len(parts) < 3 or parts[0].startswith('.')
            or not any(i < len(parts) - 1 and _COMMIT.fullmatch(parts[i]) for i in (1, 2))):
        raise ValueError('Select a saved Hugging Face file from this model library.')
    path = _plain(root / relative)
    if _export_file(root, path) != selected:
        raise ValueError('That file changed after the list was opened. Refresh saved downloads and try again.')
    return path


def remove_export(folder, selected):
    """Remove only the exact file reviewed in the app, if it is still the same file."""
    root = library_root(folder)
    with storage_lock(root):
        reviewed_export(root, selected).unlink()
