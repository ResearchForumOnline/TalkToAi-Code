"""Qt model downloads panel with cancellable network and import work."""

from contextlib import nullcontext
from datetime import datetime
import os
from pathlib import Path
import threading

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from model_library import (
    DEFAULT_LOCAL_MODEL, OPENZERO_FILE, OPENZERO_REPO, OPENZERO_REVISION,
    QWEN_LOCAL_MODEL, QWEN_FILE, QWEN_REPO, QWEN_REVISION, DownloadCancelled, ModelFile,
    download_files, format_size, gguf_selection, import_gguf, resolve_hf_selection, selected_files,
)
from model_storage import (
    PARTIAL_RETENTION_DAYS, clear_partials, display_path, library_root, native_path,
    partial_inventory, remove_export, reviewed_export, saved_exports, storage_lock,
)
from routing import delete_local_model, install_model, installed_model_records, matching_model, model_present, release_local_model, require_local_runtime
from runtime_setup import require_model_runtime
from runtime_diagnostics import last_reply_check, reply_check_text, resident_model, running_probe


class _DownloadSignals(QObject):
    progress = Signal(str, object, object)
    finished = Signal(object)
    inventory = Signal(int, object, object)


class ModelLibraryDialog(QDialog):
    def __init__(self, parent, root, select_model, model_removed, diagnose, compare, use_api, *,
                 configured_models=None, keep_alive=None, inventory_changed=None, tune=None):
        super().__init__(parent)
        self.root = Path(root)
        self.reply_history_dir = self.root / '.reply-checks-v1'
        self.select_model = select_model
        self.model_removed = model_removed
        self.keep_alive = keep_alive
        self.inventory_changed = inventory_changed
        self.configured_models = dict(configured_models or {})
        self.model_records = {}
        self.partials = []
        self.saved_files = []
        self.cancel = None
        self.stoppable = True
        self.destination = None
        self.loaded = None
        self.inventory_loaded = False
        self.inventory_stale = False
        self.inventory_epoch = 0
        self.inventory_pending = False
        self.residency = None
        self.signals = _DownloadSignals(self)
        self.signals.progress.connect(self._progress)
        self.signals.finished.connect(self._finished)
        self.signals.inventory.connect(self._received_inventory)
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
        recommended.addWidget(self._text('For chat, use Install above. Save GGUF creates a separate file for other runtimes.'))
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
        installed.addWidget(self._text('These models belong to your local Ollama library. Other apps using the same Ollama service share this list.'))
        self.inventory_note = self._text('Checking local models when this tab opens.')
        installed.addWidget(self.inventory_note)
        self.installed = QListWidget()
        self.installed.currentItemChanged.connect(self._installed_selection_changed)
        installed.addWidget(self.installed, 1)
        self.reply_history = self._text('Select a model to see its last reply check.')
        self.reply_history.setAccessibleName('Last measured reply check')
        installed.addWidget(self.reply_history)
        inventory_actions = QHBoxLayout()
        self.refresh_installed = QPushButton('Refresh models')
        self.refresh_installed.clicked.connect(self._load_installed)
        inventory_actions.addWidget(self.refresh_installed)
        self.use_installed = QPushButton('Use selected for Local')
        self.use_installed.clicked.connect(self._use_installed)
        inventory_actions.addWidget(self.use_installed)
        self.test_reply = QPushButton('Test reply')
        self.test_reply.setToolTip('Ask the selected model for 2 + 3. Check the answer and time this local chat request.')
        self.test_reply.clicked.connect(self._test_reply)
        inventory_actions.addWidget(self.test_reply)
        self.release_loaded = QPushButton('Release from memory')
        self.release_loaded.setToolTip('Free the selected model’s RAM or VRAM. Its downloaded weights stay installed.')
        self.release_loaded.clicked.connect(self._release_selected)
        inventory_actions.addWidget(self.release_loaded)
        self.delete_installed = QPushButton('Remove from Ollama…')
        self.delete_installed.clicked.connect(self._remove_installed)
        inventory_actions.addWidget(self.delete_installed)
        inventory_actions.addStretch(1)
        installed.addLayout(inventory_actions)

        saved = self._tab('Saved downloads')
        saved.addWidget(self._text('Standalone Hugging Face files saved by Studio, largest first. '
                                   f'Interrupted downloads resume for {PARTIAL_RETENTION_DAYS} days before automatic cleanup.'))
        self.exports = QListWidget()
        self.exports.currentItemChanged.connect(self._export_selection_changed)
        self.exports.setAccessibleName('Saved Hugging Face downloads')
        saved.addWidget(self.exports, 1)
        storage_buttons = QHBoxLayout()
        self.refresh_exports = QPushButton('Refresh files')
        self.refresh_exports.clicked.connect(self._load_exports)
        storage_buttons.addWidget(self.refresh_exports)
        self.use_saved = QPushButton('Use selected for chat')
        self.use_saved.clicked.connect(self._use_saved)
        storage_buttons.addWidget(self.use_saved)
        self.local_gguf = QPushButton('Browse for GGUF…')
        self.local_gguf.setToolTip('Import an existing GGUF, including a file downloaded by another app. Select all shards for a split model.')
        self.local_gguf.clicked.connect(self._choose_local_gguf)
        storage_buttons.addWidget(self.local_gguf)
        self.remove_saved = QPushButton('Remove saved file…')
        self.remove_saved.clicked.connect(self._remove_export)
        storage_buttons.addWidget(self.remove_saved)
        self.clear_cache = QPushButton('Clear unfinished…')
        self.clear_cache.clicked.connect(self._clear_partials)
        storage_buttons.addWidget(self.clear_cache)
        storage_buttons.addStretch(1)
        saved.addLayout(storage_buttons)
        self.tabs.currentChanged.connect(self._tab_changed)

        row = QHBoxLayout()
        self.local_actions = []
        actions = [('Diagnose connection', diagnose), ('Compare models', compare)]
        if tune:
            actions.append(('Tune local model', tune))
        for title, callback in actions:
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
        self.inventory_timer = QTimer(self)
        self.inventory_timer.timeout.connect(self._poll_installed)
        self.inventory_timer.start(5000)
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
                       self.refresh_installed, self.installed, self.refresh_exports, self.exports, self.local_gguf]:
            widget.setEnabled(not running)
        self.find.setEnabled(not running and bool(self.repo.text().strip()))
        self.folder.setEnabled(not running and (self.destination is not None or native_path(self.root).is_dir()))
        self.stop.setEnabled(running and self.stoppable)
        self.clear_cache.setEnabled(not running and bool(self.partials))
        self._selection_changed()
        self._installed_selection_changed()
        self._export_selection_changed()

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
        self.inventory_epoch += 1
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
                self.configured_models['Local large' if route == 'local_large' else 'Local'] = model
                if model in self.model_records:
                    self._show_installed(list(self.model_records))
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
            records, running = data
            self._accept_inventory(records, running)
            self._set_status(f'Ollama connected · {len(records)} installed model(s).')
            self.progress.setValue(100)
        elif action == 'inventory_error':
            self._accept_inventory(None, None)
            self._set_status(data[0], error=True)
        elif action == 'verified_selection':
            model, records, running = data
            self._accept_inventory(records, running)
            if model:
                self._finished(('installed', model, 'local'))
                return
            self._set_status('The selected model was removed from Ollama. Choose an installed model.')
        elif action == 'released':
            model, released = data
            if self.residency and self.residency.get('state') == 'reachable':
                loaded = resident_model(self.residency, model, self.model_records.get(model, {}).get('digest'))
                self.residency = {'state':'reachable', 'models':[
                    item for item in self.residency['models'] if item!=loaded
                ]}
                self._show_installed(list(self.model_records))
            self._set_status(f'{model} was released from memory.' if released else f'{model} is no longer in memory.')
            self.progress.setValue(100)
        elif action == 'reply_check':
            result, = data
            if result['outcome'] != 'incomplete':
                record = self.model_records.get(result['model'])
                if record is not None:
                    record['digest'] = result['digest']
                self.inventory_loaded = bool(result['digest'])
            self._set_status(reply_check_text(result))
            if result['outcome'] != 'incomplete':
                self.progress.setValue(100)
        elif action == 'exports':
            files, capped, self.partials, label = data
            self.saved_files = files
            self.exports.clear()
            for file in files:
                item = QListWidgetItem(f'{format_size(file.size)}   {file.relative}')
                item.setData(Qt.UserRole, file)
                self.exports.addItem(item)
            detail = (f'{len(files)} saved file(s), {format_size(sum(file.size for file in files))}. '
                      f'Unfinished downloads: {format_size(sum(file.size for file in self.partials))}.')
            self._set_status(label + detail + (' Showing a partial inventory.' if capped else ''))
        elif action == 'removed':
            model, = data
            self.model_records.pop(model, None)
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
        if action in ('installed', 'removed', 'released', 'reply_check'):
            QTimer.singleShot(0, self._poll_installed)

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
                    return 'saved', folder, f'Download saved to {display_path(folder)}. Ollama import stopped.'
                except Exception as exc:
                    return 'saved_error', folder, f'Ollama import failed. Files were saved to {display_path(folder)}. {str(exc)[:900]}'
                return 'saved_install', folder, model
            return 'saved', folder, f'Saved {len(paths)} file(s) to {display_path(folder)}'

        self._start('Preparing download…', work)

    def _tab_changed(self, index):
        if self.tabs.tabText(index) == 'Installed Ollama' and self.cancel is None:
            self._poll_installed()
        elif self.tabs.tabText(index) == 'Saved downloads' and self.cancel is None:
            self._load_exports()

    def _load_installed(self):
        def work(cancel, notify):
            try:
                require_local_runtime(cancel, notify)
                return 'inventory', *self._installed_snapshot()
            except (ConnectionError, OSError, ValueError) as exc:
                return 'inventory_error', str(exc)

        self._start('Connecting to local Ollama…', work)

    @staticmethod
    def _installed_snapshot():
        return installed_model_records(11434), running_probe(11434)

    def _poll_installed(self):
        if (not self.isVisible() or self.tabs.tabText(self.tabs.currentIndex()) != 'Installed Ollama'
                or self.cancel is not None or self.inventory_pending):
            return
        self.inventory_pending = True
        epoch = self.inventory_epoch

        def work():
            try:
                records, running = self._installed_snapshot()
            except (ConnectionError, OSError, ValueError):
                records, running = None, None
            try:
                self.signals.inventory.emit(epoch, records, running)
            except RuntimeError:
                pass  # The modal window may have been closed during the probe.

        threading.Thread(target=work, daemon=True).start()

    def _received_inventory(self, epoch, records, running):
        self.inventory_pending = False
        if epoch == self.inventory_epoch and self.cancel is None:
            self._accept_inventory(records, running)

    def _accept_inventory(self, records, running):
        self.inventory_stale = records is None
        if records is None:
            self.inventory_note.setText('Ollama inventory is unavailable. The last confirmed list is shown.')
            self._installed_selection_changed()
            return
        replacement = {item['name']: item for item in records}
        if replacement != self.model_records or running != self.residency or not self.inventory_loaded:
            self.model_records, self.residency = replacement, running
            self._show_installed(list(replacement))
        self.inventory_note.setText(
            f'{len(replacement)} installed model(s).' if replacement else 'No models are installed in local Ollama.')
        if self.inventory_changed:
            self.inventory_changed(list(replacement))
        self._installed_selection_changed()

    def _show_installed(self, names):
        selected = self._installed_name()
        self.installed.clear()
        aliases = {}
        for tag, record in self.model_records.items():
            digest = record.get('digest')
            if isinstance(digest, str) and digest:
                aliases.setdefault(digest, []).append(tag)
        for name in names:
            record = self.model_records.get(name, {})
            labels = []
            if type(record.get('size')) is int and record['size'] >= 0:
                labels.append(format_size(record['size']))
            routes = [route for route, model in self.configured_models.items() if matching_model([name], model)]
            if self.configured_models:
                labels.extend(routes or ['Not selected in Studio'])
            if resident_model(self.residency, name, record.get('digest')):
                labels.append('Loaded in memory')
            digest = record.get('digest')
            group = aliases.get(digest, []) if isinstance(digest, str) else []
            if len(group) > 1:
                shared = [other for other in group[:3] if other != name][:2]
                suffix = f' and {len(group) - 3} more' if len(group) > 3 else ''
                labels.append('same model as ' + ', '.join(shared) + suffix)
            item = QListWidgetItem('   ·   '.join([name, *labels]))
            item.setData(Qt.UserRole, name)
            self.installed.addItem(item)
            if name == selected:
                self.installed.setCurrentItem(item)
        if self.installed.count() and selected is None and self.installed.currentRow() < 0:
            self.installed.setCurrentRow(0)
        elif selected is not None and selected not in names:
            self.installed.setCurrentRow(-1)
        self.inventory_loaded = True

    def _installed_name(self):
        item = self.installed.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _installed_selection_changed(self):
        name = self._installed_name()
        available = self.cancel is None and bool(name) and not self.inventory_stale
        self.use_installed.setEnabled(available)
        self.test_reply.setEnabled(available)
        self.delete_installed.setEnabled(available)
        self.release_loaded.setEnabled(bool(available and resident_model(
            self.residency, name, self.model_records.get(name, {}).get('digest'))))
        measurement = last_reply_check(self.reply_history_dir, self.model_records.get(name, {}).get('digest'))
        if measurement:
            when = datetime.fromisoformat(measurement['checked_at']).astimezone().strftime('%Y-%m-%d %H:%M %Z')
            outcome = 'Verified the expected answer (5)' if measurement['outcome']=='verified' else 'Answered differently (expected 5)'
            self.reply_history.setText(f'Last reply check · {outcome} · {measurement["elapsed_seconds"]:.1f}s · {when}')
        else:
            self.reply_history.setText('No saved reply check for this version of the model.' if name else 'Select a model to see its last reply check.')

    def _test_reply(self):
        from runtime_diagnostics import measure_local_reply
        model = self._installed_name()
        if model and self.cancel is None:
            def work(cancel, notify):
                return 'reply_check', measure_local_reply(model, cancel, notify, history_dir=self.reply_history_dir,
                                                         keep_alive=self.keep_alive)
            self._start('Checking local model…', work)

    def _use_installed(self):
        model = self._installed_name()
        if model and self.cancel is None and not self.inventory_stale:
            def work(cancel, notify):
                records, running = self._installed_snapshot()
                selected = matching_model((item['name'] for item in records), model)
                return 'verified_selection', selected, records, running

            self._start('Checking that the selected model is still installed…', work)

    def _release_selected(self):
        model = self._installed_name()
        if model and self.cancel is None and not self.inventory_stale:
            self._start(f'Releasing memory for {model}…',
                        lambda cancel, notify: ('released', model, release_local_model(model, cancel)),
                        stoppable=False)

    def _remove_installed(self):
        model = self._installed_name()
        if model is None or self.cancel is not None:
            return
        confirm = QMessageBox(self)
        confirm.setWindowTitle('Remove installed model')
        confirm.setTextFormat(Qt.PlainText)
        confirm.setText(f'Remove {model} from local Ollama?')
        confirm.setInformativeText('Every app connected to this Ollama service will lose this model name. '
                                   'Ollama manages the underlying shared weights.')
        confirm.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        confirm.setDefaultButton(QMessageBox.Cancel)
        if confirm.exec() != QMessageBox.Yes:
            return

        def work(cancel, notify):
            return 'removed', delete_local_model(model, cancel)

        self._start(f'Removing {model} from local Ollama…', work, stoppable=False)

    def _load_exports(self):
        self._start('Looking for files saved by Studio…', lambda *_: self._storage_result())

    def _storage_result(self, label=''):
        return 'exports', *saved_exports(self.root), partial_inventory(self.root), label

    def _clear_partials(self):
        reviewed = list(self.partials)
        if not reviewed or self.cancel is not None:
            return
        size = format_size(sum(item.size for item in reviewed))
        answer = QMessageBox.question(self, 'Clear unfinished downloads',
                                      f'Discard {len(reviewed)} unfinished Studio download(s) ({size})?\n'
                                      'Those downloads will start again from the beginning.',
                                      QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer == QMessageBox.Yes:
            def work(*_):
                removed = clear_partials(self.root, reviewed)
                return self._storage_result(f'Cleared {removed} unfinished download(s). ')
            self._start('Clearing unfinished downloads…', work, stoppable=False)

    def _export_selection_changed(self):
        item = self.exports.currentItem()
        selected = item.data(Qt.UserRole) if item is not None else None
        self.remove_saved.setEnabled(self.cancel is None and selected is not None)
        self.use_saved.setEnabled(self.cancel is None and selected is not None and selected.relative.lower().endswith('.gguf'))

    def _remove_export(self):
        item = self.exports.currentItem()
        if not item or self.cancel is not None:
            return
        selected = item.data(Qt.UserRole)
        confirm = QMessageBox(self)
        confirm.setTextFormat(Qt.PlainText)
        confirm.setWindowTitle('Remove saved download')
        confirm.setText(f'Remove this standalone file ({format_size(selected.size)})?\n\n{selected.relative}')
        confirm.setInformativeText('Apps that open this file directly will need another copy.')
        confirm.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        confirm.setDefaultButton(QMessageBox.Cancel)
        if confirm.exec() != QMessageBox.Yes:
            return

        def work(*_):
            remove_export(self.root, selected)
            return self._storage_result('File removed. ')

        self._start('Removing the selected download…', work, stoppable=False)

    def _choose_local_gguf(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Choose one GGUF or all its shards', '', 'GGUF models (*.gguf)')
        if paths:
            self._import_existing(paths)

    def _use_saved(self):
        item = self.exports.currentItem()
        if self.cancel is not None or item is None:
            return
        selected = item.data(Qt.UserRole)
        siblings = {file.relative: file for file in self.saved_files
                    if Path(file.relative).parent == Path(selected.relative).parent}
        files = [ModelFile(name, file.size) for name, file in siblings.items()]
        try:
            group = selected_files(files, [ModelFile(selected.relative, selected.size)])
        except ValueError as exc:
            self._set_status(str(exc), error=True)
            return
        self._import_existing([file.name for file in group], [siblings[file.name] for file in group])

    def _import_existing(self, paths, saved=None):
        if not gguf_selection([ModelFile(Path(path).name, 0) for path in paths]):
            self._set_status('Choose one GGUF model or all the files in one split model.', error=True)
            return

        def work(cancel, notify):
            require_model_runtime(cancel, notify)
            root = library_root(self.root) if saved is not None else None
            with storage_lock(root) if saved is not None else nullcontext():
                source = [reviewed_export(root, item) for item in saved] if saved is not None else paths
                model = import_gguf(source, cancel, notify)
            if not model_present(11434, model):
                raise RuntimeError('The imported model did not appear in local Ollama.')
            return 'installed', model, 'local'

        self._start('Preparing your existing GGUF for Ollama…', work)

    def _open_folder(self):
        destination = self.destination if self.destination is not None else self.root
        if native_path(destination).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(display_path(destination))))

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
