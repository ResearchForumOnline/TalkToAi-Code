"""Qt model downloads panel with cancellable network and import work."""

import os
import threading

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QProgressBar, QPushButton, QVBoxLayout,
)

from model_library import (
    DEFAULT_LOCAL_MODEL, OPENZERO_FILE, OPENZERO_REPO, OPENZERO_REVISION,
    QWEN_LOCAL_MODEL, QWEN_FILE, QWEN_REPO, QWEN_REVISION, DownloadCancelled,
    download_files, format_size, gguf_selection, import_gguf, pull_model, repository_files, selected_files,
)
from routing import ensure_local_runtime, model_present


class _DownloadSignals(QObject):
    progress = Signal(str, object, object)
    finished = Signal(object)


class ModelLibraryDialog(QDialog):
    def __init__(self, parent, root, installed, select_model, diagnose, compare):
        super().__init__(parent)
        self.root = root
        self.select_model = select_model
        self.cancel = None
        self.destination = None
        self.loaded = None
        self.signals = _DownloadSignals(self)
        self.signals.progress.connect(self._progress)
        self.signals.finished.connect(self._finished)
        self.setWindowTitle('Model downloads')
        self.resize(780, 655)

        layout = QVBoxLayout(self)
        layout.addWidget(self._text('Recommended local models'))
        self._preset(layout, 'Install OpenZero 1.7B', '1.1 GB Q4 GGUF for Local',
                     DEFAULT_LOCAL_MODEL, 'local')
        self._preset(layout, 'Install Qwen3.6 35B A3B', 'About 23 GB Q4 GGUF for Local large. 3B active per token.',
                     QWEN_LOCAL_MODEL, 'local_large')
        layout.addWidget(self._text('Local inference uses Ollama. The 35B model needs room for all its weights and working memory.'))
        exports = QHBoxLayout()
        self.openzero_file = QPushButton('Save OpenZero GGUF')
        self.openzero_file.clicked.connect(lambda: self._save_preset(OPENZERO_REPO, OPENZERO_REVISION, OPENZERO_FILE))
        exports.addWidget(self.openzero_file)
        self.qwen_file = QPushButton('Save Qwen UD-Q4_K_M GGUF')
        self.qwen_file.clicked.connect(lambda: self._save_preset(QWEN_REPO, QWEN_REVISION, QWEN_FILE))
        exports.addWidget(self.qwen_file)
        exports.addStretch(1)
        layout.addLayout(exports)

        layout.addWidget(self._text('Hugging Face'))
        form = QFormLayout()
        self.repo = QLineEdit()
        self.repo.setPlaceholderText('owner/repository or https://huggingface.co/owner/repository')
        self.repo.returnPressed.connect(self._load_repository)
        self.repo.textChanged.connect(self._clear_repository)
        form.addRow('Model repository', self.repo)
        self.revision = QLineEdit('main')
        self.revision.textChanged.connect(self._clear_repository)
        form.addRow('Branch, tag or commit', self.revision)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText('Optional read token for gated or private models')
        self.token.textChanged.connect(self._clear_repository)
        form.addRow('Hugging Face token', self.token)
        layout.addLayout(form)
        listing = QHBoxLayout()
        self.find = QPushButton('List files')
        self.find.clicked.connect(self._load_repository)
        listing.addWidget(self.find)
        self.commit = self._text('Select a model repository to view its files.')
        listing.addWidget(self.commit, 1)
        layout.addLayout(listing)
        self.files = QListWidget()
        self.files.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.files.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.files, 1)
        self.install = QCheckBox('Import selected GGUF into Ollama and use it for Local')
        self.install.setToolTip('Ollama needs extra disk space for formats it can import. Other file formats can be saved for their own runtimes.')
        layout.addWidget(self.install)

        actions = QHBoxLayout()
        self.download = QPushButton('Download selected files')
        self.download.clicked.connect(self._download_selection)
        actions.addWidget(self.download)
        self.folder = QPushButton('Open download folder')
        self.folder.clicked.connect(self._open_folder)
        actions.addWidget(self.folder)
        self.stop = QPushButton('Stop')
        self.stop.clicked.connect(self._stop)
        actions.addWidget(self.stop)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.status = self._text('Downloads save by repository and commit. Select one GGUF shard to include the others.')
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        row = QHBoxLayout()
        self.local_actions = []
        for title, callback in (
            ('Use installed model…', installed), ('Diagnose connection', diagnose), ('Compare installed models', compare),
        ):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, action=callback: self._other(action))
            self.local_actions.append(button)
            row.addWidget(button)
        row.addStretch(1)
        close = QPushButton('Close')
        close.clicked.connect(self.reject)
        row.addWidget(close)
        layout.addLayout(row)
        self._set_running(False)

    @staticmethod
    def _text(message):
        label = QLabel(message)
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        return label

    def _preset(self, layout, title, description, model, route):
        row = QHBoxLayout()
        button = QPushButton(title)
        button.setObjectName('accent')
        button.clicked.connect(lambda: self._install_preset(model, route))
        row.addWidget(button)
        row.addWidget(self._text(description), 1)
        layout.addLayout(row)
        if not hasattr(self, 'presets'):
            self.presets = []
        self.presets.append(button)

    def _other(self, callback):
        self.accept()
        callback()

    def _set_running(self, running):
        for widget in [*self.presets, *self.local_actions, self.openzero_file, self.qwen_file,
                       self.repo, self.revision, self.token, self.find, self.files]:
            widget.setEnabled(not running)
        self.folder.setEnabled(not running and self.destination is not None)
        self.stop.setEnabled(running)
        self._selection_changed()

    def _selection(self):
        if self.loaded is None:
            return []
        files = self.loaded[2]
        return selected_files(files, [item.data(Qt.UserRole) for item in self.files.selectedItems()])

    def _selection_changed(self):
        try:
            files = self._selection()
        except ValueError:
            files = []
        available = self.cancel is None and bool(files)
        can_import = available and gguf_selection(files)
        self.install.setEnabled(can_import)
        if not can_import and self.cancel is None:
            self.install.setChecked(False)
        self.download.setEnabled(available)
        self.download.setText('Download selected files' if not files else f'Download {len(files)} file(s) · {format_size(sum(file.size for file in files))}')

    def _clear_repository(self):
        self.loaded = None
        self.commit.setText('List files to refresh this repository.')
        self.files.clear()
        self._selection_changed()

    def _token(self):
        return self.token.text().strip() or os.environ.get('HF_TOKEN', '').strip()

    def _start(self, work):
        if self.cancel is not None:
            return
        cancel = self.cancel = threading.Event()
        self._set_running(True)
        self._progress('Starting…', 0, 0)

        def run():
            try:
                result = work(cancel, self.signals.progress.emit)
            except DownloadCancelled:
                result = ('status', 'Stopped. Select the same download again to resume.')
            except Exception as exc:
                result = ('status', str(exc)[:1200])
            self.signals.finished.emit(result)

        threading.Thread(target=run, daemon=True).start()

    def _progress(self, message, completed, total):
        if self.cancel and self.cancel.is_set():
            return
        self.progress.setRange(0, 100 if total else 0)
        if total:
            self.progress.setValue(min(100, 100 * completed // total))
            message += f' · {format_size(completed)} / {format_size(total)}'
        self.status.setText(message)

    def _finished(self, result):
        self.cancel = None
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        action, *data = result
        if action == 'files':
            repo, commit, files = self.loaded = tuple(data)
            self.commit.setText(f'{len(files)} files · {commit[:12]}')
            self.files.clear()
            for file in files:
                item = QListWidgetItem(f'{file.name}  ·  {format_size(file.size)}')
                item.setData(Qt.UserRole, file)
                self.files.addItem(item)
            self.status.setText('Select files. Split GGUF selections include every required shard.')
        elif action == 'installed':
            model, route = data
            try:
                self.select_model(model, route)
                self.status.setText(f'Selected {model} for {"Local large" if route == "local_large" else "Local"}.')
                self.progress.setValue(100)
            except OSError as exc:
                self.status.setText(f'Ollama registered the model, but its route could not be saved. {exc}')
        elif action == 'saved':
            self.destination, message = data
            self.status.setText(message)
            self.progress.setValue(100)
        elif action == 'saved_install':
            self.destination, model = data
            self._finished(('installed', model, 'local'))
            return
        else:
            self.status.setText(data[0])
        self._set_running(False)

    def _install_preset(self, model, route):
        def work(cancel, notify):
            if not ensure_local_runtime():
                raise ConnectionError('Install or start Ollama, then retry. TalkToAi Code connects on 127.0.0.1:11434.')
            if not model_present(11434, model):
                pull_model(model, cancel, notify)
            if not model_present(11434, model):
                raise RuntimeError('Ollama did not report the newly installed model. Refresh its inventory and retry.')
            if cancel.is_set():
                raise DownloadCancelled()
            return 'installed', model, route

        self._start(work)

    def _save_preset(self, repo, commit, file):
        self._save_files(repo, commit, [file], '', False)

    def _load_repository(self):
        repo, revision, token = self.repo.text(), self.revision.text(), self._token()
        self._clear_repository()

        def work(cancel, notify):
            notify('Reading Hugging Face model repository', 0, 0)
            commit, files = repository_files(repo, revision, token)
            if cancel.is_set():
                raise DownloadCancelled()
            return 'files', repo, commit, files

        self._start(work)

    def _download_selection(self):
        if self.loaded is None:
            return
        repo, commit, _ = self.loaded
        try:
            files = self._selection()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        if files:
            self._save_files(repo, commit, files, self._token(), self.install.isChecked())

    def _save_files(self, repo, commit, files, token, install):
        def work(cancel, notify):
            folder, paths = download_files(repo, commit, files, self.root, token, cancel, notify)
            if install:
                try:
                    if not ensure_local_runtime():
                        raise ConnectionError('Install or start local Ollama and retry the import.')
                    model = import_gguf(paths, cancel, notify)
                    if not model_present(11434, model):
                        raise RuntimeError('The imported model did not appear in Ollama inventory.')
                except Exception as exc:
                    return 'saved', folder, f'Download saved. Ollama import stopped or failed. {str(exc)[:900]}'
                return 'saved_install', folder, model
            return 'saved', folder, f'Saved {len(paths)} file(s) to {folder}'

        self._start(work)

    def _open_folder(self):
        if self.destination is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.destination)))

    def _stop(self):
        if self.cancel:
            self.cancel.set()
            self.stop.setEnabled(False)
            self.status.setText('Stopping after the current network operation…')

    def reject(self):
        if self.cancel:
            self._stop()
        else:
            super().reject()
