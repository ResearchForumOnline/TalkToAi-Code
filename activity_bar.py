"""The small activity bar at the bottom of the window: what the agent is doing right now.

ActivityFeed is plain Python and holds the text. ActivityBar is a thin Qt view
of it, created only by the app window.
"""
from collections import deque
import json
import time


_LIMIT = 96


def _short(text, limit=_LIMIT):
    text = ' '.join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _argument(args, *names):
    for name in names:
        value = args.get(name) if isinstance(args, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


_VERBS = {
    'read_file': ('Reading', 'path'), 'read_project_files': ('Reading files', None), 'list_files': ('Listing', 'path'),
    'search_code': ('Searching the code for', 'query', 'pattern'), 'project_info': ('Inspecting the project', None),
    'project_map': ('Mapping the project', None), 'git_changes': ('Checking changes', None),
    'write_file': ('Writing', 'path'), 'write_file_checked': ('Writing', 'path'), 'edit_file': ('Editing', 'path'),
    'run_checks': ('Running the project checks', None), 'start_process': ('Starting', 'command'),
    'poll_process': ('Watching a running command', None), 'build_native': ('Building', 'path'),
    'install_tool': ('Installing', 'name'), 'web_search': ('Searching the web for', 'query'),
    'research': ('Researching', 'query', 'topic'), 'browser': ('Using the browser', 'url', 'action'),
    'computer': ('Operating the desktop', 'action'), 'ask_user': ('Noting a question', None),
    'update_plan': ('Updating the plan', None), 'blender_scene': ('Building the 3D scene', None),
}


def describe_tool(name, args=None):
    """One short line for a tool call, such as "Running: cmake --build build"."""
    name = str(name or 'tool')
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    args = args if isinstance(args, dict) else {}
    if name == 'run_command':
        command = _argument(args, 'command')
        return _short('Running: ' + command) if command else 'Running a command'
    if name == 'run_script':
        language = _argument(args, 'language') or 'a'
        code = _argument(args, 'code')
        first = next((line.strip() for line in code.splitlines() if line.strip() and not line.strip().startswith('#')), '')
        return _short(f'Running a {language} script' + (': ' + first if first else ''))
    if name == 'build_native' and not _argument(args, 'path'):
        return 'Building the C/C++ project'
    verb = _VERBS.get(name)
    if verb is None:
        return _short('Using ' + name.replace('_', ' '))
    detail = _argument(args, *[field for field in verb[1:] if field])
    return _short(verb[0] + (' ' + detail if detail else ''))


class ActivityFeed:
    """Current action, its start time, and a short history. Times use a monotonic clock."""

    def __init__(self, clock=time.monotonic, keep=40):
        self.clock = clock
        self.entries = deque(maxlen=keep)
        self.current = None
        self.started = None
        self.actions = 0
        self.busy = False

    def begin(self, text='Starting…'):
        self.busy = True
        self.actions = 0
        self._set(text)

    def tool(self, name, args=None):
        self.actions += 1
        self._set(describe_tool(name, args))

    def status(self, text):
        # Status lines are shown when no tool is running, so a long command stays visible.
        if self.busy and text and (self.current is None or not self.current.startswith(('Running', 'Building', 'Installing'))):
            self._set(_short(text))

    def result(self, text=''):
        if self.current and isinstance(text, str):
            failed = text.startswith('Exit ') and not text.startswith('Exit 0')
            if failed or text.startswith(('Tool error', 'Error')):
                self.entries.append((self.clock(), self.current + ' · failed'))
        if self.current and self.current.startswith(('Running', 'Building', 'Installing')):
            self.current = None

    def finish(self, text=''):
        self.busy = False
        summary = text or (f'Done · {self.actions} action' + ('s' if self.actions != 1 else '') if self.actions else 'Done')
        self._set(summary)

    def _set(self, text):
        self.current = text
        self.started = self.clock()
        self.entries.append((self.started, text))

    def line(self):
        """The text the bar shows now, with the elapsed time of a running action."""
        if not self.current:
            return 'Working…' if self.busy else ''
        if not self.busy:
            return self.current
        seconds = int(self.clock() - (self.started or self.clock()))
        return self.current + (f' · {seconds // 60}m {seconds % 60:02d}s' if seconds >= 60 else f' · {seconds}s' if seconds >= 3 else '')

    def history(self, count=12):
        return [text for _, text in list(self.entries)[-count:]]


def create_activity_bar(parent=None):
    """The Qt widget. Imported lazily so the feed above needs no Qt."""
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy

    class ActivityBar(QFrame):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setObjectName('activity_bar')
            self.setAccessibleName('Agent activity')
            self.feed = ActivityFeed()
            self.setStyleSheet('#activity_bar{background:#10241f;border:1px solid #2f6f5f;border-radius:14px;}'
                               '#activity_bar QLabel{color:#d9fff1;background:transparent;}')
            self.setFixedHeight(30)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            row = QHBoxLayout(self)
            row.setContentsMargins(12, 2, 12, 2)
            self.light = QLabel('●')
            self.light.setStyleSheet('color:#65bea5;')
            self.text = QLabel()
            self.text.setTextFormat(Qt.PlainText)
            self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(self.light)
            row.addWidget(self.text, 1)
            self.timer = QTimer(self)
            self.timer.setInterval(1000)
            self.timer.timeout.connect(self.refresh)
            self.hide_timer = QTimer(self)
            self.hide_timer.setSingleShot(True)
            self.hide_timer.timeout.connect(self.hide)
            self.hide()

        def begin(self, text='Starting…'):
            self.hide_timer.stop()
            self.feed.begin(text)
            self.show()
            self.timer.start()
            self.refresh()

        def event_received(self, kind, data):
            if kind == 'tool' and isinstance(data, dict):
                self.feed.tool(data.get('name'), data.get('args'))
            elif kind == 'status' and isinstance(data, str):
                self.feed.status(data)
            elif kind == 'result':
                self.feed.result(str(data))
            else:
                return
            self.refresh()

        def finish(self, text=''):
            self.feed.finish(text)
            self.timer.stop()
            self.refresh()
            self.hide_timer.start(8000)

        def refresh(self):
            line = self.feed.line()
            metrics = self.text.fontMetrics()
            self.text.setText(metrics.elidedText(line, Qt.ElideRight, max(120, self.text.width())))
            self.setToolTip('\n'.join(self.feed.history()))
            self.light.setStyleSheet('color:#65bea5;' if self.feed.busy else 'color:#6c7f7a;')

    return ActivityBar(parent)
