"""Visible, non-activating desktop-control session indicator."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QApplication


CONTROL_TOOLS={'computer','browser','capture_screenshot','launch_game'}


def safe_action_label(name,args):
    """Describe only an allowlisted action, never URLs, queries or typed text."""
    if name=='capture_screenshot':return 'Capturing a screenshot'
    if name=='launch_game':return 'Launching the selected game'
    action=args.get('action','') if isinstance(args,dict) else ''
    if name=='browser':
        return {'search':'Searching the web','open':'Opening a web page','inspect':'Reading the current page',
                'click':'Clicking a page control','fill':'Entering text in a page','press':'Using a browser key',
                'screenshot':'Capturing a page screenshot'}.get(action,'Using the browser')
    return {'list':'Finding application windows','windows':'Finding application windows',
            'inspect':'Reading application controls','screenshot':'Capturing the desktop',
            'click':'Clicking an observed control','type':'Entering text','type_text':'Entering text',
            'fill':'Entering text','press':'Using a keyboard action','key':'Using a keyboard action',
            'scroll':'Scrolling an application','focus':'Selecting an application'}.get(action,'Using desktop controls')


class ControlOverlay(QWidget):
    stop_requested=Signal()

    def __init__(self):
        super().__init__(None,Qt.Tool|Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint|Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating,True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setObjectName('controlOverlay')
        self.session_active=False
        self.setStyleSheet('''QWidget#controlOverlay {background:#14232e;border:1px solid #68d5b8;border-radius:12px;}
QLabel {color:#f0faf8;background:transparent;border:0;font-family:Segoe UI;}
QLabel#controlDetail {color:#b6cbd3;font-size:12px;}
QPushButton {background:#d6fff0;color:#0d3025;border:0;border-radius:7px;padding:10px 18px;font-weight:700;}
QPushButton:disabled {background:#39514e;color:#cfdfdb;}''')
        layout=QHBoxLayout(self);layout.setContentsMargins(18,12,14,12);layout.setSpacing(18)
        text=QVBoxLayout();text.setSpacing(4);layout.addLayout(text,1)
        self.headline=QLabel('TalkToAi is using your computer');self.headline.setTextFormat(Qt.PlainText)
        font=self.headline.font();font.setPointSize(11);font.setBold(True);self.headline.setFont(font);text.addWidget(self.headline)
        self.detail=QLabel();self.detail.setObjectName('controlDetail');self.detail.setTextFormat(Qt.PlainText);self.detail.setWordWrap(True);text.addWidget(self.detail)
        self.stop_button=QPushButton('Stop');self.stop_button.setFocusPolicy(Qt.NoFocus);self.stop_button.clicked.connect(self.stop_requested);layout.addWidget(self.stop_button)

    def show_control(self,action,global_escape,browser=False):
        self.session_active=True
        self.headline.setText('TalkToAi is working in its browser' if browser else 'TalkToAi is using your computer')
        self.detail.setText(action+' · '+('Esc to cancel' if global_escape else 'Esc in TalkToAi to cancel, or click Stop'))
        self.stop_button.setEnabled(True)
        if not self.isVisible():
            screen=QApplication.primaryScreen()
            if screen:
                bounds=screen.availableGeometry();self.setFixedWidth(min(610,max(280,bounds.width()-32)))
                self.adjustSize();self.move(bounds.x()+(bounds.width()-self.width())//2,bounds.y()+24)
            self.show()

    def show_stopping(self):
        self.headline.setText('TalkToAi is stopping…')
        self.detail.setText('Waiting for the current action to finish safely.')
        self.stop_button.setEnabled(False)

    def finish_control(self):
        self.session_active=False;self.hide()

    def closeEvent(self,event):
        if self.session_active:
            self.stop_requested.emit();event.ignore()
        else:event.accept()
