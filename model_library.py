"""Local model installation and verified Hugging Face artifact downloads."""

from dataclasses import dataclass
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path, PureWindowsPath
from queue import Empty, Full, Queue
import re
import shutil
import socket
import threading
import time
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener, urlopen


OLLAMA_URL = 'http://127.0.0.1:11434'
HF_URL = 'https://huggingface.co'
DEFAULT_LOCAL_MODEL = 'hf.co/shafire/OpenZero-Qwen3-1.7B-Agentic-GGUF:Q4_K_M'
QWEN_LOCAL_MODEL = 'qwen3.6:35b'
OPENZERO_REPO = 'shafire/OpenZero-Qwen3-1.7B-Agentic-GGUF'
OPENZERO_REVISION = '0ea0a8bd0e83d84f506818a18c08a15f590ae04e'
QWEN_REPO = 'unsloth/Qwen3.6-35B-A3B-GGUF'
QWEN_REVISION = 'a483e9e6cbd595906af30beda3187c2663a1118c'
QWEN_OLLAMA_ESTIMATE = 23_000_000_000
_CHUNK = 1024 * 1024
_LIMIT = 12 * 1024 * 1024
_SHARD = re.compile(r'(.+)-(\d{5})-of-(\d{5})\.gguf', re.I)
_SHA256 = re.compile(r'[a-f0-9]{64}')
_COMMIT = re.compile(r'[a-f0-9]{40}')
_REPO = re.compile(r'[\w.-]+(?:/[\w.-]+)?', re.ASCII)
_HUB_HOSTS = {'huggingface.co', 'www.huggingface.co', 'hf.co'}
_LOCAL_HTTP = build_opener(ProxyHandler({}))
_FILE_LOCK = threading.Lock()


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str = ''
    git_sha1: str = ''


OPENZERO_FILE = ModelFile(
    'OpenZero-Qwen3-1.7B-Agentic-Q4_K_M.gguf', 1_107_408_576,
    '1d43348dc10a4b97ec733cc435e398393cd235f7f6088b0cced2382ed8c9b1b7',
)
QWEN_FILE = ModelFile(
    'Qwen3.6-35B-A3B-UD-Q4_K_M.gguf', 22_134_528_992,
    'ac0e2c1189e055faa36eff361580e79c5bd6f8e76bffb4ce547f167d53e31a61',
)


class DownloadCancelled(RuntimeError):
    pass


def _check_cancel(cancel):
    if cancel.is_set():
        raise DownloadCancelled('Model download stopped.')


def format_size(size):
    return f'{size / 1e9:.1f} GB' if size >= 1e9 else f'{size / 1e6:.1f} MB'


def _repository_id(text):
    if not _REPO.fullmatch(text) or any(part in ('.', '..') for part in text.split('/')):
        raise ValueError('Enter a Hugging Face model URL or owner/repository.')
    return text


def _revision(value):
    value = value.strip() or 'main'
    if not re.fullmatch(r'[\w./-]{1,160}', value, re.ASCII) or any(
        part in ('', '.', '..') for part in value.split('/')
    ):
        raise ValueError('Use a Hugging Face branch, tag or commit hash for the revision.')
    return value


def _reference(text, revision):
    text = text.strip().rstrip('/')
    if text.partition('/')[0].lower() in _HUB_HOSTS:
        text = 'https://' + text
    if '://' not in text:
        return _repository_id(text), _revision(revision), None, False
    parsed = urlsplit(text)
    if parsed.scheme not in ('http', 'https') or parsed.netloc.lower() not in _HUB_HOSTS:
        raise ValueError('Enter a Hugging Face model URL or owner/repository.')
    parts = [unquote(part) for part in parsed.path.strip('/').split('/')]
    # A one-part legacy model ID can have the marker immediately after the ID.
    # A two-part repository ID is tried first, so e.g. owner/tree remains valid.
    for boundary in (2, 1):
        tail = parts[boundary:]
        if len(parts) < boundary or tail and tail[0] not in {'tree', 'blob', 'resolve'}:
            continue
        if tail and len(tail) == 1:
            continue
        repo = _repository_id('/'.join(parts[:boundary]))
        if not tail:
            return repo, _revision(revision), None, False
        kind, ref, *path_parts = tail
        if ref == 'refs' and len(path_parts) >= 2 and path_parts[0] in {'heads', 'tags', 'pr'}:
            ref = '/'.join([ref, *path_parts[:2]])
            path_parts = path_parts[2:]
        folder = kind == 'tree'
        path = '/'.join(path_parts) if path_parts else None
        if path:
            _parts(path)
        elif not folder:
            raise ValueError('The Hugging Face file URL is missing its filename.')
        return repo, _revision(ref), path, folder
    raise ValueError('Enter a Hugging Face model repository, tree, blob, or resolve URL.')


def parse_repository_reference(text, revision='main'):
    """Parse a Hub model ID or URL into repository ID, revision and optional path."""
    repo, ref, path, _ = _reference(text, revision)
    return repo, ref, path


def repository_id(text):
    return parse_repository_reference(text)[0]


def _parts(path):
    parts = path.split('/')
    for part in parts:
        if (not part or part in ('.', '..') or part.endswith(('.', ' '))
                or PureWindowsPath(part).is_reserved()
                or any(char in '<>:"\\|?*' or ord(char) < 32 for char in part)):
            raise ValueError('This model repository contains an unsupported filename.')
    return parts


def _hf_request(url, token='', offset=0):
    request = Request(url, headers={'Accept-Encoding': 'identity', 'User-Agent': 'TalkToAi-Code'})
    if token:
        token = token.strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,512}', token):
            raise ValueError('Enter a valid Hugging Face read token.')
        # Do not forward the Hub token to redirected CDN or object-storage hosts.
        request.add_unredirected_header('Authorization', 'Bearer ' + token)
    if offset:
        request.add_header('Range', f'bytes={offset}-')
    return request


def _hub_error(exc):
    if exc.code in (401, 403):
        return PermissionError('Hugging Face denied access. For a private or gated model, accept its terms and provide a read token.')
    if exc.code == 404:
        return FileNotFoundError('Hugging Face could not find that repository, revision or file.')
    if exc.code == 429:
        return ConnectionError('Hugging Face is rate limiting downloads. Retry in a few minutes.')
    return ConnectionError(f'Hugging Face returned HTTP {exc.code}.')


def repository_files(repository, revision='main', token=''):
    repository, revision, _, _ = _reference(repository, revision)
    url = f'{HF_URL}/api/models/{repository}/revision/{quote(revision, safe="")}?blobs=true'
    try:
        with urlopen(_hf_request(url, token), timeout=25) as response:
            raw = response.read(_LIMIT + 1)
    except HTTPError as exc:
        raise _hub_error(exc) from exc
    if len(raw) > _LIMIT:
        raise ValueError('The repository file listing is too large.')
    data = json.loads(raw)
    commit, siblings = (data.get('sha'), data.get('siblings')) if isinstance(data, dict) else (None, None)
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit) or not isinstance(siblings, list):
        raise ValueError('Hugging Face did not return a valid repository file list.')
    files = []
    for item in siblings:
        if not isinstance(item, dict):
            continue
        name, size = item.get('rfilename'), item.get('size')
        if not isinstance(name, str) or type(size) is not int or size < 0:
            continue
        try:
            _parts(name)
        except ValueError:
            continue
        lfs = item['lfs'] if isinstance(item.get('lfs'), dict) else {}
        sha = lfs.get('sha256') or ''
        git_sha1 = item.get('blobId') or ''
        files.append(ModelFile(name, size,
                               sha if isinstance(sha, str) and _SHA256.fullmatch(sha) else '',
                               git_sha1 if not lfs and isinstance(git_sha1, str) and _COMMIT.fullmatch(git_sha1) else ''))
    return commit, sorted(files, key=lambda item: (not item.name.lower().endswith('.gguf'), item.name.lower()))


def resolve_hf_selection(text, revision='main', token=''):
    """Resolve a pasted repository or file URL to an immutable downloadable revision."""
    repository, revision, path, folder = _reference(text, revision)
    commit, files = repository_files(repository, revision, token)
    if not path:
        return repository, commit, files, None
    if folder:
        matches = [file for file in files if file.name.startswith(path + '/')]
        if not matches:
            raise FileNotFoundError('The selected Hugging Face folder contains no downloadable files.')
        return repository, commit, matches, None
    if not any(file.name == path for file in files):
        raise FileNotFoundError('The file in this Hugging Face link was not found in the resolved revision.')
    return repository, commit, files, path


def selected_files(files, selected):
    """Expand a split GGUF selection so the full quantization is available."""
    names = {item.name for item in selected}
    for item in selected:
        shard = _SHARD.fullmatch(item.name)
        if not shard:
            continue
        stem, _, count = shard.groups()
        if not 1 <= int(count) <= len(files):
            raise ValueError('A split GGUF model is missing shards in this revision.')
        expected = {f'{stem}-{index:05d}-of-{count}{item.name[-5:]}' for index in range(1, int(count) + 1)}
        if not expected.issubset(file.name for file in files):
            raise ValueError('A split GGUF model is missing shards in this revision.')
        names.update(expected)
    return [item for item in files if item.name in names]


def gguf_selection(files):
    if len(files) == 1 and files[0].name.lower().endswith('.gguf'):
        return not _SHARD.fullmatch(files[0].name)
    shards = [_SHARD.fullmatch(file.name) for file in files]
    return (bool(shards) and all(shards)
            and len({(match[1], match[3]) for match in shards}) == 1
            and {int(match[2]) for match in shards} == set(range(1, len(files) + 1))
            and int(shards[0][3]) == len(files))


def _disk_check(folder, needed):
    probe = folder
    while not probe.exists():
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if needed and free < needed + 128 * 1024**2:
        raise OSError(f'Insufficient disk space at {probe}. Need {format_size(needed)} plus working space; {format_size(free)} is free.')


def _size(path):
    return path.stat().st_size if path.is_file() else 0


def _file_digest(file):
    if file.sha256:
        return hashlib.sha256(), file.sha256
    if file.git_sha1:
        return hashlib.sha1(f'blob {file.size}\0'.encode()), file.git_sha1
    return None, ''


def _hash_existing(path, digest, cancel):
    if digest is None:
        return
    with path.open('rb') as handle:
        while data := handle.read(_CHUNK):
            _check_cancel(cancel)
            digest.update(data)


def _verify_gguf(path, name):
    if not name.lower().endswith('.gguf'):
        return
    with path.open('rb') as handle:
        if handle.read(4) != b'GGUF':
            raise ValueError(f'{name} is not a GGUF model file.')


def _download(url, file, target, part, token, cancel, notify):
    _check_cancel(cancel)
    if target.is_file() and _size(target) == file.size:
        digest, expected = _file_digest(file)
        notify('Checking ' + file.name, 0, 0)
        _hash_existing(target, digest, cancel)
        if digest is None or digest.hexdigest() == expected:
            _verify_gguf(target, file.name)
            notify('Verified ' + file.name, file.size, file.size)
            return
    offset = _size(part)
    if offset > file.size:
        part.unlink()
        offset = 0
    _disk_check(part.parent, file.size - offset)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest, expected = _file_digest(file)
    if offset:
        notify('Checking partial download of ' + file.name, 0, 0)
        _hash_existing(part, digest, cancel)
    if offset < file.size or not part.is_file():
        try:
            response = urlopen(_hf_request(url, token, offset), timeout=25)
        except HTTPError as exc:
            raise _hub_error(exc) from exc
        with response:
            if response.status == 206:
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                if offset == 0 or not match or tuple(map(int, match.groups())) != (offset, file.size - 1, file.size):
                    raise ValueError('Hugging Face returned an invalid byte range. Retry the download.')
            elif response.status == 200:
                if offset:
                    _disk_check(part.parent, file.size - offset)
                    offset = 0
                    digest, expected = _file_digest(file)
            else:
                raise ConnectionError('Hugging Face did not return the requested file.')
            with part.open('ab' if offset else 'wb') as handle:
                notify('Downloading ' + file.name, offset, file.size)
                last_update = 0.0
                while offset < file.size:
                    _check_cancel(cancel)
                    data = response.read(min(_CHUNK, file.size - offset + 1))
                    if not data:
                        break
                    if offset + len(data) > file.size:
                        raise ValueError('Hugging Face returned more bytes than listed for this file.')
                    handle.write(data)
                    offset += len(data)
                    if digest is not None:
                        digest.update(data)
                    if time.monotonic() - last_update >= .25:
                        notify('Downloading ' + file.name, offset, file.size)
                        last_update = time.monotonic()
    _check_cancel(cancel)
    if offset != file.size:
        raise ConnectionError('The download ended early. Retry to resume the partial file.')
    if digest is not None and digest.hexdigest() != expected:
        part.unlink()
        raise ValueError('The downloaded file failed its checksum. Retry to download a fresh copy.')
    try:
        _verify_gguf(part, file.name)
    except ValueError:
        part.unlink()
        raise
    part.replace(target)
    notify('Verified ' + file.name if digest is not None else 'Saved ' + file.name, file.size, file.size)


def download_files(repository, commit, files, root, token, cancel, notify):
    """Download immutable repository files, returning the folder and verified paths."""
    repository = repository_id(repository)
    if not _COMMIT.fullmatch(commit) or not files:
        raise ValueError('Select files from a resolved Hugging Face revision first.')
    root = Path(root).resolve()
    target_root = root.joinpath(*_parts(repository), commit)
    work_root = root / '.partial' / hashlib.sha256(f'{repository}@{commit}'.encode()).hexdigest()
    targets = []
    names = set()
    for file in files:
        target = target_root.joinpath(*_parts(file.name))
        canonical = target.resolve()
        if not canonical.is_relative_to(root) or canonical != target or str(canonical).casefold() in names:
            raise ValueError('The selected downloads have unsafe or overlapping local paths.')
        names.add(str(canonical).casefold())
        part = work_root / hashlib.sha256(file.name.encode()).hexdigest()
        if part.resolve() != part:
            raise ValueError('The model download directory contains an unexpected link.')
        targets.append((file, target, part))
    with _FILE_LOCK:
        needed = sum(max(0, file.size - _size(part)) for file, target, part in targets if _size(target) != file.size)
        _disk_check(root, needed)
        work_root.mkdir(parents=True, exist_ok=True)
        for file, target, part in targets:
            _check_cancel(cancel)
            url = f'{HF_URL}/{repository}/resolve/{commit}/{quote(file.name)}?download=true'
            _download(url, file, target, part, token, cancel, notify)
    return target_root, [target for _, target, _ in targets]


def ollama_store():
    configured = os.environ.get('OLLAMA_MODELS')
    if not configured and os.name == 'nt':
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                configured = winreg.QueryValueEx(key, 'OLLAMA_MODELS')[0]
        except OSError:
            pass
    return Path(os.path.expandvars(configured)).expanduser() if configured else Path.home() / '.ollama' / 'models'


def _registry_layers(model):
    if model == QWEN_LOCAL_MODEL:
        registry = 'https://registry.ollama.ai/v2/library/qwen3.6/manifests/35b'
    else:
        registry = f'{HF_URL}/v2/{OPENZERO_REPO}/manifests/Q4_K_M'
    request = Request(registry, headers={'Accept': 'application/vnd.docker.distribution.manifest.v2+json'})
    with urlopen(request, timeout=15) as response:
        data = json.loads(response.read(128 * 1024))
    return data['layers']


def _preflight_pull(model, *, local_disk=True):
    if model not in (DEFAULT_LOCAL_MODEL, QWEN_LOCAL_MODEL):
        return
    root = ollama_store() if local_disk else None
    try:
        layers = _registry_layers(model)
        required = sum(
            max(0, layer['size'] - _size(root / 'blobs' / layer['digest'].replace(':', '-')))
            for layer in layers if re.fullmatch(r'sha256:[a-f0-9]{64}', layer['digest'])
        ) if local_disk else 0
    except (OSError, ValueError, KeyError, TypeError):
        layers = []
        required = QWEN_OLLAMA_ESTIMATE if model == QWEN_LOCAL_MODEL else OPENZERO_FILE.size
    if layers and model == DEFAULT_LOCAL_MODEL and not any(
        layer.get('digest') == 'sha256:' + OPENZERO_FILE.sha256 and layer.get('size') == OPENZERO_FILE.size
        for layer in layers
    ):
        raise ValueError('The OpenZero registry artifact no longer matches the selected weights. Download the pinned GGUF from Model downloads.')
    if local_disk:
        _disk_check(root, required)


def _ollama_updates(path, payload, cancel, deadline, *, endpoint=None):
    address = urlsplit(endpoint or OLLAMA_URL)
    if (address.scheme != 'http' or address.hostname not in ('127.0.0.1', 'localhost', '::1')
            or address.username or address.password or address.path or address.query or address.fragment):
        raise ValueError('Model installation requires a local Ollama endpoint or loopback SSH tunnel.')
    conn = HTTPConnection(address.hostname, address.port, timeout=10)
    pending = Queue(maxsize=8)
    finished = threading.Event()
    transport = None

    def publish(value):
        while not finished.is_set():
            try:
                pending.put(value, timeout=.1)
                return
            except Full:
                pass

    def read():
        nonlocal transport
        idle_seconds = 300
        try:
            conn.connect()
            wire = conn.sock
            wire.settimeout(idle_seconds)
            transport = wire.dup()
            if finished.is_set():
                return
            conn.request('POST', path, json.dumps(payload).encode(), {'Content-Type': 'application/json'})
            with conn.getresponse() as response:
                if response.status >= 400:
                    try:
                        detail = json.loads(response.read(8192)).get('error') or f'HTTP {response.status}'
                    except (ValueError, AttributeError):
                        detail = f'HTTP {response.status}'
                    raise RuntimeError('Ollama: ' + str(detail)[:1200])
                while not finished.is_set():
                    line = response.readline(65537)
                    if not line:
                        break
                    if len(line) > 65536:
                        raise ValueError('Ollama returned an oversized progress update.')
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        raise ValueError('Ollama returned an invalid progress update.')
                    # Hashing all the weights runs without progress frames.
                    next_idle = 900 if str(item.get('status', '')).lower().startswith('verifying sha256') else 300
                    if next_idle != idle_seconds:
                        wire.settimeout(next_idle)
                        idle_seconds = next_idle
                    publish(item)
            publish(None)
        except TimeoutError:
            publish(ConnectionError('Ollama stopped sending progress. Retry to resume.'))
        except (OSError, HTTPException, RuntimeError, ValueError) as exc:
            publish(exc)
        finally:
            conn.close()
            if transport:
                transport.close()

    threading.Thread(target=read, daemon=True).start()
    try:
        while time.monotonic() < deadline:
            _check_cancel(cancel)
            try:
                item = pending.get(timeout=.1)
            except Empty:
                continue
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
        raise TimeoutError('Ollama did not finish within the model installation deadline.')
    finally:
        finished.set()
        if transport:
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _ollama_progress(path, payload, cancel, notify, deadline, *, endpoint=None):
    updates = _ollama_updates(path, payload, cancel, deadline, endpoint=endpoint)
    layers = {}
    last_update = 0.0
    previous_status = None
    try:
        for item in updates:
            if item.get('error'):
                raise RuntimeError(str(item['error'])[:1200])
            status = str(item.get('status') or 'Working')[:160]
            total, completed = item.get('total'), item.get('completed')
            if item.get('digest') and type(total) is int and type(completed) is int:
                layers[item['digest']] = (completed, total)
            if status != previous_status or time.monotonic() - last_update >= .25:
                notify(status.capitalize(), sum(v[0] for v in layers.values()), sum(v[1] for v in layers.values()))
                last_update, previous_status = time.monotonic(), status
            if status == 'success':
                _check_cancel(cancel)
                return
    finally:
        updates.close()
    _check_cancel(cancel)
    raise ConnectionError('Ollama stopped responding before it confirmed completion. Retry to resume.')


def pull_model(model, cancel, notify, timeout=7200, *, endpoint=None):
    """Pull through the local service so Ollama verifies its own content digests."""
    _check_cancel(cancel)
    notify('Checking local model storage' if endpoint is None else 'Preparing model download on the selected server', 0, 0)
    if endpoint is None:
        _preflight_pull(model)
    else:
        _preflight_pull(model, local_disk=False)
    _check_cancel(cancel)
    _ollama_progress('/api/pull', {'model': model, 'name': model, 'stream': True},
                     cancel, notify, time.monotonic() + timeout, endpoint=endpoint)


class _BlobReader:
    def __init__(self, stream, size, cancel, notify):
        self.stream, self.size, self.cancel, self.notify = stream, size, cancel, notify
        self.completed, self.updated = 0, 0.0

    def read(self, size):
        _check_cancel(self.cancel)
        data = self.stream.read(size)
        self.completed += len(data)
        if time.monotonic() - self.updated >= .25:
            self.notify('Copying verified GGUF into Ollama', self.completed, self.size)
            self.updated = time.monotonic()
        return data


def import_gguf(paths, cancel, notify):
    """Register one downloaded GGUF or one complete split quant with local Ollama."""
    paths = [Path(path) for path in paths]
    if not gguf_selection([ModelFile(path.name, path.stat().st_size) for path in paths]):
        raise ValueError('Select one GGUF model or one complete group of split GGUF files.')
    entries = {}
    for path in paths:
        _check_cancel(cancel)
        _verify_gguf(path, path.name)
        digest = hashlib.sha256()
        notify('Preparing ' + path.name, 0, 0)
        _hash_existing(path, digest, cancel)
        entries[path.name] = 'sha256:' + digest.hexdigest()
    signature = hashlib.sha256(''.join(sorted(entries.values())).encode()).hexdigest()[:12]
    stem = re.sub(r'[^a-z0-9]+', '-', paths[0].stem.lower()).strip('-')[:54] or 'model'
    model = f'talktoai-{stem}:{signature}'
    missing = []
    for path in paths:
        _check_cancel(cancel)
        url = OLLAMA_URL + '/api/blobs/' + entries[path.name]
        try:
            with _LOCAL_HTTP.open(Request(url, method='HEAD'), timeout=10):
                pass
        except HTTPError as exc:
            if exc.code != 404:
                raise
            missing.append(path)
    _disk_check(ollama_store(), sum(path.stat().st_size for path in missing))
    for path in missing:
        size = path.stat().st_size
        with path.open('rb') as handle:
            body = _BlobReader(handle, size, cancel, notify)
            request = Request(OLLAMA_URL + '/api/blobs/' + entries[path.name], data=body, method='POST',
                              headers={'Content-Length': str(size), 'Content-Type': 'application/octet-stream'})
            with _LOCAL_HTTP.open(request, timeout=30):
                pass
    _ollama_progress('/api/create', {'model': model, 'files': entries, 'stream': True},
                     cancel, notify, time.monotonic() + 3600)
    return model
