"""Bounded source navigation, inspired by public repository-map workflows."""
import ast
from pathlib import Path
import re
import subprocess
import os
import time

PROJECT_MARKERS = ('project.godot', 'package.json', 'pyproject.toml', 'Cargo.toml', 'go.mod', 'CMakeLists.txt', '.git')
DISCOVERY_SKIP = {'node_modules', 'vendor', 'build', 'dist', 'bin', 'obj', 'library', 'temp',
                  'backups', 'backup', 'artifacts', 'output', 'outputs', 'upstream', '__pycache__', 'venv'}


def requested_runtime(text, server_label='Server'):
    """Recognize an affirmative routing instruction embedded in a longer task."""
    names='|'.join(re.escape(name) for name in ('server','amd',server_label.strip()) if name)
    return 'server' if re.search(r'(?:^|[\n.!;,])\s*(?:please\s+)?(?:always\s+)?(?:use|switch to)\s+(?:my\s+)?(?:'+names+r')\b', text, re.I) else None


def explicit_project_directory(text):
    """Use only existing absolute directories actually written by the user."""
    pattern = r'(?<!\w)(?:[A-Za-z]:[\\/]|/)[^\r\n<>|\"\x27]*'
    for match in re.finditer(pattern, text):
        raw = match.group().strip()
        # Windows paths can contain spaces, so find the longest existing prefix.
        quoted = match.start() > 0 and text[match.start()-1] in ('"', "'")
        endings = [len(raw)] if quoted else [len(raw)] + [m.start() for m in re.finditer(r'\s|[,;]', raw)]
        for end in sorted(set(endings), reverse=True):
            candidate = raw[:end].rstrip(' .,;:')
            try:
                path = Path(candidate).expanduser()
                if path.is_absolute() and path.is_dir():
                    return path.resolve()
            except (OSError, ValueError):
                continue
    return None


def resolve_project_target(selected, text, *, max_directories=800, max_depth=4, timeout=2.0):
    """Resolve a named project inside the supplied workspace without broad file reads.

    Only directory names and the public Godot application name are inspected. A
    tied or incomplete discovery is surfaced to the user instead of guessed.
    """
    explicit = explicit_project_directory(text)
    root = explicit or Path(selected).expanduser().resolve()
    match = re.search(r'\b(?:game|project|app)\s*:\s*[\"\x27]?([^\n.,;:\"\x27]+)', text, re.I)
    name = match.group(1).strip() if match else ''
    result = {'root': str(root), 'name': name, 'reason': 'Explicit project directory.' if explicit else 'Selected project.', 'candidates': [], 'truncated': False}
    if not name or not root.is_dir():
        return result
    normalized = lambda value: ' '.join(re.findall(r'[a-z0-9]+', value.casefold()))
    wanted = normalized(name)
    queue = [(root, 0)]; found = []; visited = 0; entries = 0; deadline = time.monotonic() + timeout
    # A named top-level folder is stronger evidence than unrelated repositories.
    # Narrow before traversal so a desktop with hundreds of projects stays cheap.
    if wanted not in normalized(root.name):
        try:
            branches=[]
            with os.scandir(root) as listing:
                for entry in listing:
                    entries += 1
                    if entries > 20000 or time.monotonic() >= deadline:
                        result['truncated']=True;break
                    if (not entry.name.startswith('.') and entry.name.casefold() not in DISCOVERY_SKIP
                            and wanted in normalized(entry.name) and not entry.is_symlink()
                            and entry.is_dir(follow_symlinks=False)
                            and Path(entry.path).resolve().is_relative_to(root)):
                        branches.append(Path(entry.path))
            if branches: queue = [(p, 1) for p in sorted(branches)]
        except OSError: pass
    while queue:
        if visited >= max_directories or time.monotonic() >= deadline:
            result['truncated'] = True; break
        current, depth = queue.pop(0); visited += 1
        rel = current.relative_to(root)
        path_name = normalized(str(rel) if rel.parts else current.name)
        score = 80 if wanted and wanted in path_name else 0
        if normalized(current.name) == wanted: score = 100
        marker = current / 'project.godot'
        try:
            if marker.is_file() and not marker.is_symlink() and marker.stat().st_size <= 32768:
                title = re.search(r'^config/name="([^"\n]+)"', marker.read_text(encoding='utf-8', errors='replace'), re.M)
                if title and normalized(title.group(1)) == wanted: score = 120
                elif title and wanted in normalized(title.group(1)): score = max(score, 90)
            is_project = any((current / marker_name).exists() for marker_name in PROJECT_MARKERS)
            if score and is_project: found.append((score, current))
            if depth >= max_depth: continue
            children = []
            with os.scandir(current) as listing:
                for entry in listing:
                    entries += 1
                    if entries > 20000 or time.monotonic() >= deadline:
                        result['truncated'] = True; break
                    if entry.name.startswith('.') or entry.name.casefold() in DISCOVERY_SKIP or entry.is_symlink(): continue
                    if entry.is_dir(follow_symlinks=False) and Path(entry.path).resolve().is_relative_to(root): children.append(Path(entry.path))
            # Relevant branches first; stable order makes repeated requests predictable.
            children.sort(key=lambda p: (wanted not in normalized(p.name), p.name.casefold()))
            queue.extend((p, depth + 1) for p in children)
            queue.sort(key=lambda item: (wanted not in normalized(str(item[0].relative_to(root))), item[1], str(item[0]).casefold()))
            if result['truncated']: break
        except (OSError, ValueError): continue
    found.sort(key=lambda item: (-item[0], str(item[1]).casefold()))
    result['candidates'] = [str(path) for _, path in found[:8]]
    if found and (len(found) == 1 or found[0][0] > found[1][0]) and not result['truncated']:
        result.update(root=str(found[0][1]), reason=f'Located {name} from project name and engine markers.')
    else:
        result['error'] = (f'Multiple projects match {name}; select the intended project folder.' if found else f'Could not locate {name} within {root}. Select its project folder to continue.')
        if result['truncated']: result['error'] += ' The bounded discovery limit was reached.'
    return result

SOURCE_EXTENSIONS={'.py','.gd','.cs','.js','.ts','.tsx','.jsx','.cpp','.c','.h','.hpp','.rs','.go','.java','.lua','.md','.toml','.json','.godot','.tscn','.yaml','.yml','.txt','.shader','.gdshader'}
_MAP_DECLARATION = re.compile(
    r'^\s*(?:class(?:_name)?\s+|(?:async\s+)?(?:def|func|function)\s+|'
    r'(?:export\s+)?(?:class|function|const)\s+|'
    r'(?:public|private|protected|internal)\s+.*(?:\(|class\s+))', re.I)
_MAP_SENSITIVE_NAME = re.compile(r'^(?:secret|secrets|credential|credentials|token|tokens|password|passwords|key|keys)(?:[_.-]|$)', re.I)


def _map_relevance(relative, source, words):
    """Favor declared symbols over incidental text and path-name matches."""
    path = relative.casefold()
    body = source.casefold()
    declarations = '\n'.join(line.casefold() for line in source.splitlines()
                             if _MAP_DECLARATION.match(line))
    path_hits = sum(word in path for word in words)
    symbol_hits = sum(word in declarations for word in words)
    body_hits = sum(word in body for word in words)
    return 25 * symbol_hits + 8 * path_hits + 2 * body_hits

def source_text(tools,relative):
    path=tools.path(relative)
    if path.suffix.lower() not in SOURCE_EXTENSIONS or path.stat().st_size>200000:return None
    try:return path.read_text(encoding='utf-8')
    except (UnicodeError,OSError):return None

def search_code(tools,query):
    if not query or len(query)>300:raise ValueError('Use a nonempty literal search of at most 300 characters.')
    hits=[];scanned=0
    for relative in tools.files():
        if tools.cancel.is_set():raise InterruptedError('Stopped.')
        try:text=source_text(tools,relative)
        except (ValueError,PermissionError,OSError):continue
        if text is None:continue
        scanned+=len(text)
        for number,line in enumerate(text.splitlines(),1):
            if query.casefold() in line.casefold():
                hits.append(f'{relative}:{number}: {line.strip()[:240]}')
                if len(hits)>=60:return '\n'.join(hits)+'\n[60-hit limit; narrow the query.]'
        if scanned>8000000:break
    return '\n'.join(hits) if hits else 'No matches within the bounded source scan.'

def project_map(tools,query=''):
    words=re.findall(r'[a-zA-Z_]{3,}',query.lower())[:12]
    files=tools.files()
    files.sort(key=lambda p:(-sum(w in p.lower() for w in words),len(Path(p).parts),p))
    # A path-only ordering can miss a relevant symbol in a generically named
    # file after the 60-file output cap. Read a bounded candidate set first,
    # then rank its declarations. Keep the no-query path as cheap as before.
    candidates=[];scanned_bytes=0;deadline=time.monotonic()+2.0
    for relative in files:
        if tools.cancel.is_set():raise InterruptedError('Stopped.')
        if words and (scanned_bytes>=8_000_000 or time.monotonic()>=deadline):break
        if any(_MAP_SENSITIVE_NAME.match(part) for part in Path(relative).parts):continue
        try:text=source_text(tools,relative)
        except (ValueError,PermissionError,OSError):continue
        if text is None:continue
        scanned_bytes+=len(text.encode('utf-8'))
        score=_map_relevance(relative,text,words) if words else 0
        candidates.append((score,relative,text))
        if not words and len(candidates)>=60:break
    if words:candidates.sort(key=lambda item:(-item[0],len(Path(item[1]).parts),item[1]))
    output=[];size=0;seen=0
    for _,relative,text in candidates:
        seen+=1;symbols=[]
        if relative.endswith('.py'):
            try:
                tree=ast.parse(text)
                for node in ast.walk(tree):
                    if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                        symbols.append(f'  {node.lineno}: {text.splitlines()[node.lineno-1].strip()[:140]}')
            except SyntaxError:pass
        else:
            for number,line in enumerate(text.splitlines(),1):
                if re.match(r'\s*(?:class(?:_name)? |(?:async )?(?:func|function) |(?:export )?(?:class|function|const) |(?:public|private|protected|internal) .*(?:\(|class ))',line):
                    symbols.append(f'  {number}: {line.strip()[:140]}')
        entry=relative+'\n'+'\n'.join(symbols[:10])
        output.append(entry);size+=len(entry)
        if size>=6500 or seen>=60:break
    return '\n\n'.join(output)+'\n[Bounded overview; use search_code/read_file for exact code.]'

def git_changes(tools):
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    base=['git','-c','core.fsmonitor=false','--no-pager','-C',str(tools.root)]
    try:
        check=subprocess.run(base+['rev-parse','--show-toplevel'],capture_output=True,text=True,timeout=10,creationflags=flags)
    except FileNotFoundError:return 'Git is not installed.'
    if check.returncode:return 'Selected project is not inside a Git repository.'
    commands=[['status','--short','--','.'],['diff','--no-ext-diff','--no-textconv','--stat','--','.'],['diff','--cached','--no-ext-diff','--no-textconv','--stat','--','.']]
    output=[]
    for args in commands:
        result=subprocess.run(base+args,capture_output=True,text=True,timeout=15,creationflags=flags)
        if result.returncode:
            return 'Git query failed: exit '+str(result.returncode)+' for git '+args[0]+'\n'+(result.stderr or result.stdout)[:2000]
        output.append('git '+' '.join(args)+'\n'+(result.stdout+result.stderr)[:10000])
    return '\n'.join(output)
