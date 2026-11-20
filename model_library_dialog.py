"""Qt model downloads panel with cancellable network and import work."""

import os
from pathlib import Path
import threading

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from model_library import (
    DEFAULT_LOCAL_MODEL, OPENZERO_FILE, OPENZERO_REPO, OPENZERO_REVISION,
    QWEN_LOCAL_MODEL, QWEN_FILE, QWEN_REPO, QWEN_REVISION, DownloadCancelled,
    download_files, format_size, gguf_selection, import_gguf, resolve_hf_selection, selected_files,
)
from routing import delete_local_model, install_model, list_models, model_present, require_local_runtime
from runtime_setup import require_model_runtime


class _DownloadSignals(QObject):
    progress = Signal(str, object, object)
    finished = Signal(object)


class ModelLibraryDialog(QDialog):
    def __init__(self, parent, root, select_model, model_removed, diagnose, compare, use_api):
        super().__init__(parent)
        self.root = Path(root)
        self.select_model = select_model
        self.model_removed = model_removed
        self.cancel = None
        self.stoppable = True
        self.destination = None
        self.loaded = None
        self.inventory_loaded = False
        self.signals = _DownloadSignals(self)
        self.signals.progress.connect(self._progress)
        self.signals.finished.connect(self._finished)
        self.setWindowTitle('Models and setup')
        self.resize(760, 540)

        layout = QVBoxLayout(self)
        self.status = self._text('Choose a model to install or check your local Ollama connection.')
        self.status.setAccessibleName('Model operation status')
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setAccessibleName('Model operation progress')
        self.progress.setRange(0, 100)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        recommended = self._tab('Recommended')
        self._preset(recommended, 'Install OpenZero 1.7B', '1.1 GB Q4 for Local',
                     DEFAULT_LOCAL_MODEL, 'local')
        self._preset(recommended, 'Install Qwen3.6 35B A3B', 'About 23 GB Q4 for Local large. 3B active per token.',
                     QWEN_LOCAL_MODEL, 'local_large')
        recommended.addWidget(self._text('Local models use Ollama. Qwen needs disk space and memory for the full 35B model.'))
        if os.name == 'nt':
            recommended.addWidget(self._text('Installing a model can set up missing Ollama through WinGet. Use Get Ollama if WinGet is unavailable.'))
        connection = QHBoxLayout()
        self.check_connection = QPushButton('Check Ollama connection')
        self.check_connection.clicked.connect(self._load_installed)
        connection.addWidget(self.check_connection)
        self.get_ollama = QPushButton('Get Ollama')
        self.get_ollama.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('https://ollama.com/download')))
        connection.addWidget(self.get_ollama)
        self.api = QPushButton('Use an API instead')
        self.api.clicked.connect(lambda: self._other(use_api))
        connection.addWidget(self.api)
        connection.addStretch(1)
        recommended.addLayout(connection)
        exports = QHBoxLayout()
        self.openzero_file = QPushButton('Save OpenZero GGUF')
        self.openzero_file.clicked.connect(lambda: self._save_preset(OPENZERO_REPO, OPENZERO_REVISION, OPENZERO_FILE))
        exports.addWidget(self.openzero_file)
        self.qwen_file = QPushButton('Save Qwen UD-Q4_K_M GGUF')
        self.qwen_file.clicked.connect(lambda: self._save_preset(QWEN_REPO, QWEN_REVISION, QWEN_FILE))
        exports.addWidget(self.qwen_file)
        exports.addStretch(1)
        recommended.addLayout(exports)
        recommended.addStretch(1)

        hugging_face = self._tab('Hugging Face')
        form = QFormLayout()
        self.repo = QLineEdit()
        self.repo.setPlaceholderText('Paste a Hugging Face repository, folder or file link')
        self.repo.returnPressed.connect(self._load_repository)
        self.repo.textChanged.connect(self._clear_repository)
        form.addRow('Repository or file', self.repo)
        self.revision = QLineEdit('main')
        self.revision.textChanged.connect(self._clear_repository)
        form.addRow('Branch, tag or commit', self.revision)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText('Optional read token for gated or private models')
        self.token.textChanged.connect(self._clear_repository)
        form.addRow('Hugging Face token', self.token)
        hugging_face.addLayout(form)
        listing = QHBoxLayout()
        self.find = QPushButton('List files')
        self.find.clicked.connect(self._load_repository)
        listing.addWidget(self.find)
        self.commit = self._text('Paste a link and press Enter or List files.')
        listing.addWidget(self.commit, 1)
        hugging_face.addLayout(listing)
        self.files = QListWidget()
        self.files.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.files.itemSelectionChanged.connect(self._selection_changed)
        hugging_face.addWidget(self.files, 1)
        self.install = QCheckBox('Import selected GGUF into Ollama and use it for Local')
        self.install.setToolTip('Ollama needs extra disk space for formats it can import. Other file formats can be saved for their own runtimes.')
        self.install.toggled.connect(self._selection_changed)
        hugging_face.addWidget(self.install)

        actions = QHBoxLayout()
        self.download = QPushButton('Download selected files')
        self.download.clicked.connect(self._download_selection)
        actions.addWidget(self.download)
        actions.addStretch(1)
        hugging_face.addLayout(actions)

        installed = self._tab('Installed Ollama')
        installed.addWidget(self._text('Models available on this computer. Choose one to use or remove.'))
        self.installed = QListWidget()
        self.installed.currentItemChanged.connect(self._installed_selection_changed)
        installed.addWidget(self.installed, 1)
        inventory_actions = QHBoxLayout()
        self.refresh_installed = QPushButton('Refresh models')
        self.refresh_installed.clicked.connect(self._load_installed)
        inventory_actions.addWidget(self.refresh_installed)
        self.use_installed = QPushButton('Use selected for Local')
        self.use_installed.clicked.connect(self._use_installed)
        inventory_actions.addWidget(self.use_installed)
        self.delete_installed = QPushButton('Remove from Ollama…')
        self.delete_installed.clicked.connect(self._remove_installed)
        inventory_actions.addWidget(self.delete_installed)
        inventory_actions.addStretch(1)
        installed.addLayout(inventory_actions)
        self.tabs.currentChanged.connect(self._tab_changed)

        row = QHBoxLayout()
        self.local_actions = []
        for title, callback in (('Diagnose connection', diagnose), ('Compare models', compare)):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, action=callback: self._other(action))
            self.local_actions.append(button)
            row.addWidget(button)
        row.addStretch(1)
        self.folder = QPushButton('Open download folder')
        self.folder.clicked.connect(self._open_folder)
        row.addWidget(self.folder)
        self.stop = QPushButton('Stop')
        self.stop.clicked.connect(self._stop)
        row.addWidget(self.stop)
        close = QPushButton('Close')
        close.clicked.connect(self.reject)
        row.addWidget(close)
        layout.addLayout(row)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self._set_running(False)

    def _tab(self, title):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.tabs.addTab(page, title)
        return layout

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
                       self.repo, self.revision, self.token, self.files, self.check_connection, self.api,
                       self.refresh_installed, self.installed]:
            widget.setEnabled(not running)
        self.find.setEnabled(not running and bool(self.repo.text().strip()))
        self.folder.setEnabled(not running and (self.destination is not None or self.root.is_dir()))
        self.stop.setEnabled(running and self.stoppable)
        self._selection_changed()
        self._installed_selection_changed()

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
        if not files:
            title = 'Download selected files'
        else:
            verb = 'Download and import' if self.install.isChecked() else 'Download'
            title = f'{verb} {len(files)} file(s) · {format_size(sum(file.size for file in files))}'
        self.download.setText(title)

    def _clear_repository(self):
        self.loaded = None
        self.commit.setText('Paste a link and press Enter or List files.')
        self.files.clear()
        self._selection_changed()
        self.find.setEnabled(self.cancel is None and bool(self.repo.text().strip()))

    def _token(self):
        return self.token.text().strip() or os.environ.get('HF_TOKEN', '').strip()

    def _set_status(self, message, *, error=False):
        self.status.setText(('Error · ' if error else '') + message)
        self.status.setStyleSheet('font-weight: 600')

    def _start(self, starting, work, *, stoppable=True):
        if self.cancel is not None:
            return
        cancel = self.cancel = threading.Event()
        self.stoppable = stoppable
        self._set_running(True)
        self._progress(starting, 0, 0)

        def run():
            try:
                result = work(cancel, self.signals.progress.emit)
            except DownloadCancelled:
                result = ('status', 'Stopped. Select the same task again to retry.')
            except Exception as exc:
                result = ('error', (str(exc) or type(exc).__name__)[:1200])
            self.signals.finished.emit(result)

        threading.Thread(target=run, daemon=True).start()

    def _progress(self, message, completed, total):
        if self.cancel is None or self.cancel.is_set():
            return
        self.progress.show()
        self.progress.setRange(0, 100 if total else 0)
        if total:
            self.progress.setValue(min(100, 100 * completed // total))
            message += f' · {format_size(completed)} / {format_size(total)}'
        self._set_status(message)

    def _finished(self, result):
        self.cancel = None
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        action, *data = result
        if action == 'files':
            repo, commit, files, selected = data
            self.loaded = (repo, commit, files)
            self.commit.setText(f'{repo} · {len(files)} files · {commit[:12]}')
            self.files.clear()
            for file in files:
                item = QListWidgetItem(f'{file.name}  ·  {format_size(file.size)}')
                item.setData(Qt.UserRole, file)
                self.files.addItem(item)
                if file.name == selected:
                    self.files.setCurrentItem(item)
                    self.files.scrollToItem(item)
            self._set_status('File selected from your link. Choose Download to save it.' if selected
                             else 'Select files to download. Split GGUF models include all required shards.')
        elif action == 'installed':
            model, route = data
            try:
                self.select_model(model, route)
                self._set_status(f'Selected {model} for {"Local large" if route == "local_large" else "Local"}.')
                self.progress.setValue(100)
            except OSError as exc:
                self._set_status(f'Ollama registered the model. Saving the route failed. {exc}', error=True)
            self.inventory_loaded = False
        elif action == 'saved':
            self.destination, message = data
            self._set_status(message)
            self.progress.setValue(100)
        elif action == 'saved_error':
            self.destination, message = data
            self._set_status(message, error=True)
        elif action == 'saved_install':
            self.destination, model = data
            self._finished(('installed', model, 'local'))
            return
        elif action == 'inventory':
            names, = data
            self._show_installed(names)
            self._set_status(f'Ollama connected · {len(names)} installed model(s).')
            self.progress.setValue(100)
        elif action == 'removed':
            model, = data
            names = [self.installed.item(row).data(Qt.UserRole) for row in range(self.installed.count())]
            names = [name for name in names if name != model]
            self._show_installed(names)
            try:
                self.model_removed(model)
                self._set_status(f'Removed {model} from local Ollama.')
                self.progress.setValue(100)
            except OSError as exc:
                self._set_status(f'Removed {model}. Updating app settings failed. {exc}', error=True)
        else:
            self._set_status(data[0], error=action == 'error')
        if not self.progress.value():
            self.progress.hide()
        self._set_running(False)

    def _install_preset(self, model, route):
        def work(cancel, notify):
            require_model_runtime(cancel, notify)
            return 'installed', install_model(model, cancel, notify), route

        name = 'Qwen3.6 35B A3B' if route == 'local_large' else 'OpenZero 1.7B'
        self._start(f'Preparing {name} · checking Ollama…', work)

    def _save_preset(self, repo, commit, file):
        self._save_files(repo, commit, [file], '', False)

    def _load_repository(self):
        reference, revision, token = self.repo.text().strip(), self.revision.text(), self._token()
        if not reference or self.cancel is not None:
            return
        self._clear_repository()

        def work(cancel, notify):
            selection = resolve_hf_selection(reference, revision, token)
            if cancel.is_set():
                raise DownloadCancelled()
            return 'files', *selection

        self._start('Reading Hugging Face files…', work)

    def _download_selection(self):
        if self.loaded is None:
            return
        repo, commit, _ = self.loaded
        try:
            files = self._selection()
        except ValueError as exc:
            self._set_status(str(exc), error=True)
            return
        if files:
            self._save_files(repo, commit, files, self._token(), self.install.isChecked())

    def _save_files(self, repo, commit, files, token, install):
        def work(cancel, notify):
            folder, paths = download_files(repo, commit, files, self.root, token, cancel, notify)
            if install:
                try:
                    require_model_runtime(cancel, notify)
                    model = import_gguf(paths, cancel, notify)
                    if not model_present(11434, model):
                        raise RuntimeError('The imported model did not appear in Ollama inventory.')
                except DownloadCancelled:
                    return 'saved', folder, f'Download saved to {folder}. Ollama import stopped.'
                except Exception as exc:
                    return 'saved_error', folder, f'Ollama import failed. Files were saved to {folder}. {str(exc)[:900]}'
                return 'saved_install', folder, model
            return 'saved', folder, f'Saved {len(paths)} file(s) to {folder}'

        self._start('Preparing download…', work)

    def _tab_changed(self, index):
        if self.tabs.tabText(index) == 'Installed Ollama' and not self.inventory_loaded and self.cancel is None:
            self._load_installed()

    def _load_installed(self):
        def work(cancel, notify):
            require_local_runtime(cancel, notify)
            return 'inventory', list_models(11434)

        self._start('Connecting to local Ollama…', work)

    def _show_installed(self, names):
        selected = self._installed_name()
        self.installed.clear()
        for name in names:
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, name)
            self.installed.addItem(item)
            if name == selected:
                self.installed.setCurrentItem(item)
        self.inventory_loaded = True

    def _installed_name(self):
        item = self.installed.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _installed_selection_changed(self):
        available = self.cancel is None and bool(self._installed_name())
        self.use_installed.setEnabled(available)
        self.delete_installed.setEnabled(available)

    def _use_installed(self):
        model = self._installed_name()
        if model and self.cancel is None:
            self._finished(('installed', model, 'local'))

    def _remove_installed(self):
        model = self._installed_name()
        if model is None or self.cancel is not None:
            return
        confirm = QMessageBox(self)
        confirm.setWindowTitle('Remove installed model')
        confirm.setTextFormat(Qt.PlainText)
        confirm.setText(f'Remove {model} from local Ollama?')
        confirm.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        confirm.setDefaultButton(QMessageBox.Cancel)
        if confirm.exec() != QMessageBox.Yes:
            return

        def work(cancel, notify):
            return 'removed', delete_local_model(model, cancel)

        self._start(f'Removing {model} from local Ollama…', work, stoppable=False)

    def _open_folder(self):
        destination = self.destination if self.destination is not None else self.root
        if destination.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(destination)))

    def _stop(self):
        if self.cancel and self.stoppable:
            self.cancel.set()
            self.stop.setEnabled(False)
            self._set_status('Stopping after the current network operation…')

    def reject(self):
        if self.cancel:
            self._stop()
        else:
            super().reject()
