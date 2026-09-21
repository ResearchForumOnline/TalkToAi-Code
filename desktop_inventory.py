"""Non-secret desktop/server-login inventory.

This module reports presence and metadata only. It never opens SSH private keys,
password stores, token files, browser profiles, or the contents of .rdp files.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _agent_key_count():
    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        result = subprocess.run(["ssh-add", "-l"], capture_output=True, text=True, timeout=4, creationflags=flags)
        if result.returncode not in (0, 1):
            return None
        return sum(1 for line in result.stdout.splitlines() if line.strip() and "The agent has no identities" not in line)
    except (OSError, subprocess.SubprocessError):
        return None


def inspect_desktop(app_profiles=None):
    home = Path.home()
    ssh_dir = home / ".ssh"
    desktop_locations = [home / "Desktop", home / "OneDrive" / "Desktop"]
    desktop_locations = [p for p in desktop_locations if p.is_dir()]
    result = {
        "note": "Presence metadata only; private keys, passwords, tokens, browser data and file contents were not read.",
        "ssh_client": bool(shutil.which("ssh")),
        "ssh_agent_key_count": _agent_key_count(),
        "ssh": {
            "directory_present": ssh_dir.is_dir(),
            "config_present": (ssh_dir / "config").is_file(),
            "known_hosts_present": (ssh_dir / "known_hosts").is_file(),
            "public_key_count": len(list(ssh_dir.glob("*.pub"))) if ssh_dir.is_dir() else 0,
        },
        "desktop_locations": [str(p) for p in desktop_locations],
        "desktop_server_files": [],
        "saved_app_profiles": [p.as_dict() for p in (app_profiles or [])],
    }
    keywords = ("ssh", "server", "remote", "amd", "openzero", "rdp", "vps", "linux")
    seen = set()
    for desktop in desktop_locations:
        try:
            for item in desktop.iterdir():
                if not item.is_file() or item.suffix.lower() not in {".rdp", ".lnk", ".url"}:
                    continue
                if item.suffix.lower() == ".rdp" or any(word in item.stem.lower() for word in keywords):
                    relative = str(item.relative_to(desktop))
                    if relative not in seen:
                        seen.add(relative)
                        result["desktop_server_files"].append(relative)
        except OSError:
            continue
    result["desktop_server_files"] = result["desktop_server_files"][:100]
    return result
