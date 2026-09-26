import threading
from pathlib import Path
from PySide6.QtCore import QObject, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QTextBrowser
from updates import VERSION, REPO, check_release, download_release, is_store_package

class UpdateSignals(QObject):
    result = Signal(object)

class UpdateDialog(QDialog):
    def __init__(self, owner, state):
        super().__init__(owner)
        self.owner=owner; self.state=state; self.release=None
        self.store_package=is_store_package()
        self.setWindowTitle('About & updates — TalkToAi Code'); self.resize(640,480)
        self.signals=UpdateSignals(self); self.signals.result.connect(self.completed)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel(f'TalkToAi Code {VERSION} · Windows desktop'))
        self.status=QLabel('Microsoft Store manages updates for this installation.' if self.store_package else 'Updates are checked only when you ask. No AI tokens are used.'); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.notes=QTextBrowser(); layout.addWidget(self.notes)
        self.check=QPushButton('Check GitHub for updates'); self.check.setEnabled(not self.store_package); self.check.clicked.connect(lambda:self.work(check_release)); layout.addWidget(self.check)
        self.download=QPushButton('Download & verify installer'); self.download.setEnabled(False); self.download.clicked.connect(lambda:self.work(lambda:download_release(self.release, self.state/'updates'))); layout.addWidget(self.download)
        self.install=QPushButton('Install update and quit'); self.install.setEnabled(False); self.install.clicked.connect(self.install_update); layout.addWidget(self.install)
        website=QPushButton('Open GitHub release notes'); website.clicked.connect(lambda:QDesktopServices.openUrl(QUrl(self.release['page'] if self.release else REPO+'/releases'))); layout.addWidget(website)
        close=QPushButton('Close'); close.clicked.connect(self.reject); layout.addWidget(close)

    def work(self, operation):
        self.check.setEnabled(False); self.download.setEnabled(False); self.install.setEnabled(False)
        self.status.setText('Working… you can keep using the app.')
        def run():
            try: result=operation()
            except Exception as exc: result=exc
            self.signals.result.emit(result)
        threading.Thread(target=run, daemon=True).start()

    def completed(self, result):
        self.check.setEnabled(not self.store_package)
        if isinstance(result, Exception):
            self.status.setText('Update failed: '+str(result)); return
        if isinstance(result, dict):
            self.release=result; self.notes.setPlainText(result['notes'])
            self.status.setText(('Update available: ' if result['newer'] else 'You are up to date. Latest published version: ')+result['version'])
            self.download.setEnabled(result['newer'] and not self.store_package)
        else:
            self.installer=result; self.install.setEnabled(not self.store_package)
            self.status.setText('Download verified against GitHub SHA-256. Ready to install.')

    def install_update(self):
        if self.store_package:
            self.status.setText('Use Microsoft Store to update this installation.'); return
        import subprocess
        if self.owner.busy:
            self.status.setText('Stop or finish the current task before installing.'); return
        try:
            self.owner.persist(); self.owner.write_config()
            subprocess.Popen([self.installer], cwd=str(Path(self.installer).parent))
        except Exception as exc:
            self.status.setText('Could not start installer: '+str(exc)); return
        self.owner.quit_app()
