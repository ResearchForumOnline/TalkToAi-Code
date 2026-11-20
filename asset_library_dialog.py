"""Game asset catalog and cancellable project installation."""
from pathlib import Path
import threading
import urllib.error
import zipfile

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QProgressBar, QPushButton, QVBoxLayout)

from asset_library import PACKS, asset_engine, game_asset_directory, install_pack, pack_source


class AssetSignals(QObject):
    progress = Signal(str, int)
    finished = Signal(str)


class AssetLibraryDialog(QDialog):
    def __init__(self, parent, project):
        super().__init__(parent)
        self.project = Path(project).resolve()
        self.library = game_asset_directory(self.project)
        self.cancel = None
        self.signals = AssetSignals(self)
        self.signals.progress.connect(self.show_progress)
        self.signals.finished.connect(self.finish_work)
        self.setWindowTitle('Game assets')
        self.resize(680, 530)
        layout = QVBoxLayout(self)
        location = self.library.relative_to(self.project).as_posix()
        engine = asset_engine(self.project)
        engine_note = {
            'godot': ' Godot sees these files under res://. Run the import check from Game Lab.',
            'unity': ' Unity will import compatible files under Assets. Run the import check from Game Lab.',
        }.get(engine, ' Select a Godot or Unity project for engine-specific placement.')
        hint = QLabel(f'Free CC0 packs from Kenney. New packs install in {location}.' + engine_note)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.listing = QListWidget()
        self.listing.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.listing.itemSelectionChanged.connect(self.update_actions)
        layout.addWidget(self.listing, 1)
        self.status = QLabel('')
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setValue(0)
        self.bar.hide()
        layout.addWidget(self.bar)
        row = QHBoxLayout()
        layout.addLayout(row)
        self.install = QPushButton('Install selected')
        self.import_zip = QPushButton('Import verified ZIP…')
        self.source = QPushButton('Open source page')
        self.folder = QPushButton('Open installed folder')
        self.stop = QPushButton('Stop')
        close = QPushButton('Close')
        for button, action in ((self.install, self.start), (self.import_zip, self.choose_zip),
                               (self.source, self.open_source), (self.folder, self.open_folder),
                               (self.stop, self.stop_work), (close, self.reject)):
            row.addWidget(button)
            button.clicked.connect(action)
        self.refresh()
        self.update_actions()

    def refresh(self):
        selected = {item.data(Qt.UserRole)['slug'] for item in self.listing.selectedItems()}
        self.listing.clear()
        for pack in PACKS:
            installed = (self.library / pack['slug']).is_dir()
            size = f"{pack['bytes'] / 1_000_000:.1f} MB"
            caption = f"{pack['title']} · {pack['category']} · {size}"
            if installed:
                caption += ' · Project folder exists'
            item = QListWidgetItem(caption)
            item.setData(Qt.UserRole, pack)
            self.listing.addItem(item)
            item.setSelected(pack['slug'] in selected)

    def update_actions(self):
        selected = self.listing.selectedItems()
        count = len(selected)
        running = self.cancel is not None
        self.install.setEnabled(not running and count > 0)
        self.import_zip.setEnabled(not running and count == 1)
        self.source.setEnabled(not running and count == 1)
        self.folder.setEnabled(not running and count == 1 and
                               (self.library / selected[0].data(Qt.UserRole)['slug']).is_dir())
        self.listing.setEnabled(not running)
        self.stop.setEnabled(running)

    def open_source(self):
        item = self.listing.selectedItems()[0]
        QDesktopServices.openUrl(QUrl(pack_source(item.data(Qt.UserRole))))

    def open_folder(self):
        item = self.listing.selectedItems()[0]
        path = self.library / item.data(Qt.UserRole)['slug']
        if path.is_dir() and path.resolve().is_relative_to(self.library):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def choose_zip(self):
        value, _ = QFileDialog.getOpenFileName(self, 'Open original asset pack ZIP', '', 'ZIP files (*.zip)')
        if value:
            self.start(local_archive=value)

    def start(self, checked=False, local_archive=None):
        if self.cancel is not None:
            return
        selected = [item.data(Qt.UserRole) for item in self.listing.selectedItems()]
        if not selected:
            return
        self.cancel = cancel = threading.Event()
        self.update_actions()
        self.bar.show()
        self.show_progress('Preparing selected asset packs…', 0)

        def work():
            installed = 0
            skipped = 0
            failures = []
            for pack in selected:
                if cancel.is_set():
                    break
                title = pack['title']
                self.signals.progress.emit(f'Installing {title}…', 0)
                try:
                    install_pack(self.project, pack, cancel,
                                 lambda size, total: self.signals.progress.emit(f'Downloading {title}…', min(99, size * 100 // total)),
                                 local_archive=local_archive)
                    installed += 1
                except FileExistsError:
                    skipped += 1
                except InterruptedError:
                    break
                except urllib.error.URLError:
                    failures.append(f'{title}. The creator’s download is unavailable. Use Open source page or Import verified ZIP.')
                except (OSError, ValueError, zipfile.BadZipFile) as exc:
                    failures.append(f'{title}. {exc}')
            status = f'{installed} installed. {skipped} existing folders kept.'
            if cancel.is_set():
                status += ' Installation stopped.'
            if failures:
                status += '\n' + '\n'.join(failures[:4])
            if len(failures) > 4:
                status += f'\n{len(failures) - 4} further packs failed.'
            self.signals.finished.emit(status)

        threading.Thread(target=work, daemon=True).start()

    def show_progress(self, message, value):
        self.status.setText(message)
        self.bar.setValue(value)

    def finish_work(self, message):
        self.cancel = None
        self.bar.hide()
        self.status.setText(message)
        self.refresh()
        self.update_actions()

    def stop_work(self):
        if self.cancel:
            self.cancel.set()
            self.status.setText('Stopping asset installation…')

    def reject(self):
        if self.cancel:
            self.stop_work()
        else:
            super().reject()
