"""Atomic chat persistence with a validated previous-save recovery copy."""
import json
import os
import tempfile
import uuid
from pathlib import Path


def decode_tasks(raw):
    tasks = json.loads(raw)
    if not isinstance(tasks, list):
        raise ValueError('Chat history must be a list')
    ids = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError('Invalid chat record')
        for field in ('id', 'title', 'project'):
            if not isinstance(task.get(field), str) or not task[field]:
                raise ValueError('Invalid chat ' + field)
        if task['id'] in ids:
            raise ValueError('Duplicate chat identity')
        ids.add(task['id'])
        for field in ('messages', 'changes', 'artifacts', 'activity', 'jobs'):
            if not isinstance(task.get(field, []), list):
                raise ValueError('Invalid chat ' + field)
        for message in task.get('messages', []):
            if (not isinstance(message, dict) or
                    message.get('role') not in ('user', 'assistant', 'tool', 'system') or
                    not isinstance(message.get('content', ''), (str, type(None)))):
                raise ValueError('Invalid chat message')
        for change in task.get('changes', []):
            if not isinstance(change, dict) or not all(isinstance(change.get(k), str) for k in ('path', 'checkpoint', 'diff')):
                raise ValueError('Invalid chat file change')
        for artifact in task.get('artifacts', []):
            if not isinstance(artifact, dict) or not isinstance(artifact.get('artifact'), str):
                raise ValueError('Invalid chat artifact')
        for job in task.get('jobs', []):
            if not isinstance(job, dict) or not isinstance(job.get('id'), str) or not isinstance(job.get('state'), str):
                raise ValueError('Invalid chat job')
            if not isinstance(job.get('command', []), list) or not all(isinstance(a,str) for a in job.get('command', [])):
                raise ValueError('Invalid chat job command')
        if not all(isinstance(line, str) for line in task.get('activity', [])):
            raise ValueError('Invalid chat activity')
        if not isinstance(task.get('draft', ''), str):
            raise ValueError('Invalid chat draft')
        for field in ('messages', 'changes'):
            task.setdefault(field, [])
        task.setdefault('pinned', False)
        task.setdefault('archived', False)
    return tasks


def atomic_write(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def load_tasks(path):
    """Never discard unreadable history: retain it under a unique .corrupt name."""
    path = Path(path)
    notices = []
    for candidate in (path, path.with_suffix('.json.bak')):
        try:
            raw = candidate.read_bytes()
        except FileNotFoundError:
            continue
        try:
            tasks = decode_tasks(raw)
        except (ValueError, UnicodeError):
            preserved = candidate.with_name(candidate.name + '.corrupt-' + uuid.uuid4().hex[:12])
            candidate.replace(preserved)
            notices.append('Unreadable history preserved as ' + preserved.name + '.')
            continue
        if candidate != path:
            atomic_write(path, raw)
            notices.append('Chats recovered from the previous successful save.')
        return tasks, ' '.join(notices)
    if notices:
        notices.append('No valid recovery copy was available. Original files were retained in the app data folder.')
    return [], ' '.join(notices)


def save_tasks(path, tasks):
    path = Path(path)
    raw = json.dumps(tasks, ensure_ascii=False, indent=2).encode('utf-8')
    decode_tasks(raw)  # Validate before touching either saved copy.
    try:
        previous = path.read_bytes()
    except FileNotFoundError:
        previous = None
    if previous is not None:
        decode_tasks(previous)  # Never rotate a damaged file over a good backup.
        if previous == raw:
            return
        atomic_write(path.with_suffix('.json.bak'), previous)
    atomic_write(path, raw)


def matches_task(task, query):
    text = '\n'.join([task.get('title', ''), task.get('project', ''), task.get('draft', '')] +
                     [m.get('content') or '' for m in task.get('messages', []) if m.get('role') in ('user', 'assistant')]).casefold()
    return all(word in text for word in query.casefold().split())
