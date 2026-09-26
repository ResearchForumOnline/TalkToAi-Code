"""Bounded, opt-in comparisons of installed Ollama coding routes; no downloads."""
import ast
import json
import re
import threading
import time
from datetime import datetime, timezone
from agent_core import _http_stream
from routing import inventory


def valid_clamp(content):
    match = re.search(r'```(?:python)?\s*\n(.*?)```', content, re.S)
    source = match.group(1) if match else content
    try:
        tree = ast.parse(source.strip())
        allowed = (ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return,
                   ast.Call, ast.Name, ast.Load, ast.If, ast.Compare, ast.Lt, ast.Gt,
                   ast.LtE, ast.GtE, ast.Constant, ast.IfExp)
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef) or tree.body[0].name != 'clamp':
            return False
        if any(not isinstance(node, allowed) for node in ast.walk(tree)):
            return False
        if any(not isinstance(node.func, ast.Name) or node.func.id not in ('min', 'max')
               for node in ast.walk(tree) if isinstance(node, ast.Call)):
            return False
        namespace = {'__builtins__': {}, 'min': min, 'max': max}
        exec(compile(tree, '<coding-benchmark>', 'exec'), namespace)
        return all(namespace['clamp'](*args) == expected for args, expected in
                   [((-2, 0, 10), 0), ((12, 0, 10), 10), ((5, 0, 10), 5), ((0, 0, 0), 0)])
    except Exception:
        return False


def compare_routes(config, cancel, emit=lambda *_: None, timeout_seconds=240):
    """Measure the configured server and large local model on identical fixtures.

    Fixtures contain no project data. Tool calls are validated, never executed.
    The report is advisory; a short benchmark does not establish agent quality.
    """
    tool = {'type': 'function', 'function': {'name': 'set_player_name',
            'description': 'Set the player display name.', 'parameters': {'type': 'object',
            'properties': {'name': {'type': 'string'}}, 'required': ['name']}}}
    fixtures = [('code', 'Write a Python function clamp(value, low, high). Return only the function, at most four lines.', []),
                ('tools', 'Use set_player_name to set the display name to Alex. Call the tool, do not answer in prose.', [tool])]
    results = []
    for route, port, key in [('server', 11435, 'server_model'), ('local_large', 11434, 'local_large_model')]:
        if cancel.is_set():
            break
        model = str(config.get(key, ''))
        names = inventory(port)
        entry = {'route': route, 'model': model, 'num_ctx': 4096, 'fixtures': [],
                 'quality_pass': False, 'agent_pass': False, 'success': False}
        results.append(entry)
        if not model or (model not in names and model + ':latest' not in names):
            entry['error'] = 'Configured model is not installed/reachable; no download attempted.'
            continue
        request_cancel = threading.Event()
        route_started = time.monotonic()
        deadline = route_started + timeout_seconds
        done = threading.Event()
        def watch():
            while not done.wait(.1):
                if cancel.is_set() or time.monotonic() >= deadline:
                    request_cancel.set()
                    return
        watcher = threading.Thread(target=watch, daemon=True); watcher.start()
        try:
            for kind, prompt, tools in fixtures:
                if request_cancel.is_set():
                    raise InterruptedError('Benchmark stopped or reached its time limit.')
                emit('status', f'Comparing {route}: {model} · {kind} fixture')
                payload = {'model': model, 'messages': [{'role': 'user', 'content': prompt}],
                           'stream': True, 'think': False, 'keep_alive': '5m',
                           'options': {'num_ctx': 4096, 'num_predict': 96, 'temperature': 0}}
                if tools:
                    payload['tools'] = tools
                started = time.monotonic(); first = None; content = ''; calls = []; stats = {}
                for chunk in _http_stream(f'http://127.0.0.1:{port}', payload, request_cancel):
                    if chunk.get('error'):
                        raise RuntimeError(str(chunk['error'])[:300])
                    message = chunk.get('message', {})
                    delta = message.get('content', '')
                    if (delta or message.get('tool_calls')) and first is None:
                        first = time.monotonic() - started
                    content += delta; calls.extend(message.get('tool_calls', []))
                    if chunk.get('done'):
                        stats = chunk
                passed = bool(stats) and stats.get('done_reason') != 'length'
                if kind == 'code':
                    passed = passed and valid_clamp(content)
                else:
                    fn = calls[0].get('function', {}) if len(calls) == 1 else {}
                    args = fn.get('arguments', {})
                    if isinstance(args, str):
                        args = json.loads(args)
                    passed = passed and fn.get('name') == 'set_player_name' and args == {'name': 'Alex'}
                entry['fixtures'].append({'kind': kind, 'passed': bool(passed),
                    'elapsed_seconds': round(time.monotonic() - started, 3),
                    'first_token_seconds': round(first, 3) if first is not None else None,
                    'load_seconds': round(stats.get('load_duration', 0) / 1e9, 3),
                    'tokens': stats.get('eval_count', 0),
                    'tokens_per_second': round(stats.get('eval_count', 0) / max(stats.get('eval_duration', 0) / 1e9, .001), 3)})
            entry['success'] = True
            entry['quality_pass'] = all(f['passed'] for f in entry['fixtures'])
            entry['elapsed_seconds'] = round(sum(f['elapsed_seconds'] for f in entry['fixtures']), 3)
        except Exception as exc:
            entry['error'] = (f'Time limit exceeded ({timeout_seconds} seconds); benchmark incomplete.'
                              if request_cancel.is_set() and not cancel.is_set() else f'{type(exc).__name__}: {exc}'[:500])
            entry['elapsed_seconds'] = round(time.monotonic() - route_started, 3)
        finally:
            done.set(); watcher.join(timeout=1)
    return {'schema': 'talktoai.model-comparison.v1', 'measured_at': datetime.now(timezone.utc).isoformat(),
            'cancelled': cancel.is_set(), 'results': results,
            'note': 'Two short fixtures, not an end-to-end agent benchmark. No default model is changed. Models on different hosts measure those complete routes, not hardware-independent model rankings.'}
