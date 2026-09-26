"""Bounded project experiment records; recording never executes an experiment."""
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import uuid
from datetime import datetime, timezone

MAX_BYTES = 1024 * 1024
MAX_ENTRIES = 200
MAX_ENTRY_BYTES = 12000
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
PRIVATE_PARTS = {'.git', '.ssh', '.aws', '.azure', 'private', 'secrets', 'credentials'}
PRIVATE_NAMES = {'credentials.json', 'token.json', 'tokens.json', 'config.json', 'providers.json', 'studio.json', 'connections.json', 'id_rsa', 'id_ed25519'}


def _journal(project):
    root = Path(project).resolve()
    if not root.is_dir():
        raise ValueError('Select an existing project folder.')
    directory = root / '.talktoai-code'
    path = directory / 'EXPERIMENTS.jsonl'
    if directory.is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('Experiment journal must stay inside the selected project without links.')
    return root, path


def _entries(path):
    if not path.exists():
        return []
    with path.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Experiment journal exceeds 1 MiB. Archive it manually before continuing.')
    try:
        entries = [json.loads(line) for line in data.decode('utf-8').splitlines() if line.strip()]
    except (ValueError, UnicodeError) as exc:
        raise ValueError('Experiment journal is damaged; preserve and repair it before appending.') from exc
    if len(entries) > MAX_ENTRIES or any(not isinstance(e, dict) or e.get('schema') != 'talktoai.experiment.v1' for e in entries):
        raise ValueError('Experiment journal has an unsupported format or too many entries.')
    return entries


def _text(value, name, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f'{name} must be text of at most {limit} characters' + (' and nonempty.' if required else '.'))
    return value.strip()


def _metrics(value):
    if isinstance(value, str) and len(value) > 4000:
        raise ValueError('Metric JSON exceeds 4,000 characters.')
    value = json.loads(value) if isinstance(value, str) else value
    if not isinstance(value, dict) or len(value) > 12:
        raise ValueError('metrics must be an object with at most 12 numeric measurements.')
    result = {}
    for name, number in value.items():
        _text(name, 'Metric name', 80, True)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            raise ValueError('Metric values must be finite numbers. Describe units in the metric name.')
        result[name] = number
    return result


def _evidence(root, names):
    if isinstance(names, str) and len(names) > 4000:
        raise ValueError('Evidence path JSON exceeds 4,000 characters.')
    names = json.loads(names) if isinstance(names, str) else names
    if not isinstance(names, list) or len(names) > 8:
        raise ValueError('Provide at most 8 project-relative evidence file paths.')
    evidence = []
    for name in names:
        _text(name, 'Evidence path', 300, True)
        relative = Path(name)
        parts = {p.casefold() for p in relative.parts}
        base = relative.name.casefold()
        if relative.is_absolute() or '..' in relative.parts or parts & PRIVATE_PARTS or base in PRIVATE_NAMES or base.startswith('.env') or any(word in base for word in ('password', 'credential', 'private_key', 'secret')) or relative.suffix.casefold() in {'.pem', '.key', '.pfx', '.dpapi'}:
            raise ValueError('Use a non-sensitive evidence file inside the project.')
        path = root / relative
        if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in (path, *list(path.parents)[:len(relative.parts)])):
            raise ValueError('Evidence files must stay in the project without links.')
        if not path.is_file() or path.stat().st_size > MAX_EVIDENCE_BYTES:
            raise ValueError('Evidence must be an existing file of at most 4 MiB.')
        digest = hashlib.sha256(); size = 0
        with path.open('rb') as stream:
            while chunk := stream.read(65536):
                size += len(chunk)
                if size > MAX_EVIDENCE_BYTES:
                    raise ValueError('Evidence grew beyond the 4 MiB limit.')
                digest.update(chunk)
        evidence.append({'path': relative.as_posix(), 'bytes': size, 'sha256': digest.hexdigest(), 'status': 'file_bytes_hashed'})
    return evidence


def record_experiment(project, hypothesis, command, result, metrics='{}', evidence_paths='[]', next_step=''):
    root, path = _journal(project)
    entry = {'schema': 'talktoai.experiment.v1', 'id': uuid.uuid4().hex, 'created_utc': datetime.now(timezone.utc).isoformat(),
             'hypothesis': _text(hypothesis, 'Hypothesis', 1600, True),
             'reported_command': _text(command, 'Command', 1600),
             'reported_result': _text(result, 'Result', 2400, True),
             'reported_metrics': _metrics(metrics), 'evidence': _evidence(root, evidence_paths),
             'next_step': _text(next_step, 'Next step', 1000),
             'verification': 'Claims and metrics are reported, not independently verified. Evidence hashes establish file bytes only. No command was executed by this tool.'}
    encoded = (json.dumps(entry, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    if len(encoded) > MAX_ENTRY_BYTES:
        raise ValueError('Experiment entry exceeds the 12 KB limit.')
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix('.lock')
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError('Experiment journal is busy. Retry later; recover a stale lock only after confirming no writer is active.') from exc
    temporary = None
    try:
        os.close(descriptor)
        previous = _entries(path)
        if len(previous) >= MAX_ENTRIES:
            raise ValueError('Experiment journal reached 200 entries. Archive it manually before continuing.')
        entry['sequence'] = len(previous) + 1
        if len(json.dumps(entry, ensure_ascii=False).encode('utf-8')) > MAX_ENTRY_BYTES:
            raise ValueError('Experiment entry exceeds the 12 KB limit.')
        data = ''.join(json.dumps(item, ensure_ascii=False, allow_nan=False) + '\n' for item in previous + [entry]).encode('utf-8')
        if len(data) > MAX_BYTES:
            raise ValueError('Experiment journal reached 1 MiB. Archive it manually before continuing.')
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='experiments-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        return json.dumps({'id': entry['id'], 'sequence': entry['sequence'], 'journal': '.talktoai-code/EXPERIMENTS.jsonl', 'evidence_files': len(entry['evidence']), 'verification': entry['verification']})
    finally:
        if temporary and temporary.exists(): temporary.unlink()
        lock.unlink(missing_ok=True)


def read_experiments(project, limit='5'):
    _, path = _journal(project)
    limit = int(limit)
    if not 1 <= limit <= 20:
        raise ValueError('Read 1–20 recent experiment entries.')
    entries = _entries(path)
    selected = []; size = 0
    for entry in reversed(entries[-limit:]):
        item = json.dumps(entry, ensure_ascii=False)
        if size + len(item) > 16000:
            break
        selected.insert(0, entry); size += len(item)
    return json.dumps({'total_entries': len(entries), 'entries': selected, 'omitted_entries': len(entries) - len(selected),
                       'note': 'Historical reported claims; file hashes are not proof of scientific correctness. Recheck current evidence before continuing.'}, ensure_ascii=False)
