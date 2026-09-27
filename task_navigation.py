"""Small, deterministic task preflight for users who do not know tool names.

Only generic subject queries leave the machine. User text, paths and file names
are never copied into an automatic web search.
"""
import os
import re
from pathlib import Path
import time


_OFFLINE = re.compile(r'\b(?:offline|no (?:web|internet|search)|do not (?:browse|search)|don.t (?:browse|search))\b', re.I)
_BUILD = re.compile(r'\b(?:build|make|create|upgrade|improve|moderni[sz]e|redesign)\b', re.I)
_CURRENT = re.compile(r'\b(?:current|latest|modern|202[5-9]|best practice|research)\b', re.I)
_GAME = re.compile(r'\b(?:game|godot|unity|unreal|level|player|combat)\b', re.I)
_WEB = re.compile(r'\b(?:website|web app|webpage|landing page|frontend)\b', re.I)
_APP = re.compile(r'\b(?:app|application|desktop software|program)\b', re.I)
_FRESH = re.compile(r'\b(?:latest|current|recent|today|this week|new release|what changed|newest|up to date)\b', re.I)
_SENSITIVE_REQUEST = re.compile(r'(?:[a-z]:[\\/]|/home/|/users/|@|\b(?:password|token|credential|api key|private|inbox|mailbox|my account|my usage|billing|ssh|server|desktop)\b)', re.I)
_PRIVATE = {'private', 'secrets', 'credentials', 'passwords', 'tokens', 'appdata', '.ssh', '.codex', '.git',
            'node_modules', '.venv', 'venv', 'build', 'dist', 'backup', 'backups', 'archive', 'archives'}
_MARKERS = (('project.godot', 'Godot game'), ('ProjectSettings/ProjectVersion.txt', 'Unity game'),
            ('package.json', 'JavaScript project'), ('pyproject.toml', 'Python project'),
            ('Cargo.toml', 'Rust project'), ('.git', 'Git project'))
_NAMED_PROJECT = re.compile(
    r'\b(?:find|locate|open|inspect|check|improve|upgrade|fix|work on)\s+'
    r'(?:my|the|this)?\s*([a-z0-9][a-z0-9 _-]{1,70}?)\s+(?:game|project|app)\b', re.I)


def research_query(request, engine='General'):
    """Return a fixed public-docs query or None; never interpolate request data."""
    if _OFFLINE.search(request or ''):
        return None
    wants_research = bool(_CURRENT.search(request or ''))
    wants_build = bool(_BUILD.search(request or ''))
    if not (wants_research or wants_build):
        return None
    if _GAME.search(request or '') or engine == 'Godot':
        if engine == 'Unity' or re.search(r'\bunity\b', request or '', re.I):
            return 'site:docs.unity3d.com manual game development performance UI'
        if re.search(r'\bunreal\b', request or '', re.I):
            return 'site:dev.epicgames.com/documentation unreal engine game development performance UI'
        return 'site:docs.godotengine.org stable 3D game development performance UI'
    if _WEB.search(request or ''):
        return 'site:developer.mozilla.org web development accessibility responsive design'
    if _APP.search(request or '') and (wants_build or wants_research):
        return 'site:docs.python.org application development packaging accessibility'
    return None


def canonical_source(query):
    """A stable primary entry point when search engines return redirect links."""
    if 'site:godotengine.org' in query:
        return 'https://godotengine.org/download/archive/'
    if 'site:python.org' in query:
        return 'https://www.python.org/downloads/'
    if 'site:ollama.com' in query:
        return 'https://ollama.com/blog'
    if 'site:playwright.dev' in query:
        return 'https://playwright.dev/python/docs/release-notes'
    if 'docs.godotengine.org' in query:
        return 'https://docs.godotengine.org/en/stable/tutorials/performance/optimizing_3d_performance.html'
    if 'docs.unity3d.com' in query:
        return 'https://docs.unity3d.com/Manual/index.html'
    if 'dev.epicgames.com' in query:
        return 'https://dev.epicgames.com/documentation/en-us/unreal-engine/'
    if 'developer.mozilla.org' in query:
        return 'https://developer.mozilla.org/en-US/docs/Learn_web_development'
    if 'docs.python.org' in query:
        return 'https://docs.python.org/3/tutorial/'
    return None


def public_freshness_request(request):
    """Whether a Chat question may use public web tools without private context."""
    return bool(_FRESH.search(request or '')) and not _OFFLINE.search(request or '') and not _SENSITIVE_REQUEST.search(request or '')


def public_current_query(request):
    """Fixed official-source query for a few public software-release topics."""
    if not public_freshness_request(request):
        return None
    if re.search(r'\bgodot\b',request,re.I):
        return 'site:godotengine.org Godot Engine latest release'
    if re.search(r'\bpython\b',request,re.I):
        return 'site:python.org downloads latest Python release'
    if re.search(r'\bollama\b',request,re.I):
        return 'site:ollama.com/blog latest Ollama release'
    if re.search(r'\bplaywright\b',request,re.I):
        return 'site:playwright.dev/python/docs/release-notes latest Playwright Python release'
    return None


def desktop_projects(home=None, max_projects=40, max_seconds=2.0):
    """Find project manifests in Desktop/Documents without reading file content."""
    root = Path(home).resolve() if home else Path.home().resolve()
    deadline = time.monotonic() + max_seconds
    results = []
    queue = []
    for label in ('Desktop', 'Documents'):
        base = root / label
        if not base.is_dir() or base.is_symlink():
            continue
        queue = [(base, 0)]
        while queue and len(results) < max_projects and time.monotonic() < deadline:
            folder, depth = queue.pop(0)
            if folder.is_symlink() or folder.name.lower() in _PRIVATE:
                continue
            try:
                found = next((kind for marker, kind in _MARKERS if (folder / marker).exists()), None)
                if found:
                    results.append({'path': str(folder), 'kind': found})
                if depth < 3:
                    children = sorted((p for p in folder.iterdir() if p.is_dir() and not p.is_symlink()
                                       and p.name.lower() not in _PRIVATE), key=lambda p: p.name.lower())[:80]
                    queue.extend((p, depth + 1) for p in children)
            except OSError:
                continue
    return {'projects': results, 'bounded': bool(queue) or time.monotonic() >= deadline,
            'scope': 'Desktop and Documents project manifests only; no file contents'}


def natural_desktop_target(text, home=None, max_directories=1200, max_seconds=3.0):
    """Resolve a named Desktop/Documents project from ordinary wording.

    Returns None when the user did not name a project and a fail-closed result
    for ties or incomplete scans. Does not read manifest contents.
    """
    match = _NAMED_PROJECT.search(text or '')
    if not match or not re.search(r'\b(?:desktop|documents)\b', text or '', re.I):
        return None
    name = match.group(1).strip()
    wanted = ' '.join(re.findall(r'[a-z0-9]+', name.casefold()))
    if not wanted:
        return None
    labels = ('Desktop', 'Documents') if re.search(r'\bboth\b|\bdesktop\b.*\bdocuments\b', text, re.I) else ('Desktop',) if re.search(r'\bdesktop\b', text, re.I) else ('Documents',)
    root = Path(home).resolve() if home else Path.home().resolve()
    deadline = time.monotonic() + max_seconds
    queue = []
    for label in labels:
        base = root / label
        if base.is_dir() and not base.is_symlink():
            queue.append((base, 0))
    named = []
    incomplete = False
    visited = 0
    # Most real projects sit inside one Desktop workspace folder. Inspect two
    # directory levels for the name, then ask the existing project resolver to
    # choose the actual engine root inside that narrow branch.
    while queue and visited < max_directories and time.monotonic() < deadline:
        folder, depth = queue.pop(0)
        visited += 1
        if folder.is_symlink() or folder.name.casefold() in _PRIVATE or folder.name.startswith('.'):
            continue
        leaf = ' '.join(re.findall(r'[a-z0-9]+', folder.name.casefold()))
        if depth and wanted in leaf:
            named.append(folder)
            continue
        if depth >= 2:
            continue
        try:
            with os.scandir(folder) as entries:
                children = []
                for entry in entries:
                    if time.monotonic() >= deadline:
                        incomplete = True
                        break
                    if entry.name.startswith('.') or entry.name.casefold() in _PRIVATE or entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        children.append(Path(entry.path))
                    if len(children) > max_directories:
                        incomplete = True
                        break
            queue.extend((child, depth + 1) for child in sorted(children, key=lambda p: p.name.casefold()))
        except OSError:
            incomplete = True
    incomplete = incomplete or bool(queue) or visited >= max_directories or time.monotonic() >= deadline
    found = []
    if not incomplete:
        from project_context import resolve_project_target
        for folder in named:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                incomplete = True
                break
            target = resolve_project_target(folder, 'game: '+name, max_directories=max_directories,
                                            max_depth=4, timeout=remaining)
            if target.get('truncated'):
                incomplete = True
                break
            if not target.get('error'):
                chosen = Path(target['root'])
                if chosen not in found:
                    found.append(chosen)
    candidates = [str(path) for path in found[:8]]
    result = {'root': str(root), 'name': name, 'reason': 'Named project discovery on '+', '.join(labels)+'.',
              'candidates': candidates, 'truncated': incomplete}
    if incomplete:
        result['error'] = 'Desktop project discovery reached its safety limit; choose the project folder explicitly.'
    elif not found:
        result['error'] = 'Could not find the named project in '+', '.join(labels)+'; choose its folder explicitly.'
    elif len(found) > 1:
        result['error'] = 'Multiple projects match '+name+'; choose the intended folder.'
    else:
        result.update(root=str(found[0]), reason='Located '+name+' from a unique project directory and engine marker.')
    return result
