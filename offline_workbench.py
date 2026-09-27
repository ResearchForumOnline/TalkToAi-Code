"""Discoverable local tools: the dialog only selects work; Studio owns execution."""
from pathlib import Path
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from offline_assistant import OFFLINE_COMMANDS
from project_templates import list_project_templates


def format_report(result):
    """Plain report is also retained verbatim in the Tools pane."""
    lines = [str(result.get('title', 'Local tools')), 'Status: '+str(result.get('status','unknown')).replace('_',' '), '', str(result.get('summary', ''))]
    details = result.get('details', [])
    lines.extend([''] + [str(item) for item in details] if details else [])
    metrics = result.get('metrics', {})
    scope = ', '.join(f'{key.replace("_", " ")}: {value}' for key, value in metrics.items()
        if key in ('files_seen','files_read','elapsed_ms','limited'))
    if scope:lines.extend(['', 'Scope: ' + scope])
    lines.extend(['', 'Local program result · no model or API request.'])
    return '\n'.join(lines)


class LocalToolsDialog(QDialog):
    def __init__(self, project, parent=None, *, allow_create=True):
        super().__init__(parent)
        self.setWindowTitle('Local tools · no AI required')
        self.resize(730, 580)
        self.selected_request = None
        self.selected_template = None
        layout = QVBoxLayout(self)
        heading = QLabel('Useful work, even with your model offline')
        heading.setStyleSheet('font-size:20px;font-weight:600;color:#c4ffed;')
        layout.addWidget(heading)
        intro = QLabel('These built-in programs run on your computer. No model, API key, subscription or internet connection is needed.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        location = QLabel('Selected folder: ' + str(project))
        location.setTextFormat(Qt.PlainText);location.setWordWrap(True)
        layout.addWidget(location)
        tabs = QTabWidget();layout.addWidget(tabs, 1)
        inspect = QWidget();checks = QVBoxLayout(inspect)
        self.commands = QListWidget();self.commands.setObjectName('local_command_list')
        for item in OFFLINE_COMMANDS:
            self.commands.addItem(item['title'])
        checks.addWidget(self.commands, 1)
        self.description = QLabel();self.description.setWordWrap(True);self.description.setTextFormat(Qt.PlainText)
        checks.addWidget(self.description)
        self.query = QLineEdit();self.query.setObjectName('local_query')
        checks.addWidget(self.query)
        self.example = QLabel();self.example.setWordWrap(True);self.example.setTextFormat(Qt.PlainText)
        checks.addWidget(self.example)
        self.run_button = QPushButton('Run locally');self.run_button.setObjectName('local_run')
        self.run_button.setDefault(True);checks.addWidget(self.run_button)
        self.run_button.clicked.connect(self.choose_request)
        self.commands.currentRowChanged.connect(self.refresh_command)
        self.query.textChanged.connect(self.refresh_enabled)
        self.query.returnPressed.connect(self.choose_request)
        tabs.addTab(inspect, 'Inspect and find')
        self.commands.setCurrentRow(0)

        create = QWidget();forms = QVBoxLayout(create)
        info = QLabel('Create a working starter in a new folder. The full source is included so you can change it yourself or continue with your AI model.')
        info.setWordWrap(True);forms.addWidget(info)
        self.templates = list_project_templates()
        self.template = QComboBox();self.template.setObjectName('local_template')
        for item in self.templates:self.template.addItem(item['title'], item['id'])
        forms.addWidget(self.template)
        self.template_detail = QLabel();self.template_detail.setWordWrap(True);self.template_detail.setTextFormat(Qt.PlainText)
        forms.addWidget(self.template_detail)
        forms.addWidget(QLabel('New folder name'))
        self.project_name = QLineEdit();self.project_name.setObjectName('local_project_name')
        self.project_name_edited=False;self.project_root=Path(project)
        self.project_name.textEdited.connect(lambda _:setattr(self,'project_name_edited',True))
        forms.addWidget(self.project_name)
        self.destination = QLabel();self.destination.setWordWrap(True);self.destination.setTextFormat(Qt.PlainText)
        forms.addWidget(self.destination)
        forms.addStretch()
        note = QLabel('Existing folders are never replaced. Use Act mode to create files.' if not allow_create else 'Creates local files only. Existing folders are never replaced.')
        note.setWordWrap(True);forms.addWidget(note)
        self.create_button = QPushButton('Create project');self.create_button.setObjectName('local_create')
        self.create_button.setEnabled(allow_create);forms.addWidget(self.create_button)
        self.create_button.clicked.connect(self.choose_template)
        self.project_name.textChanged.connect(lambda _:self.destination.setText('Will create: ' + str(Path(project) / self.project_name.text())))
        self.template.currentIndexChanged.connect(self.refresh_template)
        self.destination.setText('Will create: ' + str(Path(project) / self.project_name.text()))
        self.refresh_template()
        tabs.addTab(create, 'Create a project')
        close = QPushButton('Close');close.clicked.connect(self.reject);layout.addWidget(close)

    def refresh_command(self, index):
        if not 0 <= index < len(OFFLINE_COMMANDS):return
        command = OFFLINE_COMMANDS[index]
        self.description.setText(command['description'])
        needs_query = command['id'] in ('find_files', 'find_symbols')
        self.query.setVisible(needs_query)
        self.query.setPlaceholderText('Filename or glob, e.g. *.gd' if command['id'] == 'find_files' else 'Function or class name, e.g. player')
        self.example.setText('You can also type: ' + command['example'])
        self.refresh_enabled()

    def refresh_enabled(self):
        index = self.commands.currentRow()
        if not 0 <= index < len(OFFLINE_COMMANDS):self.run_button.setEnabled(False);return
        needs_query = OFFLINE_COMMANDS[index]['id'] in ('find_files', 'find_symbols')
        self.run_button.setEnabled(not needs_query or bool(self.query.text().strip()))

    def choose_request(self):
        if not self.run_button.isEnabled():return
        command = OFFLINE_COMMANDS[self.commands.currentRow()]['id']
        self.selected_request = {'command':command, 'query':self.query.text().strip() if command in ('find_files', 'find_symbols') else ''}
        self.accept()

    def refresh_template(self):
        index = self.template.currentIndex()
        if 0 <= index < len(self.templates):
            self.template_detail.setText(self.templates[index]['description'])
            if not self.project_name_edited:
                base='neon-drift' if self.template.currentData()=='browser-game' else 'taskbox'
                candidate=base
                for number in range(2,1002):
                    if not os.path.lexists(self.project_root/candidate):break
                    candidate=base+'-'+str(number)
                self.project_name.setText(candidate)

    def choose_template(self):
        if not self.create_button.isEnabled():return
        self.selected_template = (self.template.currentData(), self.project_name.text().strip())
        self.accept()
