"""User-owned state paths shared by the desktop and tool modules."""
import os
import sys
from pathlib import Path


def state_dir(store=False):
    name = 'TalkToAiCodeStore' if store else 'TalkToAiCode'
    if sys.platform == 'win32':
        root = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    elif sys.platform == 'darwin':
        root = Path.home() / 'Library' / 'Application Support'
    else:
        root = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state')
    return root / name
