"""Opt-in, bounded candidate improvement for a local project.

The candidate runs with the current user's permissions. This is a review
workflow, not an operating-system sandbox or an automatic updater.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

from agent_core import ProjectTools, _run_agent
from agent_workflow import check_evidence


SOURCE_SUFFIXES = {'.py', '.js', '.jsx', '.ts', '.tsx', '.json', '.toml', '.yaml',
                   '.yml', '.html', '.css', '.scss', '.md', '.txt', '.ps1', '.bat',
                   '.sh', '.spec', '.ini', '.cfg', '.rs', '.go', '.c', '.h', '.cpp'}
EXCLUDED_PARTS = {'.git', '.talktoai-code', '.venv', 'venv', 'node_modules', 'build', 'dist',
                  '__pycache__', '.pytest_cache', '.mypy_cache', '.tox',
                  'backups', 'archive', 'archives', 'private', 'secrets'}
EXCLUDED_NAMES = {'.env', '.env.local', 'id_rsa', 'id_ed25519', 'credentials.json',
                  'token.json', 'secrets.json', 'appsettings.production.json'}
MAX_FILES = 1500
MAX_BYTES = 60 * 1024 * 1024
MAX_FILE_BYTES = 4 * 1024 * 1024


def _eligible(relative: Path) -> bool:
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        return False
    parts = [p.lower() for p in relative.parts]
    name = parts[-1]
    if any(p in EXCLUDED_PARTS for p in parts) or name in EXCLUDED_NAMES:
        return False
    if any(word in name for word in ('credential', 'password', 'private_key', '.pem', '.pfx', '.key')):
        return False
    return relative.suffix.lower() in SOURCE_SUFFIXES or name in {'dockerfile', 'makefile', 'agents.md'}


def _source_paths(root: Path):
    try:
        proc = subprocess.run(['git', 'ls-files', '--cached', '-z'],
                              cwd=root, capture_output=True, timeout=20, check=True)
        names = [Path(os.fsdecode(part)) for part in proc.stdout.split(b'\0') if part]
    except (OSError, subprocess.SubprocessError):
        # A folder without Git has no reliable source ledger. Limit intake to
        # conventional code locations and code extensions only.
        names = []
        for base in (root, root / 'src', root / 'tests'):
            if not base.is_dir():
                continue
            for p in (base.glob('*') if base == root else base.rglob('*')):
                if p.is_file() and p.suffix.lower() in {'.py', '.js', '.jsx', '.ts', '.tsx',
                                                         '.html', '.css', '.scss', '.rs', '.go',
                                                         '.c', '.h', '.cpp'}:
                    names.append(p.relative_to(root))
    return sorted({p for p in names if _eligible(p)}, key=lambda p: str(p).lower())


def _copy_candidate(root: Path, candidate: Path):
    manifest = {}
    total = 0
    for relative in _source_paths(root):
        source = root / relative
        if not source.is_file() or source.is_symlink():
            continue
        size = source.stat().st_size
        if size > MAX_FILE_BYTES:
            continue
        total += size
        if len(manifest) >= MAX_FILES or total > MAX_BYTES:
            raise ValueError('Project exceeds Skynet Mode copy limits (1,500 files or 60 MB of source).')
        target = candidate / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest[str(relative).replace('\\', '/')] = hashlib.sha256(target.read_bytes()).hexdigest()
    if not manifest:
        raise ValueError('No eligible source files found in the selected project.')
    return manifest


def _changes(candidate: Path, original_hashes: dict[str, str]):
    changes = []
    now = {str(p.relative_to(candidate)).replace('\\', '/'): p for p in candidate.rglob('*')
           if p.is_file() and _eligible(p.relative_to(candidate)) and not p.is_symlink()}
    for name in sorted(set(original_hashes) | set(now)):
        path = now.get(name)
        after = path.read_bytes() if path else b''
        digest = hashlib.sha256(after).hexdigest() if path else None
        if digest == original_hashes.get(name):
            continue
        if len(after) > MAX_FILE_BYTES:
            raise ValueError('Candidate contains a changed file larger than 4 MB.')
        status = 'added' if name not in original_hashes else 'deleted' if path is None else 'modified'
        changes.append({'path': name, 'status': status, 'sha256': digest})
    return changes


def _diff(original: Path, candidate: Path, changes: list[dict]):
    lines = []
    for item in changes:
        name = item['path']
        before = (original / name).read_text(encoding='utf-8', errors='replace').splitlines(True) if (original / name).is_file() else []
        after = (candidate / name).read_text(encoding='utf-8', errors='replace').splitlines(True) if (candidate / name).is_file() else []
        lines.extend(difflib.unified_diff(before, after, fromfile='original/' + name, tofile='candidate/' + name))
        if sum(map(len, lines)) > 200000:
            lines.append('\n[Diff truncated at 200 KB; inspect the candidate files directly.]\n')
            break
    return ''.join(lines)


def run_improvement(url, model, project, goal, cancel, emit, performance=None, max_iterations=2):
    """Create a reviewable candidate; never install, merge, or overwrite source.

    `emit` accepts the same (kind, value) shape as run_agent. It additionally
    receives ``skynet_report`` with the candidate and report paths.
    """
    root = Path(project).resolve()
    if not root.is_dir():
        raise ValueError('Select an existing project folder.')
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 4000:
        raise ValueError('Give Skynet Mode a focused goal of 1–4,000 characters.')
    if not isinstance(max_iterations, int) or not 1 <= max_iterations <= 3:
        raise ValueError('Skynet Mode permits 1–3 candidate iterations.')
    if cancel.is_set():
        raise InterruptedError('Stopped before candidate creation.')

    candidate = Path(tempfile.mkdtemp(prefix='TalkToAi-Skynet-')).resolve()
    baseline = _copy_candidate(root, candidate)
    results = []
    emit('status', 'Skynet Mode: candidate copy ready; original project is unchanged')
    for index in range(max_iterations):
        if cancel.is_set():
            break
        before = _changes(candidate, baseline)
        instruction = (f'Skynet Mode iteration {index + 1}/{max_iterations}. Goal: {goal.strip()}\n'
                       'Improve the candidate source with a focused code change. You may inspect and edit files and run project checks. '
                       'Do not alter test assertions to manufacture a pass. Do not request deployment or modify the original folder. '
                       'Finish with a short statement of what changed and what remains uncertain.')
        tools = ProjectTools(candidate, True, cancel)
        tools.desktop = None
        tools.remote = None
        try:
            _run_agent(url, model, [{'role': 'user', 'content': instruction}], candidate, True,
                       cancel, emit, 10, performance, tools, improvement_mode=True)
        finally:
            if tools.browser:
                tools.browser.close()
            if tools.computer:
                tools.computer.close()
        changed = _changes(candidate, baseline)
        check = {'status': 'unverified', 'summary': 'No candidate changes to verify.'}
        check_output = ''
        if changed and not cancel.is_set():
            try:
                check_output = ProjectTools(candidate, True, cancel).execute('run_checks', {})
                check = check_evidence(check_output)
            except ValueError as exc:
                check = {'status': 'unverified', 'summary': f'No supported candidate check: {exc}'}
            except Exception as exc:
                check = {'status': 'failed', 'summary': f'Check runner failed: {type(exc).__name__}: {exc}'}
                check_output = str(exc)
        results.append({'iteration': index + 1, 'changed_files': len(changed),
                        'checks': check, 'check_output': check_output[-12000:]})
        emit('status', f'Skynet Mode: iteration {index + 1}/{max_iterations}; checks {check["status"]}')
        if not changed or changed == before or check['status'] == 'failed':
            break

    changes = _changes(candidate, baseline)
    diff_path = candidate / 'SKYNET-CANDIDATE.diff'
    diff_path.write_text(_diff(root, candidate, changes), encoding='utf-8')
    report = {'schema': 'talktoai.skynet.candidate.v1', 'created_utc': datetime.now(timezone.utc).isoformat(),
              'original_project': str(root), 'candidate_dir': str(candidate), 'goal': goal.strip(),
              'model': model, 'iterations': results, 'changed_files': changes,
              'test_files_changed': [item['path'] for item in changes if Path(item['path']).name.startswith('test_')
                                     or '/test/' in item['path'].lower() or '/tests/' in item['path'].lower()],
              'diff_path': str(diff_path), 'cancelled': cancel.is_set(),
              'review_required': True,
              'note': 'Candidate only. Checks run with current user permissions. Review diff and evidence before manually applying anything.'}
    report_path = candidate / 'SKYNET-REPORT.json'
    report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    report['report_path'] = str(report_path)
    emit('skynet_report', report)
    return report
