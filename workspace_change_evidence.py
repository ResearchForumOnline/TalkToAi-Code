"""Bounded, content-based evidence of source changes in the selected project.

This observes files; it never opens credentials, follows links, or changes Git.
It is deliberately separate from editor checkpoints, since a shell can write
project source without using the editor tools.
"""

import hashlib
import os
from pathlib import Path
import time


SOURCE_SUFFIXES = frozenset({
    '.py', '.gd', '.tscn', '.tres', '.cs', '.js', '.jsx', '.ts', '.tsx',
    '.go', '.rs', '.c', '.cc', '.cpp', '.h', '.hpp', '.java', '.kt',
    '.swift', '.html', '.css', '.scss', '.vue', '.svelte', '.sh', '.ps1',
    '.toml', '.yaml', '.yml', '.xml', '.sql', '.shader', '.gdshader',
})
SOURCE_NAMES = frozenset({'project.godot', 'package.json', 'pyproject.toml', 'Cargo.toml', 'Dockerfile', 'Makefile'})
SKIP_DIRS = frozenset({
    '.git', '.godot', '.talktoai-code', '.venv', 'venv', 'node_modules',
    '__pycache__', 'Library', 'Temp', 'obj', 'bin', 'vendor', 'artifacts',
    'build', 'dist', '.next', '.cache', 'coverage',
})
PRIVATE_PARTS = ('secret', 'token', 'password', 'passwd', 'credential', 'private', 'id_rsa', 'id_ed25519',
                 'keyring', 'wallet', 'vault', 'profile')
PRIVATE_DIRS = frozenset({'.ssh', '.aws', '.azure', 'private', 'secrets', 'credentials',
                          'profile', 'profiles', 'browser profiles', 'user data', 'appdata'})


def _private_component(name):
    lower = name.casefold()
    return lower.startswith('.') or lower in PRIVATE_DIRS or any(part in lower for part in PRIVATE_PARTS)


def _eligible(relative):
    relative = Path(relative)
    name = relative.name
    lower = name.casefold()
    if any(_private_component(component) for component in relative.parts):
        return False
    if Path(name).suffix.lower() in {'.pem', '.key', '.pfx', '.dpapi'}:
        return False
    return Path(name).suffix.lower() in SOURCE_SUFFIXES or name in SOURCE_NAMES


class WorkspaceChangeTracker:
    """Compare a bounded source inventory to the start of this run.

    `complete` only describes the configured scan scope. It never promises to
    observe generated files, credentials, other directories, or remote hosts.
    """

    def __init__(self, root, *, max_files=4000, max_seconds=2.0, max_bytes=1_000_000):
        self.root = Path(root).resolve()
        self.max_files = max(1, int(max_files))
        self.max_seconds = max(.01, float(max_seconds))
        self.max_bytes = max(1, int(max_bytes))
        self.baseline, self.baseline_complete = self._scan()
        self.latest = self.baseline
        self.complete = self.baseline_complete
        self.changed = []

    def _scan(self):
        found = {}
        complete = True
        deadline = time.monotonic() + self.max_seconds
        def scan_error(_error):
            nonlocal complete
            complete = False
        for directory, folders, files in os.walk(self.root, followlinks=False, onerror=scan_error):
            if time.monotonic() > deadline:
                complete = False
                break
            folders[:] = sorted(folder for folder in folders
                                if folder not in SKIP_DIRS and not _private_component(folder)
                                and not (Path(directory) / folder).is_symlink())
            for name in sorted(files):
                path = Path(directory) / name
                if not _eligible(path.relative_to(self.root)):
                    continue
                if path.is_symlink():
                    continue
                if len(found) >= self.max_files or time.monotonic() > deadline:
                    complete = False
                    break
                try:
                    stat = path.stat()
                    if not path.is_file():
                        continue
                    if stat.st_size > self.max_bytes:
                        complete = False
                        continue
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    found[path.relative_to(self.root).as_posix()] = (stat.st_size, digest)
                except (OSError, ValueError):
                    complete = False
            if not complete and (len(found) >= self.max_files or time.monotonic() > deadline):
                break
        return found, complete

    def observe(self, reason):
        latest, scan_complete = self._scan()
        self.latest = latest
        self.complete = self.baseline_complete and scan_complete
        shared = self.baseline.keys() & latest.keys()
        changed = {path for path in shared if self.baseline[path] != latest[path]}
        if self.baseline_complete:
            changed.update(latest.keys() - self.baseline.keys())
        if scan_complete:
            changed.update(self.baseline.keys() - latest.keys())
        self.changed = sorted(changed)
        return self.report(reason)

    def report(self, reason='checkpoint'):
        revision = hashlib.sha256(repr([(path, self.latest.get(path)) for path in self.changed]).encode('utf-8')).hexdigest()
        return {
            'count': len(self.changed),
            'paths': self.changed[:30],
            'more_paths': max(0, len(self.changed) - 30),
            'complete': self.complete,
            'revision': revision,
            'reason': reason,
        }
