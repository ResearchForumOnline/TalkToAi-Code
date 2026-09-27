"""Read-only project skills that run entirely on the user's computer.

The recognizer accepts a small, explicit language. It does not guess at broader
coding requests. Source and manifest contents are data and never become commands.
"""
from __future__ import annotations

import ast
from collections import Counter, deque
from dataclasses import dataclass
import fnmatch
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

from work_packet import PRIVATE_NAME, PRIVATE_PARTS, SENSITIVE_LINE


OFFLINE_COMMANDS = (
    {'id': 'quick_check', 'title': 'Project quick check',
     'description': 'Combine a project report, Python syntax, unfinished-work notes and Git summary.',
     'example': 'Check this project locally'},
    {'id': 'project_report', 'title': 'Understand this project',
     'description': 'Find project types, source languages, entry points and check hints.',
     'example': 'What is in this project?'},
    {'id': 'find_files', 'title': 'Find files',
     'description': 'Find files by name, path or a simple wildcard.',
     'example': 'Find files matching *.gd'},
    {'id': 'find_symbols', 'title': 'Find code symbols',
     'description': 'Locate named functions, classes and methods in source.',
     'example': 'Find symbol player'},
    {'id': 'todo_report', 'title': 'Find unfinished work',
     'description': 'List TODO, FIXME, HACK and XXX comments with file locations.',
     'example': 'Show TODOs'},
    {'id': 'python_syntax', 'title': 'Check Python syntax',
     'description': 'Parse Python files without importing or running the project.',
     'example': 'Check Python syntax'},
    {'id': 'git_changes', 'title': 'Show Git changes',
     'description': 'List staged and unstaged tracked files without showing file contents.',
     'example': 'Show Git changes'},
)
_COMMANDS = {item['id']: item for item in OFFLINE_COMMANDS}
_IGNORE = PRIVATE_PARTS | {'bin', 'obj', 'target', 'coverage', 'site-packages',
    'artifacts', 'outputs', 'output', 'downloads', 'logs', 'log', 'packages',
    'node_modules', 'vendor', 'library', 'temp', 'tmp', 'venv', 'env'}
_GENERATED = re.compile(r'^(?:dist|build|backup|archive|output|artifacts)(?:[-_.]|$)', re.I)
_LANGUAGES = {'.py': 'Python', '.gd': 'GDScript', '.cs': 'C#', '.js': 'JavaScript',
    '.jsx': 'JavaScript', '.mjs': 'JavaScript', '.ts': 'TypeScript', '.tsx': 'TypeScript',
    '.rs': 'Rust', '.go': 'Go', '.java': 'Java', '.c': 'C', '.h': 'C/C++ header',
    '.cpp': 'C++', '.hpp': 'C++', '.cc': 'C++', '.lua': 'Lua', '.swift': 'Swift',
    '.kt': 'Kotlin', '.rb': 'Ruby', '.php': 'PHP', '.sh': 'Shell', '.ps1': 'PowerShell'}
_MARKERS = {'project.godot': 'Godot', 'package.json': 'Node.js / JavaScript',
    'pyproject.toml': 'Python', 'requirements.txt': 'Python', 'setup.py': 'Python',
    'cargo.toml': 'Rust', 'go.mod': 'Go', 'cmakelists.txt': 'CMake / C++',
    'projectversion.txt': 'Unity', 'pom.xml': 'Java / Maven',
    'build.gradle': 'Java / Kotlin', 'gemfile': 'Ruby', 'composer.json': 'PHP'}
_ENTRY_NAMES = {'main.py', 'app.py', '__main__.py', 'manage.py', 'main.gd',
    'main.rs', 'main.go', 'index.html', 'main.ts', 'main.js', 'index.ts', 'index.js'}
_DECLARATION = re.compile(
    r'^\s*(?:(?:export|default|public|private|protected|internal|static|async|final|abstract|override)\s+)*'
    r'(?:(?:class|class_name|interface|struct|enum|def|func|function|fn)\s+([A-Za-z_$][\w$]*)'
    r'|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=.*(?:=>|function\b)'
    r'|(?:[\w<>?\[\],]+\s+)+([A-Za-z_$][\w$]*)\s*\([^;]*\)\s*(?:\{|=>))')
_TODO = re.compile(r'\b(TODO|FIXME|HACK|XXX)\b\s*:?', re.I)


@dataclass(frozen=True)
class ScanLimits:
    max_files: int = 2000
    max_entries: int = 12000
    max_bytes: int = 4_000_000
    max_file_bytes: int = 160_000
    max_seconds: float = 4.0
    max_results: int = 60


def recognize_request(text):
    """Return an exact local skill request, or None for everything broader."""
    if not isinstance(text, str) or len(text) > 500:
        return None
    value = text.strip()
    slash = re.fullmatch(r'/local\s+([a-z_]+)(?:\s+([^\r\n]+))?', value)
    if slash:
        command, query = slash.group(1), (slash.group(2) or '').strip()
        if command in _COMMANDS and ((command in {'find_files', 'find_symbols'} and len(query) <= 160) or not query):
            return {'command': command, 'query': query}
        return None
    value = re.sub(r'^(?:please\s+|can you\s+|could you\s+)', '', value, flags=re.I)
    exact_value = value.rstrip(' ?.!')
    exact = {
        'quick_check': r'(?:check|inspect) (?:this |my |the )?project locally|(?:run )?(?:a )?project quick check',
        'project_report': r'(?:inspect|summari[sz]e|describe) (?:this |my |the )?project|'
            r'(?:show |give me )?(?:a )?project (?:report|overview|health)|what is in (?:this |my |the )?project',
        'todo_report': r'(?:show|find|list) (?:the |all )?(?:todos|todo comments|fixmes|unfinished work)',
        'python_syntax': r'(?:check|inspect|validate) (?:the )?python syntax|(?:check|find) python syntax errors',
        'git_changes': r'(?:show|list|summari[sz]e) (?:the |my )?git changes|git (?:status|changes)|what changed in git',
    }
    for command, pattern in exact.items():
        if re.fullmatch(pattern, exact_value, re.I):
            return {'command': command, 'query': ''}
    match = re.fullmatch(r'(?:find|locate|show) (files?|symbols?|functions?|classes?) (?:matching |named |called )?(.+)', value, re.I)
    if match:
        query = match.group(2).strip().strip('`\"\'')
        # Single names, paths or globs only. Compound requests stay with the agent.
        if re.fullmatch(r'[\w./\\*?\[\]$-]{1,160}', query) and not query.startswith(('/', '\\')) and '..' not in query.split('/'):
            return {'command': 'find_files' if match.group(1).lower().startswith('file') else 'find_symbols', 'query': query}
    return None


def _action(command, query=''):
    return {'label': _COMMANDS[command]['title'], 'command': command, 'query': query}


def _result(command, status, summary, details=(), **extra):
    return {'command': command, 'status': status,
            'title': _COMMANDS.get(command, {}).get('title', 'Offline project assistant'),
            'summary': summary, 'details': list(details), 'actions': [],
            'metrics': {'model_calls': 0, 'network_calls': 0}, **extra}


def _safe_name(name):
    lower = name.casefold()
    return (not name.startswith('.') and lower not in _IGNORE and
            not PRIVATE_NAME.search(name) and
            Path(name).suffix.casefold() not in {'.pem', '.key', '.pfx', '.p12', '.dpapi', '.log'} and
            lower not in {'config.json', 'connections.json', 'studio.json'})


def _is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


class _Scan:
    def __init__(self, root, cancel, limits):
        self.root, self.cancel = root, cancel
        self.limits = ScanLimits(
            max(1, min(int(limits.max_files), 5000)), max(1, min(int(limits.max_entries), 20000)),
            max(1, min(int(limits.max_bytes), 8_000_000)), max(1, min(int(limits.max_file_bytes), 200_000)),
            max(0.01, min(float(limits.max_seconds), 10.0)), max(1, min(int(limits.max_results), 100)))
        self.start = time.monotonic()
        self.files = []; self.entries = self.bytes = self.read_count = self.skipped = 0
        self.limited = False
        self.exhausted = False
        self.notes = []

    def stopped(self):
        if self.cancel is not None and self.cancel.is_set():
            raise InterruptedError('Cancelled.')
        if time.monotonic() - self.start >= self.limits.max_seconds:
            self.limited = True
            self.exhausted = True
        return self.exhausted

    def inventory(self):
        queue = deque([(self.root, 0)])
        while queue and not self.stopped():
            directory, depth = queue.popleft()
            try:
                entries = []
                with os.scandir(directory) as iterator:
                    for item in iterator:
                        if self.stopped():
                            break
                        self.entries += 1
                        if self.entries > self.limits.max_entries:
                            self.limited = True
                            return self.files
                        if not _safe_name(item.name):
                            continue
                        try:
                            info = item.stat(follow_symlinks=False)
                            if _is_link(info):
                                continue
                            if stat.S_ISDIR(info.st_mode):
                                if not _GENERATED.match(item.name):
                                    if depth < 8:
                                        entries.append((Path(item.path), True))
                                    else:
                                        self.limited = True
                            elif stat.S_ISREG(info.st_mode):
                                entries.append((Path(item.path), False))
                        except OSError:
                            self.skipped += 1
                for path, is_directory in sorted(entries, key=lambda pair: pair[0].name.casefold()):
                    if self.stopped():
                        break
                    if is_directory:
                        queue.append((path, depth + 1))
                    else:
                        self.files.append(path.relative_to(self.root).as_posix())
                        if len(self.files) >= self.limits.max_files:
                            self.limited = True
                            return self.files
            except OSError:
                self.skipped += 1
        return self.files

    def read(self, relative):
        if self.stopped():
            return None
        path = self.root / relative
        try:
            # Recheck parents at use time, including Windows directory junctions.
            current = self.root
            for part in Path(relative).parts:
                current = current / part
                if _is_link(current.lstat()):
                    self.skipped += 1
                    return None
            if not path.resolve().is_relative_to(self.root):
                return None
            info = path.stat()
            if info.st_size > self.limits.max_file_bytes:
                self.skipped += 1
                return None
            if self.bytes + info.st_size > self.limits.max_bytes:
                self.limited = True
                self.exhausted = True
                return None
            with path.open('rb') as stream:
                data = stream.read(min(self.limits.max_file_bytes, self.limits.max_bytes - self.bytes) + 1)
            if len(data) > self.limits.max_file_bytes or self.bytes + len(data) > self.limits.max_bytes:
                self.limited = True
                self.exhausted = True
                return None
            self.bytes += len(data); self.read_count += 1
            if b'\0' in data:
                self.skipped += 1
                return None
            return data.decode('utf-8-sig')
        except (OSError, UnicodeError, ValueError):
            self.skipped += 1
            return None

    def finish(self, result):
        self.stopped()
        if (self.limited or self.skipped) and result['status'] == 'completed':
            result['status'] = 'partial'
        if self.limited:
            result['details'].append('Scan limit reached; results cover the inspected files only. Select a smaller project folder to continue.')
        if self.skipped:
            result['details'].append(f'{self.skipped} unreadable, oversized or unsupported entries were skipped.')
        result['details'].extend(self.notes[:5])
        result['metrics'].update(files_seen=len(self.files), files_read=self.read_count,
            bytes_read=self.bytes, entries_seen=self.entries, limited=self.limited,
            elapsed_ms=round((time.monotonic() - self.start) * 1000))
        return result


def _project_report(scan):
    paths = scan.files
    languages = Counter(_LANGUAGES.get(Path(path).suffix.casefold()) for path in paths)
    languages.pop(None, None)
    markers = [(path, _MARKERS[Path(path).name.casefold()]) for path in paths if Path(path).name.casefold() in _MARKERS]
    details = ['Source languages: ' + (', '.join(f'{name} ({count} files)' for name, count in languages.most_common()) or 'No recognized source files found.')]
    details.extend(f'Project marker: {path} — {kind}' for path, kind in markers[:20])
    entries = [path for path in paths if Path(path).name.casefold() in _ENTRY_NAMES]
    details.extend(f'Possible entry point: {path}' for path in entries[:12])
    test_files = [p for p in paths if Path(p).name.startswith('test_') or any(part.casefold() in {'tests', 'test'} for part in Path(p).parts) or re.search(r'\.(?:test|spec)\.', p)]
    details.append(f'{len(test_files)} files have a test name or sit in a test folder. No tests have been run.')
    for path, kind in markers[:12]:
        folder = str(Path(path).parent).replace('\\', '/')
        if kind == 'Godot':
            details.append(f'Open {path} in Godot. An editor import check is available through the project checks feature.')
        elif kind == 'Python':
            details.append(f'Python project in {folder}: inspect the project README for dependencies; use Check Python syntax for a no-execution check.')
        elif Path(path).name.casefold() == 'package.json':
            content = scan.read(path)
            if content is not None:
                try:
                    manifest = json.loads(content)
                    scripts = manifest.get('scripts', {}) if isinstance(manifest, dict) else {}
                    names = [str(key) for key in scripts if isinstance(scripts, dict) and re.fullmatch(r'[A-Za-z0-9:_-]{1,50}', str(key)) and not SENSITIVE_LINE.search(str(key))]
                    if names:
                        details.append(f'Package script names in {path}: {", ".join(names[:12])}. Review their definitions before running them.')
                except (ValueError, TypeError):
                    details.append(f'{path}: package metadata is not valid JSON.')
    result = _result('project_report', 'completed', f'Inspected {len(paths)} eligible files and found {len(markers)} project markers.', details[:60])
    result['actions'] = [_action('find_files'), _action('find_symbols'), _action('todo_report'), _action('git_changes')]
    if languages.get('Python'):
        result['actions'].insert(0, _action('python_syntax'))
    return result


def _find_files(scan, query):
    pattern = query.casefold().replace('\\', '/')
    matches = []
    for path in scan.files:
        if scan.stopped():
            break
        candidate = path.casefold()
        glob = any(char in pattern for char in '*?[')
        found = (fnmatch.fnmatchcase(candidate, pattern) or fnmatch.fnmatchcase(Path(candidate).name, pattern)) if glob else pattern in candidate
        if found:
            matches.append(path)
    count = len(matches)
    details = matches[:scan.limits.max_results]
    if count > len(details):
        details.append(f'Showing {len(details)} of {count} matches. Use a more specific name.')
    return _result('find_files', 'completed', f'{count} file matches in the inspected project.', details)


def _find_symbols(scan, query):
    hits = []
    for path in scan.files:
        if scan.stopped() or len(hits) >= scan.limits.max_results:
            break
        if Path(path).suffix.casefold() not in _LANGUAGES:
            continue
        source = scan.read(path)
        if source is None:
            continue
        symbols = []
        if path.lower().endswith('.py'):
            try:
                tree = ast.parse(source, filename=path)
                symbols = [(node.name, node.lineno) for node in ast.walk(tree) if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
            except (SyntaxError, ValueError, RecursionError):
                scan.skipped += 1
                if len(scan.notes) < 5:
                    scan.notes.append(f'{path}: Python declarations could not be parsed. Run Check Python syntax for diagnostics.')
        else:
            for number, line in enumerate(source.splitlines(), 1):
                if scan.stopped():
                    break
                if len(line) > 2000:
                    continue
                match = _DECLARATION.match(line)
                if match and not SENSITIVE_LINE.search(line):
                    symbols.append((next(name for name in match.groups() if name), number))
        for name, number in symbols:
            if query.casefold() in name.casefold() and not SENSITIVE_LINE.search(name):
                hits.append(f'{path}:{number} — {name}')
                if len(hits) >= scan.limits.max_results:
                    scan.limited = True
                    break
    return _result('find_symbols', 'completed', f'{len(hits)} matching declarations found. Non-Python language support uses declaration patterns.', hits)


def _todo_report(scan):
    hits = []
    for path in scan.files:
        if scan.stopped() or len(hits) >= scan.limits.max_results:
            break
        suffix = Path(path).suffix.casefold()
        if suffix not in _LANGUAGES and suffix not in {'.md', '.txt'}:
            continue
        source = scan.read(path)
        if source is None:
            continue
        for number, line in enumerate(source.splitlines(), 1):
            if _TODO.search(line) and not SENSITIVE_LINE.search(line):
                # Match comments/notes only, not arbitrary identifiers such as TODO_COUNT.
                before = line[:_TODO.search(line).start()]
                if suffix not in {'.md', '.txt'} and not re.search(r'#|//|/\*|^\s*\*|--|<!--', before):
                    continue
                hits.append(f'{path}:{number} — {line.strip()[:180]}')
                if len(hits) >= scan.limits.max_results:
                    scan.limited = True
                    break
    return _result('todo_report', 'completed', f'{len(hits)} TODO/FIXME/HACK/XXX notes found in the inspected files.', hits,
                   todo_count=len(hits))


def _python_syntax(scan):
    errors = []; parsed = 0
    for path in scan.files:
        if scan.stopped():
            break
        if not path.lower().endswith('.py'):
            continue
        source = scan.read(path)
        if source is None:
            continue
        try:
            compile(source, path, 'exec', dont_inherit=True)
            parsed += 1
        except SyntaxError as exc:
            # Never echo the source line; syntax diagnostics can contain secrets.
            errors.append(f'{path}:{exc.lineno or 1}:{exc.offset or 1} — {exc.msg[:160]}')
        except (ValueError, RecursionError):
            errors.append(f'{path} — Parser could not inspect this file.')
        if len(errors) >= scan.limits.max_results:
            scan.limited = True
            break
    details = errors + ['Syntax parsing uses this app\'s Python version. It does not execute imports, tests or application code, and does not check behavior.']
    return _result('python_syntax', 'completed', f'{parsed} Python files parsed; {len(errors)} syntax errors found.', details,
                   findings=len(errors))


def _fixed_git(scan, args):
    """Run only fixed Git queries; no shell, aliases, hooks or project scripts."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    command = ['git', '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false',
               '-c', 'core.hooksPath=' + os.devnull,
               '--no-pager', '-C', str(scan.root)] + args
    with tempfile.TemporaryFile() as output:
        # Git process overrides can point at a different index/worktree or enable
        # trace-file writes. Keep this query tied to the selected directory.
        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
        environment.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0')
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.DEVNULL, shell=False, creationflags=flags,
                                   env=environment)
        try:
            while process.poll() is None:
                if scan.stopped() or os.fstat(output.fileno()).st_size > 65536:
                    scan.limited = True
                    process.kill()
                    break
                try:
                    process.wait(timeout=0.04)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
        output.seek(0)
        return process.returncode, output.read(65537)


def _git_changes(scan):
    try:
        code, raw = _fixed_git(scan, ['rev-parse', '--show-toplevel'])
        if code:
            return _result('git_changes', 'unavailable', 'Git metadata is unavailable for this project.')
        top = Path(os.fsdecode(raw).strip()).resolve()
        if top != scan.root:
            return _result('git_changes', 'unavailable', 'Select the Git repository root to inspect its tracked changes.')
        code, raw = _fixed_git(scan, ['status', '--porcelain=v1', '-z', '--untracked-files=no', '--ignore-submodules=all', '--', '.'])
        if code:
            return _result('git_changes', 'unavailable', 'Git status did not finish. The project has not been changed.')
        rows = raw.decode('utf-8', errors='replace').split('\0'); details = []; count = 0; index = 0
        while index < len(rows):
            row = rows[index]; index += 1
            if len(row) < 4:
                continue
            state, path = row[:2], row[3:]
            if 'R' in state or 'C' in state:
                index += 1  # porcelain -z includes the original name as a second field
            if any(not _safe_name(part) for part in Path(path).parts):
                continue
            count += 1
            if len(details) < scan.limits.max_results:
                details.append(f'{state}  {path}')
        details.append('First status column: staged. Second: unstaged. M modified; A added; D deleted; R renamed; U conflicted. Untracked and excluded private/generated paths are omitted.')
        if len(raw) > 65536 or count > scan.limits.max_results:
            scan.limited = True
        return _result('git_changes', 'completed', f'{count} visible changed tracked files.', details)
    except FileNotFoundError:
        return _result('git_changes', 'unavailable', 'Git is not installed or is not available on PATH.')
    except (OSError, ValueError, subprocess.SubprocessError):
        return _result('git_changes', 'unavailable', 'Git could not inspect the selected project. No project files were changed.')


def _quick_check(scan):
    """A fixed read-only execution chain sharing one inventory and one budget."""
    stages = [('project_report', _project_report)]
    if any(path.lower().endswith('.py') for path in scan.files):
        stages.append(('python_syntax', _python_syntax))
    stages.extend([('todo_report', _todo_report), ('git_changes', _git_changes)])
    details = []; completed = []; syntax_errors = 0; todos = 0
    for command, handler in stages:
        if scan.stopped():
            details.append('Remaining quick-check stages were not run because the shared scan budget was reached.')
            break
        stage = handler(scan)
        completed.append(command)
        syntax_errors += stage.get('findings', 0)
        todos += stage.get('todo_count', 0)
        details.append(stage['title'] + ': ' + stage['summary'])
        details.extend(stage['details'][:20])
        if len(stage['details']) > 20:
            details.append(f'{len(stage["details"]) - 20} more details available by running {stage["title"]} separately.')
    result = _result('quick_check', 'completed',
        f'{len(completed)} local skills inspected this project: {syntax_errors} Python syntax errors and {todos} unfinished-work notes found.',
        details, findings=syntax_errors, todo_count=todos)
    result['details'].append('No application code, tests, package scripts, imports or model calls were executed. Git was queried only for tracked status.')
    result['actions'] = [_action('project_report'), _action('python_syntax'), _action('todo_report'), _action('git_changes')]
    result['metrics']['skills_run'] = completed
    return result


def run_offline(project, request='', *, command=None, query='', cancel=None, limits=None):
    """Run an explicit local skill and return a JSON-serializable UI result."""
    if command is None:
        recognized = recognize_request(request)
        if recognized is None:
            return _result('', 'unsupported', 'Choose an offline skill or use a supported request such as “What is in this project?”.',
                           actions=[_action(item['id']) for item in OFFLINE_COMMANDS])
        command, query = recognized['command'], recognized['query']
    if command not in _COMMANDS:
        return _result('', 'unsupported', 'That offline command is not supported.')
    if command in {'find_files', 'find_symbols'} and (not isinstance(query, str) or not query.strip() or len(query) > 160):
        return _result(command, 'needs_input', 'Enter a file name, wildcard or symbol to find.')
    try:
        if not project:
            raise ValueError()
        root = Path(project).expanduser().resolve()
        if not root.is_dir():
            raise ValueError()
    except (OSError, ValueError, TypeError):
        return _result(command, 'needs_project', 'Open an existing project folder to use this offline skill.')
    scan = _Scan(root, cancel, limits or ScanLimits(max_seconds=10.0 if command == 'quick_check' else 4.0))
    try:
        if scan.stopped():
            return scan.finish(_result(command, 'partial', 'The scan time limit was reached.'))
        if command == 'git_changes':
            return scan.finish(_git_changes(scan))
        scan.inventory()
        handlers = {'quick_check': _quick_check, 'project_report': _project_report, 'find_files': lambda item: _find_files(item, query.strip()),
                    'find_symbols': lambda item: _find_symbols(item, query.strip()),
                    'todo_report': _todo_report, 'python_syntax': _python_syntax}
        return scan.finish(handlers[command](scan))
    except InterruptedError:
        return _result(command, 'cancelled', 'Offline scan cancelled. No project files were changed.')


def main(argv=None):
    """A desktop-independent command line entry point; never starts a model."""
    import argparse
    parser = argparse.ArgumentParser(description='Run a read-only TalkToAi local project skill without an LLM or API.')
    parser.add_argument('--project', required=True, help='Selected project folder')
    parser.add_argument('--command', required=True, choices=sorted(_COMMANDS))
    parser.add_argument('--query', default='', help='Name or pattern for file/symbol searches')
    parser.add_argument('--json', action='store_true', help='Print structured JSON')
    args = parser.parse_args(argv)
    result = run_offline(args.project, command=args.command, query=args.query)
    if args.json:
        print(json.dumps(result, ensure_ascii=True, indent=2))
    else:
        print(result['title'] + '\n' + result['summary'])
        for detail in result['details']:
            print('  ' + detail)
        print('Status: ' + result['status'] + ' | 0 model calls | 0 network calls')
    if result['status'] == 'partial':
        return 2
    if result['status'] != 'completed' or result.get('findings', 0):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
