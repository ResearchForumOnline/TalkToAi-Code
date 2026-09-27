"""Small, deterministic task preflight for users who do not know tool names.

Only generic subject queries leave the machine. User text, paths and file names
are never copied into an automatic web search.
"""
import re
from pathlib import Path
import time


_OFFLINE = re.compile(r'\b(?:offline|no (?:web|internet|search)|do not (?:browse|search)|don.t (?:browse|search))\b', re.I)
_BUILD = re.compile(r'\b(?:build|make|create|upgrade|improve|moderni[sz]e|redesign)\b', re.I)
_CURRENT = re.compile(r'\b(?:current|latest|modern|202[5-9]|best practice|research)\b', re.I)
_GAME = re.compile(r'\b(?:game|godot|unity|unreal|level|player|combat)\b', re.I)
_WEB = re.compile(r'\b(?:website|web app|webpage|landing page|frontend)\b', re.I)
_APP = re.compile(r'\b(?:app|application|desktop software|program)\b', re.I)
_PRIVATE = {'private', 'secrets', 'credentials', 'passwords', 'tokens', 'appdata', '.ssh', '.codex', '.git',
            'node_modules', '.venv', 'venv', 'build', 'dist', 'backup', 'backups', 'archive', 'archives'}
_MARKERS = (('project.godot', 'Godot game'), ('ProjectSettings/ProjectVersion.txt', 'Unity game'),
            ('package.json', 'JavaScript project'), ('pyproject.toml', 'Python project'),
            ('Cargo.toml', 'Rust project'), ('.git', 'Git project'))


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
