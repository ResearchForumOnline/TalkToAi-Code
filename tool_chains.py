"""Bounded, read-only sequences of existing ProjectTools calls.

The chain is only a convenience for small models that struggle to alternate
between several tool calls. It has no authority of its own: every step goes
through ``ProjectTools.execute`` and therefore keeps that tool's normal
permission and path checks. Callers should serialize use of one ProjectTools
instance, as they already do for individual agent tool calls.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping


MAX_STEPS = 6
MAX_CHAIN_BYTES = 16_000
MAX_ARGUMENT_BYTES = 4_096
MAX_TOTAL_ARGUMENT_BYTES = 12_000
MAX_RESULT_BYTES = 3_000
MAX_TOTAL_RESULT_BYTES = 9_000
MAX_SECONDS = 60.0

# This is deliberately narrower than ProjectTools.execute. An agent can use
# the ordinary tool interface for a separately authorized write/action.
READ_ONLY_ARGS = {
    'project_info': frozenset(),
    'list_files': frozenset({'pattern'}),
    'read_file': frozenset({'path'}),
    'read_project_files': frozenset({'requests'}),
    'file_fingerprint': frozenset({'path'}),
    'search_code': frozenset({'query'}),
    'project_map': frozenset({'query'}),
    'git_changes': frozenset(),
    'desktop_server_inventory': frozenset(),
    'desktop_projects': frozenset(),
    'browser': frozenset({'action', 'target', 'value'}),
    'remote_status': frozenset(),
    'remote_project_info': frozenset({'cwd'}),
}
REQUIRED_ARGS = {
    'read_file': frozenset({'path'}),
    'read_project_files': frozenset({'requests'}),
    'file_fingerprint': frozenset({'path'}),
    'search_code': frozenset({'query'}),
    'browser': frozenset({'action'}),
}
READ_ONLY_BROWSER_ACTIONS = frozenset({'search', 'open', 'inspect'})
FREE_SEARCH_ENGINES = frozenset({'auto', 'duckduckgo', 'bing', 'google', 'brave'})


class _DeadlineCancel:
    """Pass a cooperative deadline to tools that already check cancel.is_set()."""

    def __init__(self, parent, deadline):
        self.parent = parent
        self.deadline = deadline

    def is_set(self):
        return bool(self.parent and self.parent.is_set()) or time.monotonic() >= self.deadline


def _utf8_prefix(value, limit):
    raw = value.encode('utf-8')
    return raw[:limit].decode('utf-8', errors='ignore'), len(raw), len(raw) > limit, hashlib.sha256(raw).hexdigest()


def _validate_steps(steps_json):
    if not isinstance(steps_json, str):
        raise ValueError('Chain steps must be a JSON array string.')
    if len(steps_json.encode('utf-8')) > MAX_CHAIN_BYTES:
        raise ValueError('Chain JSON exceeds 16,000 bytes.')
    try:
        steps = json.loads(steps_json)
    except (ValueError, TypeError) as exc:
        raise ValueError('Chain steps must be valid JSON.') from exc
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError('A chain needs 1-6 steps.')
    validated = []
    total_args = 0
    for index, step in enumerate(steps, 1):
        if not isinstance(step, Mapping) or 'tool' not in step or not set(step).issubset({'tool', 'args'}):
            raise ValueError(f'Step {index} needs tool and optional args fields only.')
        name, args = step['tool'], step.get('args', {})
        if not isinstance(name, str) or name not in READ_ONLY_ARGS:
            raise ValueError(f'Step {index} uses a tool unavailable in read-only chains.')
        if not isinstance(args, dict) or not set(args).issubset(READ_ONLY_ARGS[name]):
            raise ValueError(f'Step {index} has unsupported arguments for {name}.')
        if not REQUIRED_ARGS.get(name, frozenset()).issubset(args):
            raise ValueError(f'Step {index} is missing a required argument for {name}.')
        if any(not isinstance(value, str) for value in args.values()):
            raise ValueError(f'Step {index} argument values must be strings.')
        if name == 'browser':
            action = args['action'].strip().lower()
            if action not in READ_ONLY_BROWSER_ACTIONS:
                raise ValueError(f'Step {index} browser action must be search, open or inspect.')
            if action in ('search', 'open') and not args.get('target', '').strip():
                raise ValueError(f'Step {index} browser {action} needs a target.')
            if action != 'search' and args.get('value', ''):
                raise ValueError(f'Step {index} browser {action} does not accept value.')
            if action == 'inspect' and args.get('target', ''):
                raise ValueError(f'Step {index} browser inspect does not accept target.')
            if action == 'search':
                engine = args.get('value', 'auto').strip().lower() or 'auto'
                if engine not in FREE_SEARCH_ENGINES:
                    raise ValueError(f'Step {index} search engine must be Auto, DuckDuckGo, Bing, Google or Brave.')
                # A chain uses a free search provider by default even when an
                # optional paid provider is selected for individual searches.
                args = dict(args, action=action, value=engine)
        encoded = json.dumps(args, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(encoded) > MAX_ARGUMENT_BYTES:
            raise ValueError(f'Step {index} arguments exceed 4,096 bytes.')
        total_args += len(encoded)
        if total_args > MAX_TOTAL_ARGUMENT_BYTES:
            raise ValueError('Chain arguments exceed 12,000 bytes in total.')
        validated.append((name, args))
    return validated


def _result_failure_reason(name, result):
    """Recognize read errors returned as data by existing tool adapters."""
    if name == 'remote_project_info':
        match = re.match(r'Exit\s+(\d+)\b', result)
        if match and int(match.group(1)) != 0:
            return 'Remote read command returned a nonzero exit code.'
    if name == 'remote_status':
        try:
            verification = json.loads(result).get('verification', '')
        except (ValueError, AttributeError, TypeError):
            verification = ''
        match = re.match(r'Exit\s+(\d+)\b', verification)
        if match and int(match.group(1)) != 0:
            return 'Remote status check returned a nonzero exit code.'
    if name == 'read_project_files':
        try:
            files = json.loads(result).get('files', [])
        except (ValueError, AttributeError, TypeError):
            files = []
        if any(isinstance(item, dict) and item.get('error') for item in files):
            return 'One or more project files could not be read; inspect the per-file errors.'
    if name == 'git_changes' and result == 'Git is not installed.':
        return 'Git is not installed; the change query could not run.'
    if name == 'git_changes' and result.startswith('Git query failed: exit '):
        return 'Git change query failed; inspect the reported exit code.'
    return ''


def _source_bounded(name, result):
    """Flag partial source observations separately from response-byte clipping."""
    if name == 'list_files':
        return '[File scan limit reached;' in result
    if name == 'search_code':
        return '[60-hit limit;' in result
    if name in ('desktop_projects', 'read_project_files'):
        try:
            parsed = json.loads(result)
        except (ValueError, TypeError):
            return False
        if name == 'desktop_projects':
            return bool(parsed.get('bounded')) if isinstance(parsed, dict) else False
        files = parsed.get('files', []) if isinstance(parsed, dict) else []
        return any(isinstance(item, dict) and item.get('complete') is False for item in files)
    return False


def run_tool_chain(tools, steps_json, *, max_seconds=MAX_SECONDS):
    """Execute 1-6 read-only calls and return structured evidence.

    Schema errors raise ValueError before any step runs. Runtime failures are
    returned as a failed step followed by skipped steps. The deadline is
    cooperative: SSH and browser tools observe it at their cancellation
    points; one in-flight browser navigation can finish at its own timeout.
    """
    steps = _validate_steps(steps_json)
    try:
        seconds = float(max_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError('Chain time limit must be 1-60 seconds.') from exc
    if not 1 <= seconds <= MAX_SECONDS:
        raise ValueError('Chain time limit must be 1-60 seconds.')
    started = time.monotonic()
    deadline = started + seconds
    original_cancel = getattr(tools, 'cancel', None)
    scoped_cancel = _DeadlineCancel(original_cancel, deadline)
    previous_browser = getattr(tools, 'browser', None)
    original_browser_cancel = getattr(previous_browser, 'cancel', None)
    records = []
    result_budget = MAX_TOTAL_RESULT_BYTES
    chain_status = 'completed'
    tools.cancel = scoped_cancel
    if previous_browser is not None:
        previous_browser.cancel = scoped_cancel
    try:
        for index, (name, args) in enumerate(steps, 1):
            if original_cancel is not None and original_cancel.is_set():
                chain_status = 'cancelled'
                break
            if time.monotonic() >= deadline:
                chain_status = 'timed_out'
                break
            step_started = time.monotonic()
            record = {'step': index, 'tool': name}
            if name == 'browser':
                record['action'] = args['action']
            try:
                raw_result = tools.execute(name, dict(args))
                if not isinstance(raw_result, str):
                    raw_result = json.dumps(raw_result, ensure_ascii=False, default=str)
                available = min(MAX_RESULT_BYTES, result_budget)
                excerpt, length, truncated, digest = _utf8_prefix(raw_result, available)
                record.update(status='completed', result=excerpt, result_bytes=length,
                              returned_bytes=len(excerpt.encode('utf-8')),
                              result_sha256=digest, truncated=truncated,
                              source_bounded=_source_bounded(name, raw_result))
                result_budget -= record['returned_bytes']
                returned_error = _result_failure_reason(name, raw_result)
                if returned_error:
                    record['status'] = 'failed'
                    record['error'] = returned_error
                    chain_status = 'failed'
            except InterruptedError as exc:
                chain_status = 'cancelled' if original_cancel is not None and original_cancel.is_set() else 'timed_out' if time.monotonic() >= deadline else 'failed'
                record.update(status='failed', error=str(exc)[:400], truncated=False)
            except Exception as exc:
                chain_status = 'failed'
                record.update(status='failed', error=f'{type(exc).__name__}: {exc}'[:400], truncated=False)
            record['elapsed_seconds'] = round(time.monotonic() - step_started, 2)
            records.append(record)
            if chain_status != 'completed':
                break
            if original_cancel is not None and original_cancel.is_set():
                chain_status = 'cancelled'
                break
            if time.monotonic() >= deadline:
                chain_status = 'timed_out'
                break
    finally:
        tools.cancel = original_cancel
        if previous_browser is not None:
            previous_browser.cancel = original_browser_cancel
        elif getattr(tools, 'browser', None) is not None:
            # A browser created by this chain must keep the turn's original
            # cancellation event for later single-tool use.
            tools.browser.cancel = original_cancel
    for index in range(len(records), len(steps)):
        records.append({'step': index + 1, 'tool': steps[index][0], 'status': 'skipped',
                        'reason': f'chain_{chain_status}', 'truncated': False})
    return {'status': chain_status, 'steps': records,
            'completed': sum(item['status'] == 'completed' for item in records),
            'failed': sum(item['status'] == 'failed' for item in records),
            'skipped': sum(item['status'] == 'skipped' for item in records),
            'elapsed_seconds': round(time.monotonic() - started, 2),
            'result_limit_bytes': MAX_TOTAL_RESULT_BYTES}


def invoke_chain(tools, steps_json, *, max_seconds=MAX_SECONDS):
    """JSON interface suitable for ProjectTools.execute or an agent tool call."""
    return json.dumps(run_tool_chain(tools, steps_json, max_seconds=max_seconds), ensure_ascii=False)
