"""Conflict checked promotion and recovery for a selected Skynet source candidate."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid
from datetime import datetime, timezone

from ethics_policy import assert_mutable_path, rejected_candidate_changes, verify_release_policy
from skynet_mode import _eligible, _evaluator_file, _source_paths, MAX_FILE_BYTES, MAX_FILES, MAX_BYTES


class CandidateApplyError(RuntimeError):
    def __init__(self, manifest):
        self.manifest = str(manifest)
        super().__init__(f'Candidate application stopped. Inspect and recover from {manifest}.')


def _digest(path):
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f'Changed file is missing, not regular, or above 4 MB: {path.name}')
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        if os.fstat(stream.fileno()).st_size > MAX_FILE_BYTES:
            raise ValueError('Changed file exceeds the 4 MB limit.')
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(block)
    return sha.hexdigest()


def _mode(path):
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f'Changed file is not regular: {path.name}')
    return stat.S_IMODE(path.stat().st_mode)


def _inventory(root):
    """Match the source scope, byte hashes and modes used for the candidate."""
    found = {}
    modes = {}
    total = 0
    for relative in _source_paths(root):
        if relative.as_posix() == 'SKYNET-REPORT.json':
            continue
        path = root / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        size = path.stat().st_size
        total += size
        if size > MAX_FILE_BYTES or len(found) >= MAX_FILES or total > MAX_BYTES:
            raise ValueError('Project exceeds the bounded candidate source inventory.')
        name = relative.as_posix()
        found[name] = _digest(path)
        modes[name] = _mode(path)
    return found, modes


def _contained(root, name):
    if not isinstance(name, str) or '\\' in name or ':' in name or not name or '\x00' in name:
        raise ValueError('Candidate contains an unsafe path.')
    relative = Path(name)
    if (relative.is_absolute() or '..' in relative.parts or relative.as_posix() != name
            or not _eligible(relative) or _evaluator_file(name)):
        raise ValueError(f'Candidate contains a protected or unsupported path: {name}')
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f'Candidate path crosses a link: {name}')
    if not path.resolve().is_relative_to(root):
        raise ValueError(f'Candidate path leaves its project: {name}')
    return path


def _read_report(report_path, project, expected_report_sha256=None):
    verify_release_policy()
    report_path = Path(report_path)
    if report_path.is_symlink() or report_path.name != 'SKYNET-REPORT.json' or report_path.stat().st_size > 1024 * 1024:
        raise ValueError('Select a bounded Skynet report, not an arbitrary file.')
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if expected_report_sha256 is not None and _digest(report_path) != expected_report_sha256:
        raise ValueError('Candidate report changed after the app recorded it.')
    root = Path(project).resolve()
    candidate = report_path.parent.resolve()
    if (not root.is_dir() or root.is_symlink() or report.get('schema') != 'talktoai.skynet.candidate.v4'
            or Path(report.get('original_project', '')).resolve() != root
            or Path(report.get('candidate_dir', '')).resolve() != candidate
            or not report.get('selected_iteration') or report.get('cancelled')
            or report.get('policy_status') != 'unchanged'):
        raise ValueError('Report has no selected candidate for this project. Run Skynet Mode again.')
    iterations = report.get('iterations')
    selected = next((item for item in iterations if item.get('iteration') == report['selected_iteration']), None) if isinstance(iterations, list) else None
    if not selected or not selected.get('selected') or selected.get('checks', {}).get('status') != 'passed':
        raise ValueError('Selected iteration has no passing recorded checks.')
    if Path(selected.get('candidate_dir','')).resolve() != candidate:
        raise ValueError('Selected checks belong to another candidate copy.')
    diff = candidate / 'SKYNET-CANDIDATE.diff'
    if (diff.is_symlink() or Path(report.get('diff_path','')).resolve() != diff
            or not re.fullmatch(r'[0-9a-f]{64}',str(report.get('diff_sha256','')))
            or _digest(diff) != report['diff_sha256']):
        raise ValueError('Candidate review diff changed after evaluation.')
    if report.get('evaluation_mode') == 'metric':
        base = report.get('baseline_metric') or {}
        score = report.get('selected_metric') or {}
        contract = report.get('evaluation_contract') or {}
        if (base.get('status') != 'measured' or score.get('status') != 'measured'
                or score != selected.get('metric') or contract.get('direction') not in ('minimize', 'maximize')):
            raise ValueError('Selected metric evidence is incomplete.')
        better = score['value'] < base['value'] if contract['direction'] == 'minimize' else score['value'] > base['value']
        if not better:
            raise ValueError('Selected metric does not improve the baseline.')
    elif report.get('evaluation_mode') != 'checks_only':
        raise ValueError('Unknown candidate evaluation mode.')
    items = report.get('changed_files')
    baseline = report.get('baseline_hashes')
    baseline_inventory = report.get('baseline_inventory')
    candidate_inventory = report.get('candidate_inventory')
    baseline_modes = report.get('baseline_modes')
    candidate_modes = report.get('candidate_modes')
    for inventory, modes in ((baseline_inventory,baseline_modes),(candidate_inventory,candidate_modes)):
        if (not isinstance(inventory,dict) or not 1 <= len(inventory) <= MAX_FILES or
                any(not isinstance(name,str) or not _eligible(Path(name)) or not re.fullmatch(r'[0-9a-f]{64}',str(digest))
                    for name,digest in inventory.items())):
            raise ValueError('Candidate source inventory is missing or malformed.')
        if (not isinstance(modes,dict) or set(modes) != set(inventory) or
                any(type(mode) is not int or not 0 <= mode <= 0o7777 for mode in modes.values())):
            raise ValueError('Candidate source mode inventory is missing or malformed.')
    if _inventory(root) != (baseline_inventory,baseline_modes):
        raise ValueError('Project source changed since candidate checks; run Skynet Mode again.')
    if _inventory(candidate) != (candidate_inventory,candidate_modes):
        raise ValueError('Candidate source changed since its checks; run Skynet Mode again.')
    if (not isinstance(items, list) or not 1 <= len(items) <= MAX_FILES
            or not isinstance(baseline, dict) or set(baseline) != {item.get('path') for item in items if isinstance(item, dict)}):
        raise ValueError('Candidate change manifest is missing or malformed.')
    if rejected_candidate_changes(root, items):
        raise ValueError('Candidate changes protected operating policy files.')
    if (root / '.talktoai-code').is_symlink():
        raise ValueError('Project history directory must not be a link.')
    plan = []
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {'path', 'status', 'sha256'}:
            raise ValueError('Candidate changed-file entry is malformed.')
        name = item['path']
        if name in seen:
            raise ValueError('Candidate contains duplicate paths.')
        seen.add(name)
        target = _contained(root, name)
        source = _contained(candidate, name)
        assert_mutable_path(target)
        original = baseline[name]
        original_mode = baseline_modes.get(name)
        if original is not None and not re.fullmatch(r'[0-9a-f]{64}', str(original)):
            raise ValueError('Original file hash is invalid.')
        expected = item['sha256']
        expected_mode = candidate_modes.get(name)
        if item['status'] not in ('added', 'modified', 'deleted'):
            raise ValueError('Unknown candidate change type.')
        if (item['status'] == 'added') != (original is None):
            raise ValueError('Candidate change type conflicts with original hash.')
        if item['status'] == 'deleted':
            if expected is not None or source.exists():
                raise ValueError('Deleted candidate file is still present.')
        elif not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise ValueError('Candidate file hash is invalid.')
        if _digest(target) != original:
            raise ValueError(f'Project changed since candidate creation: {name}')
        if _digest(source) != expected:
            raise ValueError(f'Candidate changed since its check: {name}')
        if _mode(target) != original_mode or _mode(source) != expected_mode:
            raise ValueError(f'Candidate or project file mode changed since its check: {name}')
        plan.append({'path': name, 'status': item['status'], 'before': original, 'after': expected,
                     'before_mode': original_mode, 'after_mode': expected_mode})
    return root, candidate, report, plan


def preview_application(report_path, project, expected_report_sha256=None):
    """Read current bytes and return a plan; performs no write."""
    root, _, report, plan = _read_report(report_path, project, expected_report_sha256)
    return {'project': str(root), 'goal': report.get('goal', '')[:400],
            'evaluation_mode': report['evaluation_mode'], 'selected_iteration': report['selected_iteration'],
            'changes': [{'path': item['path'], 'status': item['status']} for item in plan],
            'diff_path': report['diff_path']}


def _write_json(path, value):
    temporary = path.with_name('.' + path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)


def _replace_bytes(target, source):
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.talktoai-apply-', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'wb') as out, source.open('rb') as incoming:
            shutil.copyfileobj(incoming, out, 1024 * 1024)
            out.flush(); os.fsync(out.fileno())
        os.chmod(temporary, stat.S_IMODE(source.stat().st_mode))
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def apply_candidate(report_path, project, expected_report_sha256=None):
    """Apply a selected candidate after rechecking both source snapshots."""
    root, candidate, report, plan = _read_report(report_path, project, expected_report_sha256)
    history = root / '.talktoai-code' / 'candidate-backups'
    history.mkdir(parents=True, exist_ok=True)
    if history.is_symlink():
        raise ValueError('Candidate history directory must not be a link.')
    backup = history / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:12])
    backup.mkdir(mode=0o700)
    record = {'schema': 'talktoai.candidate.application.v1', 'state': 'prepared',
              'project': str(root), 'candidate_report': str(Path(report_path).resolve()),
              'selected_iteration': report['selected_iteration'], 'changes': plan,
              'applied_paths': [], 'pending_path': None}
    for item in plan:
        if item['before'] is not None:
            source = _contained(root, item['path'])
            if _digest(source) != item['before'] or _mode(source) != item['before_mode']:
                raise ValueError(f'Project changed while saving backup: {item["path"]}')
            destination = backup / 'original' / item['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if _digest(destination) != item['before'] or _mode(destination) != item['before_mode']:
                raise ValueError('Backup copy did not match the original file.')
    manifest = backup / 'APPLICATION.json'
    _write_json(manifest, record)
    try:
        if (_inventory(root) != (report['baseline_inventory'],report['baseline_modes']) or
                _inventory(candidate) != (report['candidate_inventory'],report['candidate_modes'])):
            raise ValueError('Project or candidate source changed while preparing the backup.')
        for item in plan:
            target = _contained(root, item['path'])
            source = _contained(candidate, item['path'])
            if (_digest(target) != item['before'] or _digest(source) != item['after'] or
                    _mode(target) != item['before_mode'] or _mode(source) != item['after_mode']):
                raise ValueError(f'Project or candidate changed during application: {item["path"]}')
            record['state'] = 'applying'
            record['pending_path'] = item['path']
            _write_json(manifest, record)
            if item['status'] == 'deleted':
                target.unlink()
            else:
                _replace_bytes(target, source)
            if _digest(target) != item['after'] or _mode(target) != item['after_mode']:
                raise ValueError(f'Applied bytes did not match the checked candidate: {item["path"]}')
            record['applied_paths'].append(item['path'])
            record['pending_path'] = None
            _write_json(manifest, record)
        record['state'] = 'applied'
        _write_json(manifest, record)
        return {'applied': True, 'files': len(plan), 'project': str(root), 'manifest': str(manifest),
                'diff_path': report['diff_path']}
    except Exception as exc:
        record['state'] = 'needs_recovery'
        record['error_type'] = type(exc).__name__
        try:
            _write_json(manifest, record)
        except Exception:
            # The previous prepared/applying manifest still identifies the
            # backup and pending file. Preserve that recovery path in the UI.
            pass
        raise CandidateApplyError(manifest) from exc


def rollback_application(manifest_path, project):
    """Restore a recorded application if the applied files are still unchanged."""
    verify_release_policy()
    root = Path(project).resolve()
    manifest = Path(manifest_path).resolve()
    expected_parent = root / '.talktoai-code' / 'candidate-backups'
    if (not manifest.is_file() or manifest.name != 'APPLICATION.json' or
            not manifest.parent.parent.resolve() == expected_parent.resolve() or
            manifest.stat().st_size > 1024 * 1024):
        raise ValueError('Select a project-owned candidate backup manifest.')
    record = json.loads(manifest.read_text(encoding='utf-8'))
    if (record.get('schema') != 'talktoai.candidate.application.v1' or
            record.get('project') != str(root) or record.get('state') not in ('applied', 'applying', 'needs_recovery', 'restoring')):
        raise ValueError('This candidate backup is not ready to restore.')
    plan = record.get('changes')
    if not isinstance(plan, list) or not 1 <= len(plan) <= MAX_FILES:
        raise ValueError('Candidate backup manifest is invalid.')
    applied = set(record.get('applied_paths', []))
    restored = set(record.get('restored_paths', []))
    if not applied.issubset({item.get('path') for item in plan}):
        raise ValueError('Candidate backup names unknown applied files.')
    if not restored.issubset(applied):
        raise ValueError('Candidate backup names unknown restored files.')
    original_root=manifest.parent / 'original'
    if original_root.is_symlink():
        raise ValueError('Original backup directory must not be a link.')
    pending = record.get('pending_path')
    if pending:
        pending_item = next((item for item in plan if item.get('path') == pending), None)
        if pending_item is None:
            raise ValueError('Candidate backup has an unknown pending path.')
        pending_target = _contained(root, pending)
        pending_now = (_digest(pending_target),_mode(pending_target))
        if pending_now == (pending_item['after'],pending_item['after_mode']):
            applied.add(pending)
        elif pending_now != (pending_item['before'],pending_item['before_mode']):
            raise ValueError('Pending candidate file changed; review it before restoring.')
    pending_restore=record.get('pending_restore_path')
    if pending_restore and pending_restore not in applied:
        raise ValueError('Candidate backup has an unknown restore path.')
    for item in plan:
        if item['path'] not in applied:
            continue
        target = _contained(root, item['path'])
        assert_mutable_path(target)
        expected = (item['before'],item['before_mode']) if item['path'] in restored else (item['after'],item['after_mode'])
        observed = (_digest(target),_mode(target))
        if item['path'] == pending_restore and observed == (item['before'],item['before_mode']):
            restored.add(item['path'])
            expected = (item['before'],item['before_mode'])
        if observed != expected:
            raise ValueError(f'Project changed after application; review before restoring: {item["path"]}')
        if item['before'] is not None:
            original = _contained(original_root, item['path'])
            if (_digest(original),_mode(original)) != (item['before'],item['before_mode']):
                raise ValueError(f'Original backup changed: {item["path"]}')
    record['state'] = 'restoring'
    record['restored_paths'] = sorted(restored)
    record['pending_restore_path'] = None
    _write_json(manifest, record)
    for item in reversed(plan):
        if item['path'] not in applied or item['path'] in restored:
            continue
        target = _contained(root, item['path'])
        record['pending_restore_path'] = item['path']
        _write_json(manifest, record)
        if item['before'] is None:
            target.unlink()
        else:
            _replace_bytes(target, _contained(original_root, item['path']))
        if (_digest(target),_mode(target)) != (item['before'],item['before_mode']):
            raise RuntimeError(f'Restored bytes did not match the original: {item["path"]}')
        restored.add(item['path'])
        record['restored_paths'] = sorted(restored)
        record['pending_restore_path'] = None
        _write_json(manifest, record)
    record['state'] = 'restored'
    _write_json(manifest, record)
    return {'restored': True, 'files': len(applied), 'project': str(root), 'manifest': str(manifest)}
