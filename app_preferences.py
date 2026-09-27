"""Audited, reversible changes to a small set of non-secret app preferences.

The caller owns the live config mapping and its persistence callback. This module
does not load the complete config file or expose credentials to an agent. It does
not change permissions, connections, providers, model IDs, or model weights.
"""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import uuid


_PREFERENCES = {
    'auto_context': ('Automatically include project discovery tools', True, (False, True)),
    'show_tool_activity': ('Show tool activity in the Tools panel', True, (False, True)),
    'show_model_activity': ('Show available model activity', False, (False, True)),
    'keep_going': ('Default Keep going for new Code tasks', True, (False, True)),
    'work_session_minutes': ('Maximum Keep going duration for future requests', 120, (60, 120, 240)),
    'num_ctx': ('Context size requested for future model calls', 8192, (8192, 16384, 32768)),
    'web_browser': ('Preferred research browser', 'auto', ('auto', 'edge', 'chrome', 'firefox', 'chromium')),
    'web_search': ('Preferred free browser search', 'auto', ('auto', 'duckduckgo', 'bing', 'google', 'brave')),
    'skynet_iterations': ('Default candidate iterations in Skynet Mode', 2, (1, 2, 3, 4, 5)),
}
_MAX_AUDIT_BYTES = 2 * 1024 * 1024
_MAX_RECORD_BYTES = 4096
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[Path, threading.RLock] = {}


def _lock_for(path: Path) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(path, threading.RLock())


def _checked_value(key: str, value: object) -> object:
    if key not in _PREFERENCES:
        raise ValueError('This app setting cannot be changed by the agent: ' + str(key)[:80])
    _, _, allowed = _PREFERENCES[key]
    if not any(type(value) is type(candidate) and value == candidate for candidate in allowed):
        raise ValueError(f'{key} must be one of {list(allowed)!r}')
    return value


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class AppPreferenceManager:
    """Use a live config mapping and the app's existing atomic persist operation.

    Instantiate once per application instance. The audit contains only allowlisted
    preference values. A prepared record without a commit is not a completed edit.
    """

    def __init__(self, config: MutableMapping[str, object], persist: Callable[[], None], audit_path: str | Path):
        if not isinstance(config, MutableMapping) or not callable(persist):
            raise TypeError('A live config mapping and persistence callback are required')
        self.config = config
        self.persist = persist
        self.audit_path = Path(audit_path).absolute()
        self.lock = _lock_for(self.audit_path)

    def inspect(self) -> dict:
        with self.lock:
            records = self._read_audit()
            committed = self._committed(records)
            return {
                'preferences': {key: self._safe_current(key) for key in _PREFERENCES},
                'schema': {key: {'description': spec[0], 'allowed': list(spec[2])} for key, spec in _PREFERENCES.items()},
                'latest_change_id': committed[-1]['id'] if committed else None,
                'pending_audit_records': sum(record['state'] == 'prepared' for record in records)
                - sum(record['state'] in ('committed', 'aborted') for record in records),
                'applies': 'Future requests and new tasks; an active model call retains its starting settings.',
                'excluded': 'Permission, connection, credential, paid search, model ID, download and weight settings.',
            }

    def history(self, limit: int = 10) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('limit must be 1-50')
        with self.lock:
            return self._committed(self._read_audit())[-limit:]

    def change(self, changes: dict[str, object]) -> dict:
        if not isinstance(changes, dict) or not 1 <= len(changes) <= len(_PREFERENCES):
            raise ValueError('changes must be a nonempty object of allowed app preferences')
        checked = {key: _checked_value(key, value) for key, value in changes.items()}
        with self.lock:
            self._read_audit()
            before = {key: {'present': key in self.config,
                            'value': self.config.get(key, _PREFERENCES[key][1])} for key in checked}
            for key, old in before.items():
                try:
                    _checked_value(key, old['value'])
                except ValueError as error:
                    raise ValueError(f'{key} has an unsupported saved value; correct it in Settings before an audited agent change') from error
            actual = {key: value for key, value in checked.items() if before[key]['value'] != value}
            if not actual:
                return {'changed': False, 'preferences': {key: before[key]['value'] for key in checked},
                        'message': 'These preferences already have the requested values.'}
            return self._apply(actual, {key: before[key] for key in actual})

    def rollback(self, change_id: str) -> dict:
        if not isinstance(change_id, str) or len(change_id) != 32 or any(c not in '0123456789abcdef' for c in change_id):
            raise ValueError('Supply a change ID from preference history')
        with self.lock:
            committed = self._committed(self._read_audit())
            matches = [entry for entry in committed if entry['id'] == change_id]
            if not matches:
                raise ValueError('Preference change was not found or was not committed')
            if any(entry.get('rollback_of') == change_id for entry in committed):
                raise ValueError('This preference change has already been rolled back')
            original = matches[0]
            later = committed[committed.index(original) + 1:]
            if any(set(entry['after']) & set(original['after']) for entry in later):
                raise ValueError('A later audited change touched the same preference; inspect history before rollback')
            for key, after in original['after'].items():
                if self.config.get(key, _PREFERENCES[key][1]) != after:
                    raise ValueError(f'{key} changed since this audit entry; inspect current settings before rollback')
            target = {key: original['before'][key]['value'] for key in original['after']}
            current = {key: {'present': key in self.config, 'value': self.config.get(key, _PREFERENCES[key][1])}
                       for key in target}
            return self._apply(target, current, rollback_of=change_id,
                               restore_missing={key for key in target if not original['before'][key]['present']})

    def _apply(self, after: dict, before: dict, rollback_of: str | None = None,
               restore_missing: set[str] | None = None) -> dict:
        change_id = uuid.uuid4().hex
        record = {'id': change_id, 'at': _stamp(), 'state': 'prepared',
                  'before': before, 'after': after}
        if rollback_of:
            record['rollback_of'] = rollback_of
        self._append(record)
        try:
            for key, value in after.items():
                if restore_missing and key in restore_missing:
                    self.config.pop(key, None)
                else:
                    self.config[key] = value
            self.persist()
            self._append({'id': change_id, 'at': _stamp(), 'state': 'committed'})
        except Exception:
            for key, old in before.items():
                if old['present']:
                    self.config[key] = old['value']
                else:
                    self.config.pop(key, None)
            try:
                self.persist()
            finally:
                try:
                    self._append({'id': change_id, 'at': _stamp(), 'state': 'aborted'})
                except (OSError, ValueError):
                    pass
            raise
        return {'changed': True, 'change_id': change_id,
                'changes': {key: {'before': before[key]['value'], 'after': after[key]} for key in after},
                'rollback_of': rollback_of,
                'applies': 'Future requests and new tasks; current model call settings are unchanged.'}

    def _safe_current(self, key: str) -> object:
        current = self.config.get(key, _PREFERENCES[key][1])
        try:
            return _checked_value(key, current)
        except ValueError:
            return '(unsupported saved value; open Settings)'

    def _checked_audit_path(self) -> None:
        parent = self.audit_path.parent
        if not parent.is_dir() or parent.is_symlink() or self.audit_path.is_symlink():
            raise ValueError('Preference audit location must be a real existing app data directory')
        if self.audit_path.exists() and not self.audit_path.is_file():
            raise ValueError('Preference audit path is not a regular file')

    def _read_audit(self) -> list[dict]:
        self._checked_audit_path()
        if not self.audit_path.exists():
            return []
        if self.audit_path.stat().st_size > _MAX_AUDIT_BYTES:
            raise ValueError('Preference audit is full; archive it before editing app preferences')
        records = []
        states: dict[str, str] = {}
        with self.audit_path.open('r', encoding='utf-8') as stream:
            for line in stream:
                if len(line.encode('utf-8')) > _MAX_RECORD_BYTES:
                    raise ValueError('Preference audit contains an oversized record')
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError('Preference audit is damaged; preserve it for review') from error
                if (not isinstance(record, dict) or record.get('state') not in ('prepared', 'committed', 'aborted')
                        or not isinstance(record.get('id'), str) or len(record['id']) != 32
                        or any(c not in '0123456789abcdef' for c in record['id'])):
                    raise ValueError('Preference audit contains an invalid record')
                if record['state'] == 'prepared':
                    if record['id'] in states:
                        raise ValueError('Preference audit contains a duplicate change ID')
                    before, after = record.get('before'), record.get('after')
                    if (not isinstance(before, dict) or not isinstance(after, dict) or not after
                            or set(before) != set(after)):
                        raise ValueError('Preference audit contains an invalid change')
                    for key, value in after.items():
                        _checked_value(key, value)
                        old = before[key]
                        if not isinstance(old, dict) or type(old.get('present')) is not bool:
                            raise ValueError('Preference audit contains an invalid previous value')
                        _checked_value(key, old.get('value'))
                    states[record['id']] = 'prepared'
                else:
                    if states.get(record['id']) != 'prepared':
                        raise ValueError('Preference audit contains an unmatched completion')
                    states[record['id']] = record['state']
                records.append(record)
        return records

    @staticmethod
    def _committed(records: list[dict]) -> list[dict]:
        prepared = {record['id']: record for record in records if record['state'] == 'prepared'}
        completed = []
        seen = set()
        for record in records:
            if record['state'] == 'committed' and record['id'] in prepared and record['id'] not in seen:
                completed.append({**prepared[record['id']], 'state': 'committed'})
                seen.add(record['id'])
        return completed

    def _append(self, record: dict) -> None:
        self._checked_audit_path()
        encoded = (json.dumps(record, ensure_ascii=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
        if len(encoded) > _MAX_RECORD_BYTES:
            raise ValueError('Preference audit record is too large')
        current = self.audit_path.stat().st_size if self.audit_path.exists() else 0
        if current + len(encoded) > _MAX_AUDIT_BYTES:
            raise ValueError('Preference audit is full; archive it before editing app preferences')
        descriptor = os.open(self.audit_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            with os.fdopen(descriptor, 'ab', closefd=True) as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            raise
