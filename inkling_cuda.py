"""Provision the pinned llama.cpp CUDA runner for an installed NVIDIA driver."""

from contextlib import ExitStack
import ctypes
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import sys
import tempfile
import time
import zipfile

from model_storage import _plain, library_root, storage_lock


CUDA_ARCHIVES = {
    '12.4': (
        ('llama-b12613-bin-win-cuda-12.4-x64.zip', 357_938_946,
         '41179bdab19d94eccafb5fa7391722be8be6c01ebb009313c3c98bda6c93fecc'),
        ('cudart-llama-bin-win-cuda-12.4-x64.zip', 391_443_627,
         '8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6'),
    ),
    '13.5': (
        ('llama-b12613-bin-win-cuda-13.5-x64.zip', 225_600_888,
         '87fbf5aab2747a0d18699d8f2894203b904b503926a1656c830ad68bcb5d3cee'),
        ('cudart-llama-bin-win-cuda-13.5-x64.zip', 426_314_199,
         'c2bf4cc202bfbaaa7a0b75f5082457c3811326947bb216059b72839d844e9cba'),
    ),
}
_DLL_NAME = re.compile(r'[a-z0-9][a-z0-9_.-]*\.dll')
_EXTRACT_LIMIT = 3 * 1024**3
_PROMOTION_TIMEOUT = 30
_LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
_CUDA_ERROR_NOT_INITIALIZED = 3


def bundled_crt_paths():
    """Return app-owned fallback CRT directories for a frozen Windows runner."""
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return ()
    frozen = getattr(sys, '_MEIPASS', '')
    if not frozen:
        return ()
    root = Path(frozen)
    app = Path(sys.executable).parent
    if not app.is_absolute() or root not in (app, app / '_internal') or os.pathsep in str(root):
        return ()
    # Prefer the loader's CRT over the copies bundled for Qt when both exist.
    candidates = (
        (root, ('vcruntime140.dll', 'vcruntime140_1.dll')),
        (root / 'PySide6', ('msvcp140.dll',)),
    )
    paths = []
    for folder, names in candidates:
        try:
            if _plain(folder).is_dir() and all(_plain(folder / name).is_file() for name in names):
                paths.append(str(folder))
        except (OSError, ValueError):
            continue
    return tuple(paths)


def _driver_version():
    if sys.platform != 'win32':
        return 0
    driver = Path(os.environ.get('SYSTEMROOT', r'C:\Windows')) / 'System32' / 'nvcuda.dll'
    if not driver.is_file():
        return 0
    try:
        # Keep dependency lookup in the Windows system directory.
        api = ctypes.WinDLL(str(driver), winmode=_LOAD_LIBRARY_SEARCH_SYSTEM32)
        query = api.cuDriverGetVersion
        query.argtypes = [ctypes.POINTER(ctypes.c_int)]
        query.restype = ctypes.c_int
        version = ctypes.c_int()
        result = query(ctypes.byref(version))
        if result == _CUDA_ERROR_NOT_INITIALIZED:
            initialize = api.cuInit
            initialize.argtypes = [ctypes.c_uint]
            initialize.restype = ctypes.c_int
            if initialize(0) == 0:
                result = query(ctypes.byref(version))
        return version.value if result == 0 else 0
    except (OSError, AttributeError):
        return 0


def _sdk_for_driver(version):
    if version >= 13000:
        return '13.5'
    if version >= 12000:
        return '12.4'
    return None


def _bundle_members(packages, sdk, server, base_dlls):
    major = sdk.split('.')[0]
    required = {server, *base_dlls, 'ggml-cuda.dll', 'ggml-cpu-x64.dll',
                f'cudart64_{major}.dll', f'cublas64_{major}.dll', f'cublaslt64_{major}.dll'}
    members = {}
    for package in packages:
        for item in package.infolist():
            name = item.filename.casefold()
            if name != server and not _DLL_NAME.fullmatch(name):
                continue
            if (name in members or name in {'nvcuda.dll', 'vulkan-1.dll'} or item.file_size <= 0
                    or item.is_dir() or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)):
                raise ValueError('Invalid Inkling CUDA runtime member.')
            members[name] = (package, item)
    if not required <= members.keys() or sum(item.file_size for _, item in members.values()) > _EXTRACT_LIMIT:
        raise ValueError('The verified Inkling CUDA bundle is incomplete or exceeds its extraction limit.')
    return members


def _move_engine(source, destination, cancel):
    from inkling_runtime import _cancelled

    deadline = time.monotonic() + _PROMOTION_TIMEOUT
    while True:
        _cancelled(cancel)
        try:
            source.replace(destination)
            return
        except PermissionError as exc:
            # Windows scanners may briefly hold a newly unpacked DLL open.
            if getattr(exc, 'winerror', None) not in (5, 32) or time.monotonic() >= deadline:
                raise
            cancel.wait(.25)


def cuda_runner(state_folder, cancel, notify):
    """Return a verified Windows CUDA server path, or None without a compatible driver."""
    from inkling_runtime import (
        ENGINE_TAG, _CHUNK, _DLLS, _SERVER, _cancelled, _digest, _download_pinned_archive, supported,
    )

    _cancelled(cancel)
    sdk = _sdk_for_driver(_driver_version()) if supported() else None
    if sdk is None:
        return None

    root = _plain(library_root(state_folder) / f'inkling-{ENGINE_TAG}')
    with storage_lock(root), tempfile.TemporaryDirectory(dir=root, prefix='.cuda-', ignore_cleanup_errors=True) as work, ExitStack() as stack:
        stage = _plain(Path(work))
        engine = _plain(root / f'engine-cuda-{sdk}')
        packages = []
        for name, size, digest in CUDA_ARCHIVES[sdk]:
            archive = _plain(root / name)
            valid = False
            if archive.is_file() and archive.stat().st_size == size:
                with archive.open('rb') as stream:
                    valid = _digest(stream, cancel) == digest
            if not valid:
                temporary = stage / 'download.zip'
                _download_pinned_archive(temporary, name, size, digest, cancel, notify,
                                         f'Downloading the verified Inkling CUDA {sdk} runner', timeout=900)
                temporary.replace(archive)
            packages.append(stack.enter_context(zipfile.ZipFile(archive)))
        members = _bundle_members(packages, sdk, _SERVER, _DLLS)
        if engine.is_dir() and {entry.name.casefold() for entry in engine.iterdir()} == members.keys():
            for name, (package, item) in members.items():
                local = _plain(engine / name)
                if not local.is_file() or local.stat().st_size != item.file_size:
                    break
                with package.open(item) as original, local.open('rb') as installed:
                    if _digest(original, cancel) != _digest(installed, cancel):
                        break
            else:
                return engine / _SERVER
        if shutil.disk_usage(root).free < sum(item.file_size for _, item in members.values()):
            raise OSError('There is not enough free disk space to prepare the Inkling CUDA runner.')
        prepared = stage / 'engine'
        prepared.mkdir()
        notify(f'Preparing the verified Inkling CUDA {sdk} runner', 0, 0)
        for name, (package, item) in members.items():
            with package.open(item) as source, (prepared / name).open('xb') as output:
                while chunk := source.read(_CHUNK):
                    _cancelled(cancel)
                    output.write(chunk)
        _cancelled(cancel)
        if engine.exists():
            _move_engine(engine, _plain(root / ('.previous-cuda-' + secrets.token_hex(8))), cancel)
        _move_engine(prepared, engine, cancel)
        return engine / _SERVER
