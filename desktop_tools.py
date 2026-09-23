"""Current-user desktop tools for TalkToAi Code.

Commands run as the signed-in Windows user. File tools operate under the user
profile and deliberately exclude credential material from agent reads/writes.
"""
from __future__ import annotations

import difflib
import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path


SKIP = {"AppData", ".ssh", ".codex", ".talktoai-code", ".git", "node_modules", "__pycache__", ".venv", "venv", "Library", "Temp", "obj", "bin", "vendor", "dist", "build"}
SECRET_NAMES = {".env", "credentials.json", "tokens.json", "id_rsa", "id_ed25519", "known_hosts"}
SECRET_SUFFIXES = {".pem", ".key", ".pfx", ".kdbx", ".dpapi"}


class DesktopTools:
    def __init__(self, act=False, cancel=None, state=None):
        self.root = Path.home().resolve()
        self.act = act
        self.cancel = cancel
        self.state = Path(state or os.environ.get("LOCALAPPDATA", str(self.root))) / "TalkToAiCode" / "desktop-checkpoints"
        self.changes = []

    def path(self, relative):
        target = (self.root / str(relative)).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError("Desktop path must stay inside the signed-in user's profile.")
        parts = {part.lower() for part in target.relative_to(self.root).parts}
        name = target.name.lower()
        if parts.intersection({".ssh", "appdata"}) or name in SECRET_NAMES or name.startswith(".env.") or target.suffix.lower() in SECRET_SUFFIXES:
            raise PermissionError("Credential and private configuration files are excluded from agent file tools.")
        return target

    def files(self):
        result = []
        for directory, folders, files in os.walk(self.root, followlinks=False):
            current = Path(directory)
            rel_parts = current.relative_to(self.root).parts if current != self.root else ()
            folders[:] = sorted(x for x in folders if x.lower() not in {s.lower() for s in SKIP} and not (current / x).is_symlink())
            if any(part.lower() in {s.lower() for s in SKIP} for part in rel_parts):
                folders[:] = []
                continue
            for name in sorted(files):
                p = current / name
                try:
                    self.path(p.relative_to(self.root))
                except (PermissionError, ValueError):
                    continue
                if not p.is_symlink():
                    result.append(str(p.relative_to(self.root)))
                if len(result) >= 2000:
                    return result
        return result

    def _command(self, command, cwd=None):
        if not self.act:
            raise PermissionError("Plan mode only permits desktop inventory, listing and reading.")
        if self.cancel and self.cancel.is_set():
            raise InterruptedError("Task stopped.")
        workdir = self.path(cwd or ".")
        if workdir.is_file():
            workdir = workdir.parent
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", str(command)], cwd=workdir, stdout=output, stderr=subprocess.STDOUT, creationflags=flags)
            deadline = time.monotonic() + 180
            while process.poll() is None:
                if (self.cancel and self.cancel.wait(.1)) or time.monotonic() > deadline:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, creationflags=flags)
                    process.wait(timeout=10)
                    raise InterruptedError("Desktop command stopped or reached its 180-second limit.")
            output.seek(0, 2);size=output.tell();output.seek(max(0,size-30000))
            return f"Exit {process.returncode}\n" + output.read().decode("utf-8", errors="replace")

    def execute(self, name, args):
        if name == "desktop_list":
            return "\n".join(self.files())
        if name == "desktop_read_file":
            p=self.path(args["path"])
            if p.stat().st_size > 500000:raise ValueError("Desktop file exceeds the 500 KB text limit.")
            return p.read_text(encoding="utf-8")
        if name == "desktop_write_file":
            if not self.act:raise PermissionError("Plan mode does not permit desktop edits.")
            p=self.path(args["path"]);content=args["content"]
            if len(content.encode("utf-8"))>800000:raise ValueError("Generated desktop file exceeds the 800 KB limit.")
            old=p.read_bytes() if p.exists() else None;before=old.decode("utf-8") if old is not None else ""
            checkpoint=self.state/uuid.uuid4().hex;checkpoint.mkdir(parents=True,exist_ok=True)
            if old is not None:(checkpoint/"original").write_bytes(old)
            (checkpoint/"record.json").write_text(json.dumps({"path":str(p),"existed":old is not None,"new_content":content}),encoding="utf-8")
            p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content.encode("utf-8"))
            diff="".join(difflib.unified_diff(before.splitlines(True),content.splitlines(True),fromfile=str(p),tofile=str(p)))
            change={"path":"~\\"+str(p.relative_to(self.root)),"diff":diff,"checkpoint":str(checkpoint)};self.changes.append(change)
            return f"Saved {change['path']}. Checkpoint: {checkpoint.name}\n{diff[:18000]}"
        if name == "desktop_run_command":
            return self._command(args.get("command", ""), args.get("cwd", "."))
        raise ValueError(f"Unknown desktop tool: {name}")
