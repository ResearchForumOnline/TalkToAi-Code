"""Opt-in, bounded candidate improvement for a local project.

The candidate runs with the current user's permissions. This is a review
workflow, not an operating-system sandbox or an automatic updater.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

from agent_core import ProjectTools, _run_agent
from agent_workflow import check_evidence
from ethics_policy import verify_release_policy, rejected_candidate_changes


SOURCE_SUFFIXES = {'.py', '.js', '.jsx', '.ts', '.tsx', '.json', '.toml', '.yaml',
                   '.yml', '.html', '.css', '.scss', '.md', '.txt', '.ps1', '.bat',
                   '.sh', '.spec', '.ini', '.cfg', '.rs', '.go', '.c', '.h', '.cpp',
                   '.gd', '.godot', '.tscn', '.tres', '.gdshader', '.shader', '.uid',
                   '.cs', '.csproj', '.sln', '.lua', '.hpp', '.svg', '.gltf', '.obj',
                   '.mtl', '.import', '.unity', '.prefab', '.mat', '.meta', '.asmdef',
                   '.uss', '.uxml', '.lock'}
ASSET_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.ico', '.ogg', '.wav', '.mp3',
                  '.glb', '.ttf', '.otf', '.woff', '.woff2'}
EXCLUDED_PARTS = {'.git', '.talktoai-code', '.venv', 'venv', 'node_modules', 'build', 'dist',
                  '__pycache__', '.pytest_cache', '.mypy_cache', '.tox',
                  'backups', 'archive', 'archives', 'private', 'secrets', '.godot',
                  'library', 'temp', 'logs', 'obj', 'bin', '.ssh', '.aws', '.azure'}
EXCLUDED_NAMES = {'.env', '.env.local', 'id_rsa', 'id_ed25519', 'credentials.json',
                  'token.json', 'secrets.json', 'appsettings.production.json'}
MAX_FILES = 1500
MAX_BYTES = 60 * 1024 * 1024
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_SCANNED_FILES = 20000
MAX_METRIC_OUTPUT = 16 * 1024
CONTRACT_NAME = 'SKYNET-EVALUATOR.json'


def _create_candidate() -> Path:
    return Path(tempfile.mkdtemp(prefix='TalkToAi-Skynet-')).resolve()


def _create_baseline() -> Path:
    return Path(tempfile.mkdtemp(prefix='TalkToAi-Skynet-Baseline-')).resolve()


def _eligible(relative: Path) -> bool:
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        return False
    parts = [p.lower() for p in relative.parts]
    name = parts[-1]
    if any(p in EXCLUDED_PARTS for p in parts) or name in EXCLUDED_NAMES or name.startswith('.env'):
        return False
    if any(word in name for word in ('credential', 'password', 'private_key', '.pem', '.pfx', '.key')):
        return False
    return relative.suffix.lower() in SOURCE_SUFFIXES | ASSET_SUFFIXES or name in {'dockerfile', 'makefile', 'agents.md'}


def _source_paths(root: Path):
    root = root.resolve()
    try:
        proc = subprocess.run(['git', 'ls-files', '--cached', '-z'],
                              cwd=root, capture_output=True, timeout=20, check=True)
        names = [Path(os.fsdecode(part)) for part in proc.stdout.split(b'\0') if part]
    except (OSError, subprocess.SubprocessError):
        # Exported projects and downloaded source ZIPs have no Git ledger.
        # Prune generated/private folders before traversing and retain only
        # recognized source/assets. Never walk through directory links.
        names = []
        scanned = 0
        for base, directories, files in os.walk(root, followlinks=False):
            directories[:] = sorted(d for d in directories
                if d.lower() not in EXCLUDED_PARTS and not d.startswith('.')
                and not (Path(base) / d).is_symlink()
                and (Path(base) / d).resolve().is_relative_to(root))
            for name in sorted(files):
                scanned += 1
                if scanned > MAX_SCANNED_FILES:
                    raise ValueError('Project scan exceeds 20,000 files. Select the specific app or game folder.')
                relative = (Path(base) / name).relative_to(root)
                if _eligible(relative):
                    names.append(relative)
    return sorted({p for p in names if _eligible(p)}, key=lambda p: str(p).lower())


def _bounded_sha256(path: Path, label: str) -> str:
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        if os.fstat(stream.fileno()).st_size > MAX_FILE_BYTES:
            raise ValueError(f'Skynet source exceeds the 4 MB limit: {label}.')
        read = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            read += len(chunk)
            if read > MAX_FILE_BYTES:
                raise ValueError(f'Skynet source grew beyond the 4 MB limit: {label}.')
            hasher.update(chunk)
    return hasher.hexdigest()


def _copy_candidate(root: Path, candidate: Path):
    manifest = {}
    total = 0
    for relative in _source_paths(root):
        source = root / relative
        if not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
            continue
        size = source.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError(f'Candidate file exceeds the 4 MB copy limit: {relative}. Select a smaller project or prepare a source copy with smaller assets.')
        total += size
        if len(manifest) >= MAX_FILES or total > MAX_BYTES:
            raise ValueError('Project exceeds Skynet Mode copy limits (1,500 files or 60 MB of source).')
        target = candidate / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest[str(relative).replace('\\', '/')] = _bounded_sha256(target, str(relative))
    if not manifest:
        raise ValueError('No eligible source files found in the selected project.')
    return manifest


def _changes(candidate: Path, original_hashes: dict[str, str]):
    changes = []
    now = {}
    total = 0
    for relative in _source_paths(candidate):
        path = candidate / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(candidate.resolve()):
            continue
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError(f'Candidate file exceeds the 4 MB limit: {relative}.')
        total += size
        if len(now) >= MAX_FILES or total > MAX_BYTES:
            raise ValueError('Candidate exceeds Skynet Mode limits (1,500 files or 60 MB of source).')
        now[str(relative).replace('\\', '/')] = path
    for name in sorted(set(original_hashes) | set(now)):
        path = now.get(name)
        digest = _bounded_sha256(path, name) if path else None
        if digest == original_hashes.get(name):
            continue
        status = 'added' if name not in original_hashes else 'deleted' if path is None else 'modified'
        changes.append({'path': name, 'status': status, 'sha256': digest})
    return changes


def _safe_diff_lines(root: Path, name: str):
    """Read only bounded, contained, non-linked source for a review diff."""
    relative = Path(name)
    if not _eligible(relative):
        raise ValueError(f'Unsafe diff path: {name}')
    root = root.resolve()
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            return []
    if not path.is_file() or not path.resolve().is_relative_to(root):
        return []
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f'Diff source exceeds the 4 MB limit: {name}')
    with path.open('rb') as stream:
        if os.fstat(stream.fileno()).st_size > MAX_FILE_BYTES:
            raise ValueError(f'Diff source exceeds the 4 MB limit: {name}')
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f'Diff source grew beyond the 4 MB limit: {name}')
    return raw.decode('utf-8', errors='replace').splitlines(True)


def _diff(original: Path, candidate: Path, changes: list[dict]):
    lines = []
    for item in changes:
        name = item['path']
        if Path(name).suffix.lower() in ASSET_SUFFIXES:
            lines.append(f'Binary asset {item["status"]}: {name} (SHA256: {item["sha256"] or "deleted"})\n')
            continue
        before = _safe_diff_lines(original, name)
        after = _safe_diff_lines(candidate, name)
        lines.extend(difflib.unified_diff(before, after, fromfile='original/' + name, tofile='candidate/' + name))
        if sum(map(len, lines)) > 200000:
            lines.append('\n[Diff truncated at 200 KB; inspect the candidate files directly.]\n')
            break
    return ''.join(lines)


def _evaluator_file(name: str) -> bool:
    """Tests and check configuration belong to the frozen evaluation contract."""
    relative = Path(name)
    parts = [part.lower() for part in relative.parts]
    filename = parts[-1]
    return (any(part in {'test', 'tests', '__tests__', 'spec', 'specs'} for part in parts[:-1])
            or filename.startswith('test_') or filename.endswith(('_test.py', '.test.js', '.test.ts',
                                                                   '.spec.js', '.spec.ts', '.spec.tsx'))
            or filename in {'pytest.ini', 'tox.ini', 'pyproject.toml', 'setup.cfg',
                            'jest.config.js', 'jest.config.ts', 'jest.config.mjs',
                            'vitest.config.js', 'vitest.config.ts', 'vitest.config.mjs',
                            'package.json', 'cargo.toml', 'go.mod', 'agents.md',
                            CONTRACT_NAME.lower()})


def _load_contract(candidate: Path, baseline: dict[str, str]):
    path = candidate / CONTRACT_NAME
    if CONTRACT_NAME not in baseline:
        return None
    if path.stat().st_size > MAX_METRIC_OUTPUT:
        raise ValueError('Skynet evaluator contract exceeds 16 KB.')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('Skynet evaluator contract must be valid UTF-8 JSON.') from exc
    expected = {'schema', 'metric', 'direction', 'command', 'timeout_seconds', 'frozen_files'}
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError('Skynet evaluator contract has missing or unsupported fields.')
    if data['schema'] != 'talktoai.skynet.evaluator.v1':
        raise ValueError('Unsupported Skynet evaluator contract schema.')
    if not isinstance(data['metric'], str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}', data['metric']):
        raise ValueError('Metric name must be a short identifier.')
    if data['direction'] not in ('minimize', 'maximize'):
        raise ValueError('Metric direction must be minimize or maximize.')
    if isinstance(data['timeout_seconds'], bool) or not isinstance(data['timeout_seconds'], int) or not 1 <= data['timeout_seconds'] <= 60:
        raise ValueError('Evaluator timeout must be 1–60 seconds.')
    command = data['command']
    if (not isinstance(command, list) or len(command) != 2 or command[0] != 'python'
            or not isinstance(command[1], str) or not command[1].endswith('.py')):
        raise ValueError('Evaluator command must be ["python", "relative/path.py"].')
    frozen = data['frozen_files']
    if not isinstance(frozen, list) or not 1 <= len(frozen) <= 20 or len(set(map(str, frozen))) != len(frozen):
        raise ValueError('Evaluator frozen_files must contain 1–20 unique source paths.')
    for name in frozen:
        if (not isinstance(name, str) or '\\' in name or ':' in name or
                not _eligible(Path(name)) or name not in baseline):
            raise ValueError(f'Evaluator frozen path is missing or unsafe: {name}')
    if command[1] not in frozen:
        raise ValueError('The evaluator Python script must be listed in frozen_files.')
    return data


def _stop_metric_process(process):
    if process.poll() is not None:
        return
    try:
        if os.name == 'nt':
            flags = subprocess.CREATE_NO_WINDOW
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           capture_output=True, timeout=5, creationflags=flags)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        if process.poll() is None:
            process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _resolve_metric_python(platform_name=None):
    if not getattr(sys, 'frozen', False):
        return sys.executable
    platform_name = platform_name or os.name
    names = ('python', 'python3') if platform_name == 'nt' else ('python3', 'python')
    for name in names:
        executable = shutil.which(name)
        if (executable and Path(executable).is_file() and
                not any(part.lower() == 'windowsapps' for part in Path(executable).parts)):
            return executable
    return None


def _run_metric_contract(evaluation: Path, contract: dict, cancel):
    python = _resolve_metric_python()
    if not python:
        return {'status': 'unverified', 'summary': 'Python executable is unavailable for the evaluator.'}
    script = contract['command'][1]
    if not (evaluation / script).is_file():
        return {'status': 'unverified', 'summary': 'Frozen evaluator script is missing.'}
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen([python, '-B', script], cwd=evaluation,
                                       stdout=output, stderr=errors, creationflags=flags,
                                       start_new_session=os.name != 'nt')
        except OSError as exc:
            return {'status': 'failed', 'summary': f'Evaluator could not start: {type(exc).__name__}: {exc}'}
        deadline = time.monotonic() + contract['timeout_seconds']
        try:
            while process.poll() is None:
                if cancel.is_set():
                    _stop_metric_process(process)
                    return {'status': 'cancelled', 'summary': 'Stopped during metric evaluation.'}
                if time.monotonic() >= deadline:
                    _stop_metric_process(process)
                    return {'status': 'failed', 'summary': 'Evaluator exceeded its bounded timeout.'}
                if (os.fstat(output.fileno()).st_size > MAX_METRIC_OUTPUT or
                        os.fstat(errors.fileno()).st_size > MAX_METRIC_OUTPUT):
                    _stop_metric_process(process)
                    return {'status': 'failed', 'summary': 'Evaluator output exceeded 16 KB.'}
                time.sleep(.1)
        finally:
            if process.poll() is None:
                _stop_metric_process(process)
        if os.fstat(output.fileno()).st_size > MAX_METRIC_OUTPUT or os.fstat(errors.fileno()).st_size > MAX_METRIC_OUTPUT:
            return {'status': 'failed', 'summary': 'Evaluator output exceeded 16 KB.'}
        if process.returncode != 0:
            errors.seek(0)
            return {'status': 'failed', 'summary': f'Evaluator exited {process.returncode}: '
                    + errors.read(1200).decode('utf-8', errors='replace')}
        output.seek(0)
        try:
            metric = json.loads(output.read(MAX_METRIC_OUTPUT).decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            return {'status': 'failed', 'summary': 'Evaluator stdout must be one JSON metric object.'}
        if (not isinstance(metric, dict) or set(metric) != {'metric', 'value'} or
                metric['metric'] != contract['metric'] or isinstance(metric['value'], bool) or
                not isinstance(metric['value'], (int, float))):
            return {'status': 'failed', 'summary': 'Evaluator must return one matching finite numeric metric.'}
        try:
            value = float(metric['value'])
        except (OverflowError, ValueError):
            return {'status': 'failed', 'summary': 'Evaluator must return one matching finite numeric metric.'}
        if not math.isfinite(value):
            return {'status': 'failed', 'summary': 'Evaluator must return one matching finite numeric metric.'}
        return {'status': 'measured', 'metric': contract['metric'],
                'direction': contract['direction'], 'value': value}


def _evaluate(candidate: Path, cancel, contract=None):
    """Run project checks in a disposable copy and detect check-time source edits."""
    if cancel.is_set():
        return {'status': 'unverified', 'summary': 'Stopped before checks.'}, '', None
    with tempfile.TemporaryDirectory(prefix='TalkToAi-Skynet-Eval-') as directory:
        evaluation = Path(directory).resolve()
        before = _copy_candidate(candidate, evaluation)
        try:
            output = ProjectTools(evaluation, True, cancel).execute('run_checks', {})
            result = check_evidence(output)
        except ValueError as exc:
            output = ''
            result = {'status': 'unverified', 'summary': f'No supported candidate check: {exc}'}
        except Exception as exc:
            output = str(exc)
            result = {'status': 'failed', 'summary': f'Check runner failed: {type(exc).__name__}: {exc}'}
        check_mutations = _changes(evaluation, before)
        if check_mutations:
            result = {'status': 'blocked', 'summary':
                      'The check run modified source in its disposable evaluation copy; its result cannot select a candidate.'}
            output += '\nEvaluation-time source changes: '+', '.join(item['path'] for item in check_mutations[:20])
        measurement = None
        if contract and result['status'] == 'passed':
            measurement = _run_metric_contract(evaluation, contract, cancel)
            metric_mutations = _changes(evaluation, before)
            if metric_mutations:
                result = {'status': 'blocked', 'summary':
                          'The evaluator modified source in its disposable copy; its score cannot select a candidate.'}
                measurement = {'status': 'blocked', 'summary': result['summary']}
                output += '\nMetric-time source changes: '+', '.join(item['path'] for item in metric_mutations[:20])
        return result, output[-12000:], measurement


def run_improvement(url, model, project, goal, cancel, emit, performance=None, max_iterations=2):
    """Create a reviewable candidate; never install, merge, or overwrite source.

    `emit` accepts the same (kind, value) shape as run_agent. It additionally
    receives ``skynet_report`` with the candidate and report paths.
    """
    verify_release_policy()
    root = Path(project).resolve()
    if not root.is_dir():
        raise ValueError('Select an existing project folder.')
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 4000:
        raise ValueError('Give Skynet Mode a focused goal of 1–4,000 characters.')
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or not 1 <= max_iterations <= 5:
        raise ValueError('Skynet Mode permits 1–5 candidate iterations.')
    if cancel.is_set():
        raise InterruptedError('Stopped before candidate creation.')

    candidate = _create_candidate()
    baseline = _copy_candidate(root, candidate)
    frozen_baseline = _create_baseline()
    if _copy_candidate(candidate, frozen_baseline) != baseline:
        raise RuntimeError('Candidate source changed while capturing the baseline; retry after it is stable.')
    contract = _load_contract(candidate, baseline)
    emit('status', 'Skynet Mode: candidate copy ready; measuring baseline checks')
    baseline_check, baseline_output, baseline_metric = _evaluate(candidate, cancel, contract)
    results = []
    policy_rejections = []
    best_candidate = None
    best_iteration = None
    best_metric = None
    emit('status', f'Skynet Mode: baseline checks {baseline_check["status"]}; original project is unchanged')
    for index in range(max_iterations):
        if cancel.is_set():
            break
        if index and best_candidate:
            # Explore from the last selected passing source. Earlier passing
            # candidates remain untouched if this iteration regresses.
            candidate = _create_candidate()
            _copy_candidate(best_candidate, candidate)
        before = _changes(candidate, baseline)
        metric_goal = (f' Independent frozen evaluator: {contract["metric"]} ({contract["direction"]}). '
                       f'Baseline value: {baseline_metric["value"] if baseline_metric and baseline_metric["status"] == "measured" else "unavailable"}. '
                       'A candidate is selected only when checks pass and this metric strictly improves. '
                       if contract else '')
        instruction = (f'Skynet Mode iteration {index + 1}/{max_iterations}. Goal: {goal.strip()}\n'
                       'Improve the candidate source with a focused code change. You may inspect and edit files and run project checks. '
                       'The original tests, check configuration, and AGENTS.md are frozen for evaluation; do not edit them. '
                       +metric_goal+
                       'Do not request deployment or modify the original folder. '
                       'Finish with a short statement of what changed and what remains uncertain.')
        tools = ProjectTools(candidate, True, cancel)
        tools.desktop = None
        tools.remote = None
        try:
            _run_agent(url, model, [{'role': 'user', 'content': instruction}], candidate, True,
                       cancel, emit, 10, performance, tools, improvement_mode=True)
        finally:
            try:
                if tools.jobs:
                    tools.jobs.close()
            finally:
                if tools.browser:
                    tools.browser.close()
                if tools.computer:
                    tools.computer.close()
        changed = _changes(candidate, baseline)
        policy_rejections = rejected_candidate_changes(root,changed)
        if policy_rejections:
            results.append({'iteration':index+1,'candidate_dir':str(candidate),'changed_files':len(changed),
                            'checks':{'status':'blocked','summary':'Candidate changed protected operating policy or enforcement.'},
                            'check_output':'Rejected paths: '+', '.join(policy_rejections)})
            emit('status','Skynet candidate rejected: protected policy/enforcement changed. Do not apply this candidate.')
            break
        frozen_paths = set(contract['frozen_files']) | {CONTRACT_NAME} if contract else set()
        changed_evaluation = [item['path'] for item in changed
                              if _evaluator_file(item['path']) or item['path'] in frozen_paths]
        if changed_evaluation:
            results.append({'iteration':index+1,'candidate_dir':str(candidate),'changed_files':len(changed),
                            'checks':{'status':'blocked','summary':'Candidate changed frozen tests, check configuration, or project instructions.'},
                            'check_output':'Frozen paths: '+', '.join(changed_evaluation[:20])})
            emit('status','Skynet candidate rejected: frozen evaluation files changed. Do not apply this candidate.')
            break
        check = {'status': 'unverified', 'summary': 'No candidate changes to verify.'}
        check_output = ''
        measurement = None
        if changed and not cancel.is_set():
            check, check_output, measurement = _evaluate(candidate, cancel, contract)
        results.append({'iteration': index + 1, 'candidate_dir':str(candidate), 'changed_files': len(changed),
                        'checks': check, 'check_output': check_output[-12000:], 'metric': measurement})
        policy_rejections = rejected_candidate_changes(root,_changes(candidate,baseline))
        if policy_rejections:
            results[-1]['checks']={'status':'blocked','summary':'Candidate checks changed protected operating policy. Candidate rejected.'}
            emit('status','Skynet candidate rejected: checks changed protected policy. Do not apply this candidate.')
            break
        if check['status'] == 'passed' and changed and not cancel.is_set():
            select = not contract
            if contract and measurement and measurement['status'] == 'measured':
                reference = best_metric or (baseline_metric if baseline_metric and baseline_metric['status'] == 'measured' else None)
                value = measurement['value']
                if reference is None:
                    select = False
                    results[-1]['selection_reason'] = 'Baseline metric unavailable; improvement cannot be measured.'
                elif contract['direction'] == 'minimize':
                    select = value < reference['value']
                else:
                    select = value > reference['value']
                if reference is not None:
                    results[-1]['selection_reason'] = ('Measured improvement over baseline or prior best.' if select
                                                       else 'Metric did not improve over baseline or prior best.')
            elif contract:
                results[-1]['selection_reason'] = 'Metric was not measured; candidate not selected.'
            if select:
                # Without a contract, passing checks are the only signal and
                # selection is latest passing, not a measured quality gain.
                best_candidate = candidate
                best_iteration = index + 1
                best_metric = measurement
                results[-1]['selected'] = True
                for previous in results[:-1]:
                    previous.pop('selected', None)
        emit('status', f'Skynet Mode: iteration {index + 1}/{max_iterations}; checks {check["status"]}')
        if not changed or changed == before:
            break

    candidate = best_candidate or candidate
    changes = _changes(candidate, baseline)
    policy_rejections = rejected_candidate_changes(root,changes)
    diff_path = candidate / 'SKYNET-CANDIDATE.diff'
    diff_path.write_text(_diff(frozen_baseline, candidate, changes), encoding='utf-8')
    report = {'schema': 'talktoai.skynet.candidate.v3', 'created_utc': datetime.now(timezone.utc).isoformat(),
              'original_project': str(root), 'candidate_dir': str(candidate), 'goal': goal.strip(),
              'model': model, 'baseline_checks': baseline_check,
              'baseline_check_output': baseline_output[-12000:],
              'evaluation_mode': 'metric' if contract else 'checks_only',
              'evaluation_contract': ({'path': CONTRACT_NAME, 'sha256': baseline[CONTRACT_NAME],
                                       'metric': contract['metric'], 'direction': contract['direction']}
                                      if contract else None),
              'baseline_metric': baseline_metric, 'selected_metric': best_metric,
              'iterations': results, 'selected_iteration': best_iteration,
              'selection_basis': ('Strictly improved frozen evaluator metric with passing checks.' if best_iteration and contract
                                  else 'Latest candidate passing independent checks; no quality improvement score.' if best_iteration
                                  else 'Baseline metric unavailable; no candidate can be selected.' if contract and (not baseline_metric or baseline_metric['status'] != 'measured')
                                  else 'No measured improvement over baseline; inspect candidate reports.' if contract
                                  else 'No passing candidate; inspect the final unverified or failed copy.'),
              'changed_files': changes,
              'test_files_changed': [item['path'] for item in changes if Path(item['path']).name.startswith('test_')
                                     or '/test/' in item['path'].lower() or '/tests/' in item['path'].lower()],
              'diff_path': str(diff_path), 'cancelled': cancel.is_set(),
              'rejected_policy_changes': policy_rejections,
              'policy_status': 'rejected' if policy_rejections else 'unchanged',
              'review_required': True,
              'note': 'Candidate only. Checks run in a disposable source copy with current user permissions. Passing checks do not prove a quality improvement. Review diff and evidence before manually applying anything.'}
    report_path = candidate / 'SKYNET-REPORT.json'
    report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    report['report_path'] = str(report_path)
    emit('skynet_report', report)
    return report
