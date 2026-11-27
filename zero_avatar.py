"""Local artwork and live status for Agent Zero."""

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from activity_bar import describe_tool


ASSETS = Path(__file__).resolve().parent / 'assets' / 'zero-avatar'
TILE = 180
COLUMNS = 12
CLIPS = {
    'idle': ('idle', 128, True),
    'waking': ('waking', 32, False),
    'connecting': ('idle', 128, True),
    'working': ('working', 128, True),
    'waiting': ('idle', 128, True),
    'making': ('making', 128, True),
    'complete': ('complete', 80, False),
}
BUILD_TOOLS = {
    'write_file', 'write_file_checked', 'edit_file', 'apply_patch', 'build_native',
    'blender_scene', 'create_project', 'generate_image', 'generate_video',
}


class AvatarPortrait(QWidget):
    """Play a small sheet of image frames without a system media codec."""

    ended = Signal()

    def __init__(self, assets, parent=None):
        super().__init__(parent)
        self.setFixedSize(150, 132)
        self.setAccessibleName('ZERO portrait')
        self.assets = assets
        self.still = QImage(str(assets / 'still.jpg'))
        self.sheet = QImage()
        self.clip = ''
        self.frame = 0
        self.count = 0
        self.loop = False
        self.motion = True
        self.finished = False
        self.timer = QTimer(self, interval=63)
        self.timer.timeout.connect(self.advance)

    def play(self, state):
        name, count, loop = CLIPS[state]
        if name == self.clip and not self.finished:
            return
        self.clip = name
        self.count = count
        self.loop = loop
        self.frame = 0
        self.finished = False
        self.sheet = QImage()
        if self.motion:
            self.load_sheet()
        self.sync_playback()
        self.update()

    def load_sheet(self):
        sheet = QImage(str(self.assets / (self.clip + '.jpg')))
        expected_height = ((self.count + COLUMNS - 1) // COLUMNS) * TILE
        if sheet.width() == COLUMNS * TILE and sheet.height() == expected_height:
            self.sheet = sheet

    def set_motion(self, enabled):
        self.motion = enabled
        if enabled and self.clip and self.sheet.isNull():
            self.load_sheet()
        elif not enabled:
            self.sheet = QImage()
        self.sync_playback()
        self.update()

    def sync_playback(self):
        if (self.motion and self.isVisible() and not self.window().isMinimized()
                and not self.sheet.isNull() and not self.finished):
            if not self.timer.isActive():
                self.timer.start()
        else:
            self.timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self.sync_playback()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def advance(self):
        if self.window().isMinimized():
            self.timer.stop()
            return
        self.frame += 1
        if self.frame == self.count:
            if self.loop:
                self.frame = 0
            else:
                self.finished = True
                self.timer.stop()
                self.ended.emit()
                return
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        bounds = QRectF(self.rect())
        shape = QPainterPath()
        shape.addRoundedRect(bounds, 10, 10)
        painter.setClipPath(shape)
        painter.fillRect(bounds, QColor('#080911'))
        target = QRectF(9, 0, 132, 132)
        if self.sheet.isNull():
            painter.drawImage(target, self.still)
        else:
            source = QRectF((self.frame % COLUMNS) * TILE, (self.frame // COLUMNS) * TILE, TILE, TILE)
            painter.drawImage(target, self.sheet, source)
        shade = QLinearGradient(0, 0, self.width(), 0)
        shade.setColorAt(0, QColor('#080911'))
        shade.setColorAt(.16, QColor(8, 9, 17, 0))
        shade.setColorAt(.84, QColor(8, 9, 17, 0))
        shade.setColorAt(1, QColor('#080911'))
        painter.fillRect(bounds, shade)


class ZeroAvatar(QFrame):
    motion_changed = Signal(bool)

    def __init__(self, parent=None, *, motion=True, assets=ASSETS):
        super().__init__(parent)
        self.setObjectName('zeroAvatar')
        self.setAccessibleName('Agent Zero live status')
        self.setStyleSheet('''
            QFrame#zeroAvatar {border:1px solid #38425f; border-radius:13px;
              background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #16172b, stop:.6 #111525, stop:1 #090a12);}
            QFrame#zeroAvatar QLabel {background:transparent; border:0;}
            QFrame#zeroAvatar QPushButton {background:#1b2133; border:1px solid #434b66;
              color:#e0e5f5; border-radius:7px; padding:5px 9px;}
            QFrame#zeroAvatar QPushButton:hover {background:#29314a;}
        ''')
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 5, 15, 5)
        row.setSpacing(18)
        self.portrait = AvatarPortrait(assets, self)
        self.portrait.ended.connect(self.next_clip)
        row.addWidget(self.portrait)
        copy = QVBoxLayout()
        copy.setContentsMargins(0, 6, 0, 6)
        copy.setSpacing(5)
        copy.addStretch()
        title = QLabel('Agent Zero')
        title.setStyleSheet('color:#f2effa; font-size:24px; font-weight:700;')
        copy.addWidget(title)
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        self.status.setStyleSheet('color:#c3ccff; font-size:13px; font-weight:600;')
        copy.addWidget(self.status)
        copy.addStretch()
        row.addLayout(copy, 1)
        self.motion_button = QPushButton()
        self.motion_button.clicked.connect(self.toggle_motion)
        row.addWidget(self.motion_button, 0, Qt.AlignVCenter)
        self.state = 'idle'
        self.label = 'Ready to help'
        self.run_failed = False
        self.after_wake = 'connecting'
        self.set_motion(motion)
        self.display('idle', self.label)

    def set_motion(self, enabled):
        self.portrait.set_motion(enabled)
        self.motion_button.setText('Pause motion' if enabled else 'Enable motion')
        if not enabled and self.state in ('waking', 'complete'):
            self.next_clip()

    def toggle_motion(self):
        enabled = not self.portrait.motion
        self.set_motion(enabled)
        self.motion_changed.emit(enabled)

    def display(self, state, label):
        self.state, self.label = state, label
        self.status.setText(label)
        self.portrait.play(state)
        if state in ('waking', 'complete') and (not self.portrait.motion or self.portrait.sheet.isNull()):
            self.next_clip()

    def next_clip(self):
        if self.state == 'waking':
            self.display(self.after_wake, self.label)
        elif self.state == 'complete':
            self.display('idle', self.label)

    def begin(self, kind):
        self.run_failed = False
        self.after_wake = 'working' if kind in ('local', 'media', 'tuning') else 'connecting'
        label = {'local': 'Running a local program', 'media': 'Creating media',
                 'tuning': 'Checking model performance'}.get(kind, 'Connecting to the selected model')
        self.display('waking', label)

    def event_received(self, kind, data):
        if kind == 'error':
            self.run_failed = True
            self.display('idle', 'This run needs attention')
        if self.run_failed:
            return
        if kind == 'route':
            self.display('working', 'Waiting for the model')
        elif kind == 'delta':
            if self.label != 'Writing a reply':
                self.display('working', 'Writing a reply')
        elif kind == 'tool' and isinstance(data, dict):
            name = data.get('name')
            label = 'Waiting for a project reviewer' if name == 'delegate_review' else describe_tool(name, data.get('args'))
            self.display('making' if name in BUILD_TOOLS else 'waiting', label)
        elif kind == 'result':
            self.display('working', 'Tool result received')
        elif kind == 'local_scene_fallback':
            self.display('making', 'Building the scene locally')

    def progress(self, elapsed, step):
        pieces = [self.label]
        if step:
            pieces.append(f'Step {step}')
        pieces.append(f'{elapsed // 60}m {elapsed % 60:02d}s' if elapsed >= 60 else f'{elapsed}s')
        text = '  ·  '.join(pieces)
        if self.status.text() != text:
            self.status.setText(text)

    def finish(self, outcome):
        if self.run_failed:
            outcome = 'This run needs attention'
        self.display('complete' if outcome in ('Reply complete', 'Task complete') else 'idle', outcome)
