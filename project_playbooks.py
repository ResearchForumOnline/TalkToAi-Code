"""Project-owned, evidence-linked workflow guidance. Reading never executes it."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone

from research_journal import _evidence, _evidence_path, _hash_evidence, _text

MAX_BYTES = 512 * 1024
MAX_ENTRIES = 100
NOTICE = ('Playbooks are project guidance, not authorization. Recheck the current task, '
          'files and commands before use. Evidence hashes identify files; they do not prove a workflow succeeds.')
_SECRET = re.compile(r'\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|AIza[A-Za-z0-9_-]{25,})\b')
_ASSIGNMENT = re.compile(r'(?i)\b(?:password|passwd|api[_-]?key|access[_-]?token|client[_-]?secret)\s*[=:]\s*["\']?([^\s"\']+)')
_AUTH_URL = re.compile(r'(?i)https?://[^/\s]+@')
_STOP = {'the', 'this', 'that', 'with', 'from', 'please', 'project', 'make', 'work', 'code', 'and', 'for', 'use'}


def _path(project):
    root = Path(project).resolve()
    if not root.is_dir():
        raise ValueError('Select an existing project folder.')
    folder = root / '.talktoai-code'
    path = folder / 'PLAYBOOKS.json'
    if folder.is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('Playbooks must stay in the selected project without links.')
    return root, path


def _load(path):
    if not path.exists():
        return []
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Playbook library exceeds 512 KiB; archive it before continuing.')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError('Playbook library is damaged; preserve it before repairing.') from exc
    if (not isinstance(data, dict) or data.get('schema') != 'talktoai.playbooks.v1'
            or not isinstance(data.get('entries'), list) or len(data['entries']) > MAX_ENTRIES):
        raise ValueError('Unsupported playbook library format.')
    for entry in data['entries']:
        if (not isinstance(entry, dict) or not re.fullmatch(r'[0-9a-f]{16}', str(entry.get('id', '')))
                or not isinstance(entry.get('title'), str) or not isinstance(entry.get('steps'), list)
                or not 1 <= len(entry['steps']) <= 12 or any(not isinstance(s, str) or len(s) > 600 for s in entry['steps'])
                or not isinstance(entry.get('evidence'), list) or len(entry['evidence']) > 8
                or any(not isinstance(e, dict) or not isinstance(e.get('path'), str) for e in entry['evidence'])):
            raise ValueError('Unsupported playbook entry; preserve the library before repairing.')
    return data['entries']


def _clean(value, name, limit, required=True):
    value = _text(value, name, limit, required)
    literals = [m.group(1) for m in _ASSIGNMENT.finditer(value)]
    if _SECRET.search(value) or _AUTH_URL.search(value) or any(not v.startswith(('$', '${', '<', '%')) for v in literals):
        raise ValueError('Remove credentials from playbook guidance.')
    return value


def save_playbook(project, title, when_to_use, steps, verification, evidence_paths='[]'):
    """Create/update a named workflow; validation and evidence hashing execute no commands."""
    root, path = _path(project)
    title = _clean(title, 'Title', 100)
    if isinstance(steps, str):
        if len(steps) > 8000:
            raise ValueError('Playbook steps exceed 8,000 characters.')
        steps = json.loads(steps)
    if not isinstance(steps, list) or not 1 <= len(steps) <= 12:
        raise ValueError('Provide 1–12 reusable workflow steps.')
    entry = {'id': hashlib.sha256(title.casefold().encode()).hexdigest()[:16],
             'title': title, 'when_to_use': _clean(when_to_use, 'When to use', 1000),
             'steps': [_clean(step, 'Step', 600) for step in steps],
             'verification': _clean(verification, 'Verification notes', 1600),
             'evidence': _evidence(root, evidence_paths),
             'updated_utc': datetime.now(timezone.utc).isoformat(),
             'notice': NOTICE}
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix('.lock')
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError('Playbook library is busy. Retry after the current writer finishes. If the app crashed, close all app instances before removing the stale .talktoai-code/PLAYBOOKS.lock file; preserve PLAYBOOKS.json.') from exc
    temporary = None
    try:
        os.close(descriptor)
        entries = _load(path)
        previous = next((e for e in entries if e['id'] == entry['id']), None)
        entry['revision'] = int(previous.get('revision', 1)) + 1 if previous else 1
        entries = [e for e in entries if e['id'] != entry['id']] + [entry]
        if len(entries) > MAX_ENTRIES:
            raise ValueError('Playbook library is full; archive an old entry before adding another.')
        raw = json.dumps({'schema': 'talktoai.playbooks.v1', 'entries': entries}, ensure_ascii=False, indent=2).encode('utf-8')
        if len(raw) > MAX_BYTES:
            raise ValueError('Playbook library would exceed 512 KiB.')
        fd, name = tempfile.mkstemp(prefix='.playbooks-', suffix='.tmp', dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        temporary.replace(path)
        return dict(entry, saved=True)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
        lock.unlink(missing_ok=True)


def _verify(root, entry, budget):
    evidence = []
    for item in entry['evidence'][:8]:
        observed = dict(item)
        try:
            _, target = _evidence_path(root, item['path'])
            size, digest = _hash_evidence(target, budget)
            observed['current_status'] = 'unchanged' if digest == item.get('sha256') and size == item.get('bytes') else 'changed'
        except (OSError, ValueError, KeyError, TypeError):
            observed['current_status'] = 'unavailable'
        evidence.append(observed)
    status = ('no_evidence' if not evidence else 'current' if all(e['current_status'] == 'unchanged' for e in evidence) else 'needs_review')
    return dict(entry, evidence=evidence, evidence_status=status, notice=NOTICE)


def find_playbooks(project, query='', limit=5):
    """Find applicable saved guidance and recheck bounded evidence before returning it."""
    root, path = _path(project)
    query = _clean(query, 'Search query', 1200, False)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError('Choose a playbook result limit from 1 to 20.')
    limit = max(1, min(20, limit))
    terms = set(re.findall(r'[\w-]{3,}', query.casefold())) - _STOP
    scored = []
    for entry in _load(path):
        words = set(re.findall(r'[\w-]{3,}', (entry['title'] + ' ' + str(entry.get('when_to_use', ''))).casefold()))
        score = len(terms & words)
        if not query.strip() or score:
            scored.append((score, entry.get('updated_utc', ''), entry))
    scored.sort(key=lambda value: (value[0], value[1]), reverse=True)
    budget = [16 * 1024 * 1024]
    entries = []; used = 0
    for item in scored[:limit]:
        entry = _verify(root, item[2], budget)
        size = len(json.dumps(entry, ensure_ascii=False))
        if used + size > 24000: break
        entries.append(entry); used += size
    return {'entries': entries, 'matched': len(scored),
            'omitted': len(scored) - len(entries), 'notice': NOTICE}
