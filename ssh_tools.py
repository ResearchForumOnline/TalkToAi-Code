"""Small, metadata-only OpenSSH integration for TalkToAi Code.

The app deliberately delegates authentication to the user's OpenSSH config,
agent, and keychain.  It never reads private keys or credential files.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path


SAFE_ALIAS = re.compile(r"^[A-Za-z0-9_.@:-]{1,128}$")


def validate_alias(alias: str) -> str:
    value = str(alias or "").strip()
    if not SAFE_ALIAS.fullmatch(value) or value.startswith("-"):
        raise ValueError("SSH host alias must be a simple OpenSSH config alias.")
    return value


class SSHProfile:
    def __init__(self, label: str, alias: str, remote_path: str = ""):
        self.label = str(label or alias).strip()[:80]
        self.alias = validate_alias(alias)
        self.remote_path = str(remote_path or "").strip()[:500]

    def as_dict(self):
        return {"label": self.label, "alias": self.alias, "remote_path": self.remote_path}


class SSHSession:
    def __init__(self, profile: SSHProfile, timeout: int = 12):
        self.profile = profile
        self.timeout = timeout

    def _run(self, args, timeout=None):
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        return subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={self.timeout}",
             self.profile.alias, *args],
            capture_output=True, text=True, timeout=timeout or self.timeout + 8,
            creationflags=flags,
        )

    def resolve(self):
        """Validate the alias without connecting and omit sensitive fields."""
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        result = subprocess.run(
            ["ssh", "-G", self.profile.alias], capture_output=True, text=True,
            timeout=8, creationflags=flags,
        )
        if result.returncode:
            raise RuntimeError((result.stderr or "SSH alias was not found.").strip()[-1200:])
        fields = {}
        for line in result.stdout.splitlines():
            key, _, value = line.partition(" ")
            if key in {"hostname", "user", "port"}:
                fields[key] = value.strip()
        return fields

    def test(self):
        result = self._run(["printf", "TALKTOAI_SSH_OK"], timeout=self.timeout + 8)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "SSH connection failed.").strip()[-1600:])
        if "TALKTOAI_SSH_OK" not in result.stdout:
            raise RuntimeError("SSH connected but the verification marker was not returned.")
        resolved = self.resolve()
        return "SSH connected: " + resolved.get("user", "configured user") + "@" + resolved.get("hostname", self.profile.alias) + ":" + resolved.get("port", "22")

    def open_terminal(self):
        flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
        subprocess.Popen(["powershell.exe", "-NoExit", "-Command", "ssh", self.profile.alias], creationflags=flags)

    def run(self, command: str, cwd: str = "", cancel=None):
        command = str(command or "").strip()
        if not command:
            raise ValueError("Remote command cannot be empty.")
        remote_cwd = str(cwd or self.profile.remote_path or "").strip()
        if remote_cwd:
            directory = ('"$HOME"' + _quote_posix(remote_cwd[1:])) if remote_cwd == '~' or remote_cwd.startswith('~/') else _quote_posix(remote_cwd)
            command = "cd -- " + directory + " && " + command
        if cancel is None:
            result = self._run([command], timeout=180)
        else:
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            process = subprocess.Popen(['ssh', '-o', 'BatchMode=yes', '-o', f'ConnectTimeout={self.timeout}', self.profile.alias, command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=flags)
            deadline = time.monotonic() + 180
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=.15)
                    result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
                    break
                except subprocess.TimeoutExpired:
                    if cancel.is_set() or time.monotonic() >= deadline:
                        process.kill(); process.communicate()
                        raise InterruptedError('SSH client stopped. A detached remote process may still be running.')
        output = (result.stdout or "") + (result.stderr or "")
        return f"Exit {result.returncode}\n" + output[-20000:]


def _quote_posix(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def load_profiles(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [SSHProfile(x.get("label", ""), x["alias"], x.get("remote_path", "")) for x in data if isinstance(x, dict) and x.get("alias")]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def save_profiles(path: Path, profiles):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([p.as_dict() for p in profiles], indent=2), encoding="utf-8")
    tmp.replace(path)
