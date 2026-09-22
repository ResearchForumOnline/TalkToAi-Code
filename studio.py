"""TalkToAi Code Studio: Qt desktop conversation and agent workspace."""
import json
import os
from pathlib import Path
import sys
import threading
import uuid
import urllib.request
import time
import re
import copy
import subprocess
from PySide6.QtCore import Qt, Signal, QObject, QTimer, QEvent
from PySide6.QtGui import QFont, QTextCursor, QKeySequence, QShortcut, QDesktopServices, QIcon, QPixmap, QPainter, QColor
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QComboBox, QListWidget, QListWidgetItem, QSplitter, QTabWidget,
    QTextBrowser, QPlainTextEdit, QFileDialog, QMessageBox, QFrame, QInputDialog,
    QSystemTrayIcon, QMenu, QDialog, QLineEdit, QCheckBox, QDialogButtonBox)
from agent_core import ProjectTools, run_agent, restore_checkpoint, set_active_remote, set_agent_preferences, set_active_provider
from routing import choose_route, ensure_local_model
from ssh_tools import SSHProfile, SSHSession, load_profiles, save_profiles
from providers import ProviderProfile, load_profiles as load_provider_profiles, save_profiles as save_provider_profiles
from desktop_inventory import inspect_desktop

SOURCE = Path(__file__).resolve().parent
HOME = Path(os.environ.get('TALKTOAI_CODE_HOME', str(Path(sys.executable).parent if getattr(sys, 'frozen', False) else SOURCE)))
STATE = Path(os.environ.get('LOCALAPPDATA', str(HOME))) / 'TalkToAiCode'
STATE.mkdir(parents=True, exist_ok=True)
SESSION = STATE / 'studio.json'
CONNECTIONS = STATE / 'connections.json'
PROVIDERS = STATE / 'providers.json'

STYLE = '''
QWidget { background:#191919; color:#e7e7e7; font-family:'Segoe UI'; font-size:13px; }
QWidget#sidebar { background:#121212; border-right:1px solid #2b2b2b; }
QLabel#brand { font-size:17px; font-weight:600; padding:14px 4px; }
QLabel#muted { color:#969696; font-size:12px; }
QLabel#hero { font-size:30px; font-weight:600; }
QPushButton { background:#252525; border:1px solid #363636; border-radius:7px; padding:8px 12px; }
QPushButton:hover { background:#333333; border-color:#515151; }
QPushButton:disabled { color:#666; }
QPushButton#accent { background:#ededed; color:#151515; font-weight:600; }
QComboBox { background:#242424; border:1px solid #383838; border-radius:6px; padding:6px 10px; }
QListWidget { background:transparent; border:0; outline:0; }
QListWidget::item { padding:10px; margin:2px 0; border-radius:7px; color:#e7e7e7; }
QListWidget::item:selected { background:#303030; }
QListWidget::item:hover { background:#242424; }
QTextBrowser { border:0; background:transparent; font-size:15px; padding:14px; }
QPlainTextEdit { background:#202020; border:1px solid #353535; border-radius:8px; padding:10px; selection-background-color:#435266; }
QFrame#composer { background:#242424; border:1px solid #454545; border-radius:14px; }
QFrame#composer QPlainTextEdit { background:transparent; border:0; }
QFrame#composer QWidget { background:transparent; }
QFrame#composer QPushButton#accent { background:#ededed; color:#151515; }
QTabWidget::pane { border:1px solid #303030; }
QTabBar::tab { background:#202020; padding:10px 14px; color:#aaa; }
QTabBar::tab:selected { color:white; border-bottom:2px solid #d1d1d1; }
QSplitter::handle { background:#2c2c2c; width:1px; }
QScrollBar:vertical { background:transparent; width:8px; }
QScrollBar::handle:vertical { background:#494949; border-radius:4px; min-height:35px; }
'''

class Bus(QObject):
    event = Signal(str, object)

class Composer(QPlainTextEdit):
    submitted = Signal()
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
            self.submitted.emit()
        else:
            super().keyPressEvent(event)

class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('TalkToAi Code')
        self.resize(1480, 930)
        self.setMinimumSize(1100, 700)
        self.bus = Bus()
        self.bus.event.connect(self.handle_event)
        self.cancel = threading.Event()
        self.busy = False
        self.current_file = None
        self.partial = ''
        self.stream_dirty=False
        self.started_at=None
        self.tool_events=[]
        self.pending_prompt=''
        self.allow_quit=False
        self.tray=None
        self.config = {
            'project': str(HOME.parent),
            'local_model': 'qwen3.5:4b',
            'local_large_model': 'smtek/Qwen3.8-27B',
            'server_model': 'openzero-qwen3-coder-30b-a3b-q3',
            'approval_policy': 'ask_remote',
            'auto_context': True,
            'show_tool_activity': True,
            'remote_enabled': True,
            'remote_pilot': True,
            'active_ssh_alias': '',
            'active_ssh_path': '',
            'active_provider': '',
            'access_mode': 'full_user',
            'pc_pilot': True,
        }
        try:
            self.config.update(json.loads((HOME / 'config.json').read_text(encoding='utf-8-sig')))
        except (OSError, ValueError):
            pass
        self.ssh_profiles = load_profiles(CONNECTIONS)
        try:
            self.config.update(json.loads((STATE/'config.json').read_text(encoding='utf-8')))
        except (OSError, ValueError):
            pass
        self.provider_profiles = load_provider_profiles(PROVIDERS)
        try:
            self.tasks = json.loads(SESSION.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.tasks = []
        if not isinstance(self.tasks, list):
            self.tasks = []
        for task in self.tasks:
            if isinstance(task, dict):
                task.setdefault('pinned', False)
        self.task = None
        self.build()
        if self.config.get('approval_policy')=='plan':self.mode.setCurrentText('Plan')
        self.refresh_tasks()
        if self.tasks:
            self.select_task(0)
        else:
            self.new_task()
        QShortcut(QKeySequence('Ctrl+N'), self, self.new_task)
        QShortcut(QKeySequence('Ctrl+S'), self, self.save_file)
        QShortcut(QKeySequence('Ctrl+O'), self, self.choose_project)
        QShortcut(QKeySequence('Ctrl+K'), self, self.command_palette)
        QShortcut(QKeySequence('F1'), self, self.faq_dialog)
        self.prompt.textChanged.connect(self.save_draft)
        QTimer.singleShot(500, self.health)
        self.paint_timer=QTimer(self);self.paint_timer.timeout.connect(self.paint_stream);self.paint_timer.start(120)
        self.clock_timer=QTimer(self);self.clock_timer.timeout.connect(self.tick);self.clock_timer.start(1000)
        self.install_tray()
        self.refresh_connection_label()
        self.refresh_access_label()

    def install_tray(self):
        icon=QPixmap(64,64);icon.fill(Qt.transparent)
        painter=QPainter(icon);painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor('#202c45'));painter.setPen(Qt.NoPen);painter.drawRoundedRect(2,2,60,60,14,14)
        painter.setPen(QColor('#a4e8cb'));painter.setFont(QFont('Segoe UI',29,QFont.Bold));painter.drawText(icon.rect(),Qt.AlignCenter,'T');painter.end()
        self.setWindowIcon(QIcon(icon))
        if not QSystemTrayIcon.isSystemTrayAvailable():return
        self.tray=QSystemTrayIcon(QIcon(icon),self);self.tray.setToolTip('TalkToAi Code — ready')
        menu=QMenu(self)
        menu.addAction('Open TalkToAi Code',self.show_from_tray)
        menu.addAction('New task',self.tray_new_task)
        menu.addAction('Stop current task',self.stop_task)
        menu.addSeparator();menu.addAction('Quit TalkToAi Code',self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason:self.show_from_tray() if reason in (QSystemTrayIcon.Trigger,QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

    def show_from_tray(self):self.showNormal();self.raise_();self.activateWindow()

    def tray_new_task(self):self.show_from_tray();self.new_task()

    def quit_app(self):
        self.allow_quit=True;self.cancel.set();self.persist()
        if self.tray:self.tray.hide()
        self.close();QApplication.instance().quit()

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type()==QEvent.WindowStateChange and self.isMinimized() and getattr(self,'tray',None):
            QTimer.singleShot(0,self.hide)

    def button(self, text, callback, parent, accent=False):
        b = QPushButton(text)
        if accent:
            b.setObjectName('accent')
        b.clicked.connect(callback)
        parent.addWidget(b)
        return b

    def build(self):
        body = QWidget(); outer = QHBoxLayout(body); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        self.setCentralWidget(body)
        sidebar = QWidget(); sidebar.setObjectName('sidebar'); sidebar.setFixedWidth(248)
        side = QVBoxLayout(sidebar); side.setContentsMargins(14,12,14,16); side.setSpacing(10)
        label = QLabel('◈  TalkToAi Code'); label.setObjectName('brand'); side.addWidget(label)
        self.new_button = self.button('＋  New task', self.new_task, side)
        self.project_button = self.button('▱  Open project', self.choose_project, side)
        self.project_label = QLabel(); self.project_label.setWordWrap(True); self.project_label.setObjectName('muted'); side.addWidget(self.project_label)
        self.access_label = QLabel(); self.access_label.setWordWrap(True); self.access_label.setObjectName('muted'); side.addWidget(self.access_label)
        label = QLabel('TASKS'); label.setObjectName('muted'); side.addWidget(label)
        self.task_search=QLineEdit();self.task_search.setPlaceholderText('Search tasks…');self.task_search.textChanged.connect(self.filter_tasks);side.addWidget(self.task_search)
        self.pin_task_button = self.button('📌  Pin current task', self.toggle_pin_task, side)
        self.task_list = QListWidget(); self.task_list.currentRowChanged.connect(self.select_task); side.addWidget(self.task_list,1)
        self.button('⚙  Settings', self.settings, side)
        self.button('About & updates', self.updates_dialog, side)
        self.button('⌁  Connections', self.connections_dialog, side)
        self.button('Link ZeroThink account', self.link_zerothink, side)
        self.button('❔  FAQ / How to', self.faq_dialog, side)
        self.button('Actions  ·  Ctrl+K',self.command_palette,side)
        self.connection_label = QLabel('⌁  No SSH connection'); self.connection_label.setObjectName('muted'); side.addWidget(self.connection_label)
        self.health_label = QLabel('○  Checking models'); self.health_label.setObjectName('muted'); side.addWidget(self.health_label)
        outer.addWidget(sidebar)
        split = QSplitter(); outer.addWidget(split,1)
        center = QWidget(); chat = QVBoxLayout(center); chat.setContentsMargins(30,18,30,20); chat.setSpacing(12)
        bar = QHBoxLayout(); self.title = QLabel('New task'); self.title.setFont(QFont('Segoe UI',14,QFont.DemiBold)); bar.addWidget(self.title,1)
        self.button('Workspace  ▥', lambda: self.right.setVisible(not self.right.isVisible()), bar)
        chat.addLayout(bar)
        shortcuts=QHBoxLayout()
        for title,command in [('Inspect project','inspect project'),('Run tests','run tests'),('Open Desktop','open desktop')]:
            self.button(title,lambda checked=False,c=command:self.quick_command(c),shortcuts)
        chat.addLayout(shortcuts)
        self.transcript = QTextBrowser(); self.transcript.setOpenExternalLinks(False); self.transcript.document().setDefaultStyleSheet('p {line-height:1.6;} pre {background:#242424; padding:12px;} code {font-family:Consolas;} h2 {font-size:17px;}')
        chat.addWidget(self.transcript,1)
        self.status = QLabel('Ready'); self.status.setObjectName('muted'); chat.addWidget(self.status)
        box = QFrame(); box.setObjectName('composer'); composer = QVBoxLayout(box)
        self.prompt = Composer(); self.prompt.setPlaceholderText('Ask anything, or describe what to build…'); self.prompt.setFixedHeight(104); self.prompt.submitted.connect(self.send); composer.addWidget(self.prompt)
        options = QHBoxLayout()
        self.route = QComboBox(); self.route.addItems(['Auto · AMD 30B / fallback', 'Local · compact CPU', 'AMD · Qwen Coder 30B-A3B', 'Local · Qwen3.8 27B', 'API · optional provider']); options.addWidget(self.route)
        self.mode = QComboBox(); self.mode.addItems(['Act', 'Plan']); self.mode.setToolTip('Act permits file edits and host commands. Plan only reads project files. Commands are not OS-sandboxed.'); options.addWidget(self.mode)
        options.addStretch()
        self.stop = self.button('Stop', self.stop_task, options); self.stop.setEnabled(False)
        self.send_button = self.button('↑  Send', self.send, options, True)
        composer.addLayout(options); chat.addWidget(box)
        hint=QLabel('Enter to send  ·  Shift+Enter for a new line  ·  Ctrl+O to open a project'); hint.setObjectName('muted'); chat.addWidget(hint)
        self.performance_label=QLabel('Auto prefers a verified coding route, then falls back when unavailable.');self.performance_label.setObjectName('muted');chat.addWidget(self.performance_label)
        split.addWidget(center)
        self.right = QTabWidget(); self.right.setMinimumWidth(320); split.addWidget(self.right); split.setSizes([850,390])
        files = QWidget(); fl=QVBoxLayout(files)
        self.filter_label=QLabel('Project files'); fl.addWidget(self.filter_label)
        self.files=QListWidget(); self.files.itemDoubleClicked.connect(self.open_file); fl.addWidget(self.files,1)
        row=QHBoxLayout(); self.button('Refresh',self.refresh_files,row); self.button('Save',self.save_file,row); fl.addLayout(row)
        self.editor=QPlainTextEdit(); self.editor.setFont(QFont('Consolas',11)); fl.addWidget(self.editor,2); self.right.addTab(files,'Files')
        changes=QWidget(); cl=QVBoxLayout(changes)
        self.change_list=QListWidget(); self.change_list.currentRowChanged.connect(self.show_diff); cl.addWidget(self.change_list,1)
        self.diff=QPlainTextEdit(); self.diff.setReadOnly(True); self.diff.setFont(QFont('Consolas',10)); cl.addWidget(self.diff,3)
        self.button('Restore selected edit',self.undo,cl); self.right.addTab(changes,'Changes')
        self.output=QPlainTextEdit(); self.output.setReadOnly(True); self.output.setFont(QFont('Consolas',10)); self.right.addTab(self.output,'Tools')
        game=QWidget(); gl=QVBoxLayout(game)
        lab=QLabel('Game Lab'); lab.setFont(QFont('Segoe UI',20,QFont.DemiBold)); gl.addWidget(lab)
        desc=QLabel('Launch a project, run an import check,\nand keep visual evidence with your task.'); desc.setWordWrap(True); desc.setObjectName('muted'); gl.addWidget(desc)
        self.button('▶  Run Godot project',lambda:self.game_command(False),gl)
        self.button('✓  Godot import check',lambda:self.game_command(True),gl)
        self.button('Open Blender',self.blender,gl)
        self.button('Capture this app',self.capture,gl)
        self.button('Capture game / desktop in 3s',self.capture_desktop,gl)
        self.button('Open project folder',lambda:os.startfile(self.task['project']),gl)
        gl.addStretch(); self.right.addTab(game,'Game')
        artifacts=QWidget();al=QVBoxLayout(artifacts)
        al.addWidget(QLabel('Screenshots and generated evidence'))
        self.artifacts=QListWidget();self.artifacts.itemDoubleClicked.connect(self.open_artifact);al.addWidget(self.artifacts)
        self.right.addTab(artifacts,'Evidence')

    def persist(self):
        tmp=SESSION.with_suffix('.tmp'); tmp.write_text(json.dumps(self.tasks,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(SESSION)

    def save_draft(self):
        if self.task:self.task['draft']=self.prompt.toPlainText()

    def filter_tasks(self,text):
        for i in range(self.task_list.count()):self.task_list.item(i).setHidden(text.casefold() not in self.task_list.item(i).text().casefold())

    def quick_command(self,text):
        if self.busy:return
        self.prompt.setPlainText(text);self.send()

    def rename_task(self):
        if self.busy:return
        name,ok=QInputDialog.getText(self,'Rename task','Task name:',text=self.task['title'])
        if ok and name.strip():self.task['title']=name.strip()[:120];self.title.setText(self.task['title']);self.refresh_tasks();self.persist()

    def toggle_pin_task(self):
        if self.busy or not self.task:return
        self.task['pinned'] = not self.task.get('pinned', False)
        task_id = self.task['id']
        self.persist(); self.refresh_tasks(); self.select_task_by_id(task_id)
        self.status.setText('Task pinned to the top' if self.task.get('pinned') else 'Task unpinned')

    def select_task_by_id(self, task_id):
        for row in range(self.task_list.count()):
            if self.task_list.item(row).data(Qt.UserRole) == task_id:
                self.task_list.setCurrentRow(row)
                self.select_task(row)
                return

    def fork_task(self):
        if self.busy:return
        self.save_draft();task=copy.deepcopy(self.task);task['id']=uuid.uuid4().hex;task['title']='Branch · '+task['title'];task['changes']=[]
        self.tasks.insert(0,task);self.refresh_tasks();self.select_task(0);self.persist()
        self.status.setText('Conversation branched · project files are shared')

    def command_palette(self):
        if self.busy:self.status.setText('Use Steer or Stop while a task is running.');return
        dialog=QDialog(self);dialog.setWindowTitle('Actions');dialog.resize(600,480);layout=QVBoxLayout(dialog)
        query=QLineEdit();query.setPlaceholderText('Find an action…');layout.addWidget(query);items=QListWidget();layout.addWidget(items)
        actions=[('Open project',self.choose_project),('Open Desktop',lambda:self.quick_command('open desktop')),('Inspect project',lambda:self.quick_command('inspect project')),('Run tests',lambda:self.quick_command('run tests')),('Launch game',lambda:self.quick_command('launch game')),('Capture screenshot',lambda:self.quick_command('take a screenshot')),('Map project',lambda:self.quick_command('map project')),('Rename task',self.rename_task),('Pin or unpin task',self.toggle_pin_task),('Branch conversation',self.fork_task),('Export task report',self.export_task),('Settings',self.settings),('SSH connections',self.connections_dialog),('API providers',self.providers_dialog),('Model choices and storage',self.models_dialog),('Open Cline',self.cline),('FAQ / How to',self.faq_dialog)]
        for label,callback in actions:items.addItem(label)
        def filter_items(text):
            for i in range(items.count()):items.item(i).setHidden(text.casefold() not in items.item(i).text().casefold())
            for i in range(items.count()):
                if not items.item(i).isHidden():items.setCurrentRow(i);break
        def activate():
            row=items.currentRow()
            if row>=0 and not items.item(row).isHidden():dialog.accept();actions[row][1]()
        query.textChanged.connect(filter_items);query.returnPressed.connect(activate);items.itemActivated.connect(lambda _:activate());items.setCurrentRow(0);query.setFocus();dialog.exec()

    def refresh_tasks(self):
        selected_id = self.task.get('id') if self.task else None
        self.task_list.blockSignals(True); self.task_list.clear()
        ordered = sorted(enumerate(self.tasks), key=lambda pair: (not pair[1].get('pinned', False), pair[0]))
        for _, task in ordered:
            item = QListWidgetItem(('📌  ' if task.get('pinned') else '') + task.get('title', 'Untitled task'))
            item.setData(Qt.UserRole, task.get('id'))
            item.setToolTip(task.get('project', ''))
            self.task_list.addItem(item)
        self.task_list.blockSignals(False)
        if selected_id:
            self.select_task_by_id(selected_id)

    def new_task(self):
        if self.busy: return
        task={'id':uuid.uuid4().hex,'title':'New task','project':self.task['project'] if self.task else self.config['project'],'messages':[],'changes':[],'pinned':False}
        self.tasks.insert(0,task); self.refresh_tasks(); self.task_list.setCurrentRow(0); self.select_task(0); self.persist()

    def select_task(self,row):
        if self.busy or row<0 or row>=self.task_list.count(): return
        if self.task:self.task['draft']=self.prompt.toPlainText()
        item=self.task_list.item(row)
        task_id=item.data(Qt.UserRole) if item else None
        self.task=next((task for task in self.tasks if task.get('id')==task_id), None)
        if not self.task:return
        self.partial=''; self.current_file=None; self.editor.clear(); self.output.clear()
        self.prompt.setPlainText(self.task.get('draft',''))
        self.task.setdefault('artifacts',[]);self.task.setdefault('activity',[]);self.refresh_artifacts()
        for line in self.task['activity'][-60:]:self.output.appendPlainText(line)
        self.title.setText(self.task['title']); self.project_label.setText(Path(self.task['project']).name)
        self.pin_task_button.setText('📌  Unpin current task' if self.task.get('pinned') else '📌  Pin current task')
        self.project_label.setToolTip(self.task['project']); self.render(); self.refresh_changes()
        self.files.clear(); self.filter_label.setText('Double-click a file to edit · Refresh to list')

    def render(self):
        parts=[]
        for m in self.task['messages']:
            if m['role']=='tool': continue
            if m.get('content'): parts.append(('## You' if m['role']=='user' else '## TalkToAi Code')+'\n\n'+m['content'])
        if self.partial: parts.append('## TalkToAi Code\n\n'+self.partial)
        if not parts: parts=['# What will you build?\n\nDescribe the outcome. Your agent can inspect the project, edit files, use the desktop tools, run tests, launch Godot, use Blender scripts, connect to configured SSH hosts, and capture screenshots.\n\n**Quick start**\n\n1. Type **“open score arena”** for the included game, or **“open desktop”** for your Desktop folder.\n2. Use **Auto** for the tested route and **Act** when you want changes.\n3. Type **“check my desktop for server logins”** for non-secret SSH metadata.\n4. Open **FAQ / How to** for examples.\n\nTry: **“Inspect this game and add a useful feature. Run the import check.”**\n\nYou can say **“use AMD”**, **“use local”**, **“switch to plan mode”**, or steer a running task with the composer.']
        scroll=self.transcript.verticalScrollBar();follow=scroll.value()>=scroll.maximum()-40;position=scroll.value()
        self.transcript.setMarkdown('\n\n---\n\n'.join(parts))
        if follow:self.transcript.moveCursor(QTextCursor.End)
        else:scroll.setValue(position)

    def paint_stream(self):
        if self.stream_dirty:self.stream_dirty=False;self.render()

    def tick(self):
        if self.busy and self.started_at:
            self.performance_label.setText(f'{self.route_description}  ·  {int(time.monotonic()-self.started_at)}s elapsed  ·  output streams when available')

    def refresh_artifacts(self):
        self.artifacts.clear()
        for artifact in self.task.get('artifacts',[]):
            item=QListWidgetItem(Path(artifact['artifact']).name);item.setData(Qt.UserRole,artifact['artifact']);self.artifacts.addItem(item)

    def open_artifact(self,item):
        path=Path(item.data(Qt.UserRole))
        if path.is_file():QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def natural_control(self,text):
        normalized=text.strip().lower().rstrip('.!')
        if normalized in ('help','faq','how to','/help'):
            self.prompt.clear();self.faq_dialog();return True
        if normalized in ('link zerothink','login zerothink','sign in to zerothink','link agentzero'):
            self.prompt.clear();self.link_zerothink();return True
        if normalized=='check my desktop for server logins':normalized='check desktop for server logins'
        if normalized in ('open connections','ssh connections','manage connections'):
            self.prompt.clear();self.connections_dialog();return True
        if normalized in ('connect to server','connect to amd','use my amd server','check amd server','check remote status'):
            self.prompt.setPlainText('Use my AMD server. Verify the configured SSH connection and inspect the current remote project state before doing anything else.');return False
        if normalized in ('open settings','show settings','permissions'):
            self.prompt.clear();self.settings();return True
        if normalized in ('open api providers','api providers','use api provider','use api'):
            self.prompt.clear();self.providers_dialog();return True
        direct={'run tests':'run_checks','run checks':'run_checks','test this project':'run_checks','launch game':'launch_game','run game':'launch_game','take a screenshot':'capture_screenshot','capture screenshot':'capture_screenshot','inspect project':'project_info','show git changes':'git_changes','map project':'project_map','check desktop for server logins':'desktop_server_inventory','check desktop logins':'desktop_server_inventory','find server logins':'desktop_server_inventory','scan desktop for ssh':'desktop_server_inventory'}
        if normalized in direct:
            self.prompt.clear();self.task['messages'].append({'role':'user','content':text});self.render();self.persist()
            self.started_at=time.monotonic();self.route_description='Local tool · no model wait';self.cancel=threading.Event();self.set_busy(True)
            name=direct[normalized];project=self.task['project'];act=self.mode.currentText()=='Act'
            def execute():
                try:
                    self.bus.event.emit('tool',{'name':name,'args':{}})
                    result=ProjectTools(project,act,self.cancel).execute(name,{})
                    self.bus.event.emit('result',result)
                    self.bus.event.emit('message',{'role':'assistant','content':result})
                    if name=='capture_screenshot':self.bus.event.emit('artifact',json.loads(result))
                    self.bus.event.emit('status','Ready')
                except Exception as exc:self.bus.event.emit('error',str(exc))
                finally:self.bus.event.emit('finished',None)
            threading.Thread(target=execute,daemon=True).start();return True
        controls={'use amd':2,'switch to amd':2,'use local':1,'switch to local':1,'use auto':0,'automatic routing':0,'use api':4,'use api provider':4}
        reply=None
        if normalized in ('open desktop','open my desktop') or normalized=='open score arena' or normalized.startswith('open project '):
            path=Path.home()/'Desktop' if normalized in ('open desktop','open my desktop') else HOME/'samples/score-arena' if normalized=='open score arena' else Path(text.strip()[13:].strip().strip('"'))
            if not path.is_dir():self.error('That project folder does not exist.');return True
            self.new_task();self.task['project']=str(path.resolve());self.project_label.setText(path.name);self.refresh_files();reply='Opened project: '+str(path.resolve())
        if normalized in controls:self.route.setCurrentIndex(controls[normalized]);reply='Model route set to '+self.route.currentText()+'.'
        elif normalized in ('plan mode','switch to plan mode','act mode','switch to act mode'):
            self.mode.setCurrentText('Plan' if 'plan' in normalized else 'Act');reply='Mode set to '+self.mode.currentText()+'.'
        if reply:
            self.task['messages'] += [{'role':'user','content':text},{'role':'assistant','content':reply}];self.prompt.clear();self.persist();self.render();return True
        return False

    def choose_project(self):
        if self.busy:return
        path=QFileDialog.getExistingDirectory(self,'Open project',self.task['project'])
        if path:
            self.new_task(); self.task['project']=path; self.project_label.setText(Path(path).name); self.persist(); self.refresh_files()

    def refresh_files(self):
        try:
            self.files.clear(); self.files.addItems(ProjectTools(self.task['project']).files()); self.filter_label.setText('Double-click a file to edit')
        except Exception as exc:self.error(exc)

    def open_file(self,item):
        try:
            tools=ProjectTools(self.task['project']); content=tools.execute('read_file',{'path':item.text()})
            self.current_file=item.text(); self.editor.setPlainText(content); self.filter_label.setText(item.text())
        except Exception as exc:self.error(exc)

    def save_file(self):
        if self.busy or not self.current_file:return
        try:
            tools=ProjectTools(self.task['project'],True); tools.execute('write_file',{'path':self.current_file,'content':self.editor.toPlainText()})
            self.task['changes']+=tools.changes; self.persist(); self.refresh_changes(); self.status.setText('File saved · checkpoint created')
        except Exception as exc:self.error(exc)

    def send(self):
        text=self.prompt.toPlainText().strip()
        if not text:return
        if self.busy:
            self.pending_prompt=(self.pending_prompt+'\n\n'+text).strip()
            self.prompt.clear()
            self.stop_task()
            self.status.setText('Steering queued · finishing the current tool safely')
            return
        if self.natural_control(text):return
        text=self.prompt.toPlainText().strip()
        try:ProjectTools(self.task['project'])
        except Exception as exc:self.error(exc);return
        self.prompt.clear(); self.task['messages'].append({'role':'user','content':text})
        self.task['draft']=''
        if self.task['title']=='New task':self.task['title']=text.splitlines()[0][:45];self.title.setText(self.task['title']);self.refresh_tasks()
        self.partial=''; self.render(); self.persist(); self.cancel=threading.Event(); self.set_busy(True)
        preference=('auto','local','server','local_large','provider')[self.route.currentIndex()]
        self.started_at=time.monotonic();self.route_description='Selecting runtime';self.status.setText('Checking installed models…')
        project=self.task['project']; history=list(self.task['messages']); act=self.mode.currentText()=='Act'
        active_remote=self.active_remote()
        requested_ssh=act and bool(re.search(r'\b(ssh|log ?in|connect)\b',text,re.I)) and bool(re.search(r'\b(server|host|ssh)\b|\.[a-z]{2,}',text,re.I))
        set_active_remote(active_remote if self.config.get('remote_enabled') and self.config.get('remote_pilot',True) else None)
        set_agent_preferences(
            remote_allowed=requested_ssh or (bool(self.config.get('remote_enabled')) and self.config.get('approval_policy') == 'auto_remote' and bool(self.config.get('remote_pilot',True))),
            auto_context=bool(self.config.get('auto_context', True)),
            desktop_access=self.config.get('access_mode','full_user')=='full_user',
            pc_pilot=bool(self.config.get('pc_pilot',True)),
            remote_pilot=requested_ssh or bool(self.config.get('remote_pilot',True)),
        )
        def work():
            try:
                try:benchmarks=json.loads((HOME/'benchmark-results.json').read_text(encoding='utf-8'))
                except (OSError,ValueError):benchmarks={}
                if preference=='provider':
                    profile=self.active_provider()
                    if not profile:raise ValueError('No API provider is configured. Open API providers and add an OpenAI-compatible endpoint.')
                    set_active_provider(profile)
                    selected={'route':'provider','url':profile.base_url,'model':profile.model,'reason':'selected user provider; free-tier status is controlled by the provider'}
                else:
                    set_active_provider(None)
                    if preference in ('local','local_large'):
                        requested_model=self.config['local_model'] if preference=='local' else self.config.get('local_large_model', self.config['local_model'])
                        ensure_local_model(requested_model, self.bus.event.emit)
                    selected=choose_route(self.config,preference,benchmarks)
                if self.cancel.is_set():return
                self.bus.event.emit('route',selected)
                run_agent(selected['url'],selected['model'],history,project,act,self.cancel,self.bus.event.emit)
            except Exception as exc:self.bus.event.emit('error',str(exc))
            finally:
                set_active_remote(None)
                set_active_provider(None)
                set_agent_preferences(False, True, False, True, False)
                self.bus.event.emit('finished',None)
        threading.Thread(target=work,daemon=True).start()

    def set_busy(self,busy):
        self.busy=busy
        for w in (self.new_button,self.project_button,self.task_list,self.route,self.mode):w.setEnabled(not busy)
        self.prompt.setEnabled(True);self.send_button.setEnabled(True);self.send_button.setText('✦  Steer' if busy else '↑  Send')
        self.stop.setEnabled(busy)
        if self.tray:self.tray.setToolTip('TalkToAi Code — '+('working in background' if busy else 'ready'))

    def stop_task(self):
        self.cancel.set(); self.status.setText('Stopping · waiting for model or running command to yield')

    def handle_event(self,kind,data):
        if kind=='delta':self.partial+=data;self.stream_dirty=True
        elif kind=='message':self.task['messages'].append(data);self.partial='';self.persist();self.render()
        elif kind=='tool':
            line='→ '+data['name']+'\n'+json.dumps(data['args'],ensure_ascii=False)[:2000]
            if self.config.get('show_tool_activity',True):self.output.appendPlainText(line)
            self.task.setdefault('activity',[]).append(line);self.status.setText('Using '+data['name'])
        elif kind=='result':
            if self.config.get('show_tool_activity',True):self.output.appendPlainText(str(data)+'\n')
            self.task.setdefault('activity',[]).append(str(data)[-6000:]);self.persist()
        elif kind=='artifact':
            if isinstance(data,dict) and data.get('artifact'):self.task.setdefault('artifacts',[]).append(data);self.refresh_artifacts();self.persist()
        elif kind=='route':
            label='AMD' if data['route']=='server' else 'API' if data['route']=='provider' else 'PC'
            self.route_description=label+' · '+data['model'];self.status.setText(self.route_description+' · '+data['reason'])
        elif kind=='metrics':
            self.task['last_metrics']=data;self.performance_label.setText(f"{data['tokens_per_second']} tokens/s · {data['seconds']}s · step {data['step']}")
        elif kind=='change':self.task['changes'].append(data);self.persist();self.refresh_changes()
        elif kind=='status':self.status.setText(data)
        elif kind=='health':self.health_label.setText(data)
        elif kind=='error':
            self.status.setText('Request failed');self.output.appendPlainText(str(data));self.right.setCurrentIndex(2)
            self.task['messages'].append({'role':'assistant','content':'Task error: '+str(data)});self.persist();self.render()
        elif kind=='finished':
            if self.partial:self.task['messages'].append({'role':'assistant','content':self.partial+'\n\n[Interrupted]'});self.partial=''
            self.set_busy(False);self.task['draft']=self.prompt.toPlainText();self.persist();self.render()
            metrics=self.task.get('last_metrics',{})
            self.performance_label.setText(f"{self.route_description} · {int(time.monotonic()-self.started_at)}s total · last step {metrics.get('tokens_per_second','—')} tokens/s")
            if self.pending_prompt:
                queued=self.pending_prompt;self.pending_prompt=''
                self._send_queued(queued)

    def _send_queued(self,text):
        if self.busy:return
        self.prompt.setPlainText(text);self.send()

    def refresh_changes(self):
        self.change_list.clear()
        for change in self.task['changes']:self.change_list.addItem(change['path']+(' · restored' if change.get('restored') else ''))

    def show_diff(self,row):
        self.diff.setPlainText(self.task['changes'][row]['diff'] if row>=0 else '')

    def undo(self):
        row=self.change_list.currentRow()
        if self.busy or row<0:return
        try:
            change=self.task['changes'][row];restore_checkpoint(change['checkpoint']);change['restored']=True;self.persist();self.refresh_changes();self.status.setText('Edit restored')
        except Exception as exc:self.error(exc)

    def health(self):
        def check():
            labels=[]
            for name,port in [('PC',11434),('AMD',11435)]:
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/tags',timeout=3) as r:json.load(r)
                    labels.append(name+' online')
                except Exception:labels.append(name+' offline')
            self.bus.event.emit('health',' · '.join(labels))
        threading.Thread(target=check,daemon=True).start()

    def active_remote(self):
        alias=self.config.get('active_ssh_alias','').strip()
        for profile in self.ssh_profiles:
            if profile.alias == alias:
                return profile
        return None

    def refresh_connection_label(self):
        profile=self.active_remote()
        if profile and self.config.get('remote_enabled') and self.config.get('remote_pilot',True):
            self.connection_label.setText('⌁  Remote Pilot · '+profile.label)
            self.connection_label.setToolTip(profile.alias+' · '+(profile.remote_path or 'home directory')+' · automatic SSH tasks enabled')
        elif profile:
            self.connection_label.setText('⌁  SSH saved · agent off')
            self.connection_label.setToolTip(profile.alias)
        else:
            self.connection_label.setText('⌁  No SSH connection')
            self.connection_label.setToolTip('Add a host alias in Connections')

    def refresh_access_label(self):
        enabled=self.config.get('access_mode','full_user')=='full_user'
        self.access_label.setText('▣  Desktop tools · '+('ON' if enabled else 'project only'))
        self.access_label.setToolTip('Full user mode uses the signed-in account for desktop commands. Credential file reads remain excluded.')

    def active_provider(self):
        label=self.config.get('active_provider','').strip()
        return next((p for p in self.provider_profiles if p.label==label),None)

    def providers_dialog(self):
        if self.busy:return
        dialog=QDialog(self);dialog.setWindowTitle('Optional API providers');dialog.resize(800,540)
        layout=QVBoxLayout(dialog)
        intro=QLabel('Local Ollama remains the default and costs nothing. Add an OpenAI-compatible endpoint only if you have deliberately chosen its free/no-cost terms. TalkToAi Code stores the environment-variable name, never the API key.')
        intro.setWordWrap(True);intro.setObjectName('muted');layout.addWidget(intro)
        row=QHBoxLayout();listing=QListWidget();form=QVBoxLayout();row.addWidget(listing,1);row.addLayout(form,2);layout.addLayout(row,1)
        label_edit=QLineEdit();label_edit.setPlaceholderText('Provider name, e.g. Free API trial')
        url_edit=QLineEdit();url_edit.setPlaceholderText('https://provider.example/v1')
        model_edit=QLineEdit();model_edit.setPlaceholderText('Provider model name')
        env_edit=QLineEdit();env_edit.setPlaceholderText('Optional environment variable, e.g. DEEPSEEK_API_KEY')
        for title,edit in [('Name',label_edit),('Base URL',url_edit),('Model',model_edit),('API key environment variable (optional)',env_edit)]:
            form.addWidget(QLabel(title));form.addWidget(edit)
        form.addStretch();status=QLabel('No provider selected');status.setWordWrap(True);status.setObjectName('muted');form.addWidget(status)
        def reload_list():
            listing.clear()
            for p in self.provider_profiles:
                item=QListWidgetItem(p.label+'  ·  '+p.model);item.setData(Qt.UserRole,p.label);listing.addItem(item)
        def load_selected():
            item=listing.currentItem()
            if not item:return
            p=next((x for x in self.provider_profiles if x.label==item.data(Qt.UserRole)),None)
            if p:label_edit.setText(p.label);url_edit.setText(p.base_url);model_edit.setText(p.model);env_edit.setText(p.api_key_env)
        def save_current():
            try:
                profile=ProviderProfile(label_edit.text(),url_edit.text(),model_edit.text(),env_edit.text())
                old=listing.currentItem().data(Qt.UserRole) if listing.currentItem() else None
                previous=next((p for p in self.provider_profiles if p.label==old),None)
                if previous and previous.kind=='zerothink':
                    profile=ProviderProfile(label_edit.text(),url_edit.text(),model_edit.text(),kind='zerothink',engine=previous.engine)
                self.provider_profiles=[p for p in self.provider_profiles if p.label not in {old,profile.label}]
                self.provider_profiles.append(profile);save_provider_profiles(PROVIDERS,self.provider_profiles)
                self.config['active_provider']=profile.label;self.write_config();reload_list();listing.setCurrentRow(len(self.provider_profiles)-1)
                status.setText('Saved. Select API · optional provider in the composer to use it.')
            except Exception as exc:status.setText(str(exc))
        def remove_current():
            item=listing.currentItem()
            if not item:return
            label=item.data(Qt.UserRole);self.provider_profiles=[p for p in self.provider_profiles if p.label!=label];save_provider_profiles(PROVIDERS,self.provider_profiles)
            if self.config.get('active_provider')==label:self.config['active_provider']='';self.write_config()
            reload_list();status.setText('Provider removed. No API key was touched.')
        def test_current():
            try:
                selected=next((p for p in self.provider_profiles if listing.currentItem() and p.label==listing.currentItem().data(Qt.UserRole)),None)
                if selected and selected.kind=='zerothink':
                    from zerothink_link import request,load_token
                    data=request('/api_cli.php',{'action':'me'},load_token())
                    status.setText('Account connected. Vault providers configured: '+', '.join(k for k,v in data.get('user',{}).get('provider_keys',{}).items() if v));return
                profile=ProviderProfile(label_edit.text(),url_edit.text(),model_edit.text(),env_edit.text())
                url=profile.base_url.rstrip('/')+'/models';headers={}
                if profile.api_key_env and os.environ.get(profile.api_key_env):headers['Authorization']='Bearer '+os.environ[profile.api_key_env]
                request=urllib.request.Request(url,headers=headers);status.setText('Testing endpoint…');dialog.repaint()
                with urllib.request.urlopen(request,timeout=8) as response:status.setText('Endpoint responded HTTP '+str(response.status)+'.')
            except Exception as exc:status.setText('Endpoint test failed: '+str(exc))
        listing.currentRowChanged.connect(lambda _:load_selected());reload_list()
        if listing.count():listing.setCurrentRow(0)
        actions=QHBoxLayout();self.button('Add / save',save_current,actions,True);self.button('Test endpoint',test_current,actions);self.button('Remove',remove_current,actions);self.button('Close',dialog.accept,actions);layout.addLayout(actions)
        dialog.exec()

    def link_zerothink(self):
        if self.busy:return
        from zerothink_link import link_dialog
        profile=link_dialog(self)
        if profile:
            self.provider_profiles=[p for p in self.provider_profiles if p.label!=profile.label]+[profile]
            save_provider_profiles(PROVIDERS,self.provider_profiles)
            self.config['active_provider']=profile.label;self.write_config();self.route.setCurrentIndex(4)
            self.status.setText('ZeroThink linked · vault provider selected')

    def connections_dialog(self):
        if self.busy:return
        dialog=QDialog(self);dialog.setWindowTitle('SSH connections');dialog.resize(760,500)
        layout=QVBoxLayout(dialog)
        intro=QLabel('Use an OpenSSH host alias from your normal SSH config or agent. Private keys and passwords stay outside TalkToAi Code.')
        intro.setWordWrap(True);intro.setObjectName('muted');layout.addWidget(intro)
        row=QHBoxLayout(); listing=QListWidget(); form=QVBoxLayout(); row.addWidget(listing,1); row.addLayout(form,2);layout.addLayout(row,1)
        label_edit=QLineEdit();label_edit.setPlaceholderText('Friendly name, e.g. AMD server')
        alias_edit=QLineEdit();alias_edit.setPlaceholderText('OpenSSH alias, e.g. amd-box')
        path_edit=QLineEdit();path_edit.setPlaceholderText('Optional remote project folder, e.g. ~/games/my-game')
        for title,edit in [('Name',label_edit),('SSH alias',alias_edit),('Remote folder',path_edit)]:
            form.addWidget(QLabel(title));form.addWidget(edit)
        form.addStretch()
        allow=QCheckBox('Allow the agent to run commands on the active SSH host')
        allow.setChecked(self.config.get('approval_policy')=='auto_remote' and bool(self.config.get('remote_enabled')))
        allow.setToolTip('This is separate from local Act mode. Keep it off when you only want to open a terminal or test the connection.')
        form.addWidget(allow)
        status=QLabel('');status.setWordWrap(True);status.setObjectName('muted');form.addWidget(status)
        def reload_list():
            listing.clear()
            for p in self.ssh_profiles:
                item=QListWidgetItem(p.label+'  ·  '+p.alias)
                item.setData(Qt.UserRole,p.alias);listing.addItem(item)
        def load_selected():
            item=listing.currentItem()
            if not item:return
            p=next((x for x in self.ssh_profiles if x.alias==item.data(Qt.UserRole)),None)
            if p:
                label_edit.setText(p.label);alias_edit.setText(p.alias);path_edit.setText(p.remote_path)
        def save_current():
            try:
                profile=SSHProfile(label_edit.text(),alias_edit.text(),path_edit.text())
                old_alias=listing.currentItem().data(Qt.UserRole) if listing.currentItem() else None
                self.ssh_profiles=[p for p in self.ssh_profiles if p.alias not in {old_alias,profile.alias}]
                self.ssh_profiles.append(profile);save_profiles(CONNECTIONS,self.ssh_profiles)
                self.config['active_ssh_alias']=profile.alias;self.config['active_ssh_path']=profile.remote_path
                self.config['remote_enabled']=True;self.config['remote_pilot']=allow.isChecked();self.config['approval_policy']='auto_remote' if allow.isChecked() else 'ask_remote'
                self.write_config();reload_list();listing.setCurrentRow(len(self.ssh_profiles)-1);self.refresh_connection_label()
                status.setText('Saved. Test the connection before using it for agent commands.')
            except Exception as exc:status.setText(str(exc))
        def remove_current():
            item=listing.currentItem()
            if not item:return
            alias=item.data(Qt.UserRole);self.ssh_profiles=[p for p in self.ssh_profiles if p.alias!=alias];save_profiles(CONNECTIONS,self.ssh_profiles)
            if self.config.get('active_ssh_alias')==alias:self.config['active_ssh_alias']='';self.config['remote_enabled']=False;self.config['remote_pilot']=False;self.write_config()
            reload_list();self.refresh_connection_label();status.setText('Connection removed from TalkToAi Code. SSH config was not changed.')
        def test_current():
            try:
                p=SSHProfile(label_edit.text(),alias_edit.text(),path_edit.text());status.setText('Testing SSH…');dialog.repaint();status.setText(SSHSession(p).test())
            except Exception as exc:status.setText('SSH test failed: '+str(exc))
        def terminal_current():
            try:SSHSession(SSHProfile(label_edit.text(),alias_edit.text(),path_edit.text())).open_terminal();status.setText('Opened an SSH terminal.')
            except Exception as exc:status.setText(str(exc))
        listing.currentRowChanged.connect(lambda _:load_selected())
        reload_list()
        if listing.count():listing.setCurrentRow(0)
        actions=QHBoxLayout()
        self.button('Add / save',save_current,actions,True);self.button('Test connection',test_current,actions);self.button('Open terminal',terminal_current,actions);self.button('Remove',remove_current,actions);self.button('Close',dialog.accept,actions)
        layout.addLayout(actions);dialog.exec();self.refresh_connection_label()

    def write_config(self):
        tmp=STATE/'config.json.tmp';tmp.write_text(json.dumps(self.config,indent=2),encoding='utf-8');tmp.replace(STATE/'config.json')

    def updates_dialog(self):
        from update_dialog import UpdateDialog
        if not getattr(self, '_updates', None):self._updates=UpdateDialog(self, STATE)
        self._updates.show();self._updates.raise_()

    def startup_link_path(self):
        return Path(os.environ.get('APPDATA', str(STATE))) / 'Microsoft/Windows/Start Menu/Programs/Startup/TalkToAi Code.lnk'

    def set_start_with_windows(self, enabled):
        link=self.startup_link_path()
        if enabled:
            link.parent.mkdir(parents=True, exist_ok=True)
            if getattr(sys, 'frozen', False):
                target=str(Path(sys.executable)); args='--tray'
            else:
                target=sys.executable; args=f'"{SOURCE / "studio.py"}" --tray'
            work=str(Path(target).parent)
            script=f'''$s=New-Object -ComObject WScript.Shell;$l=$s.CreateShortcut('{link}');$l.TargetPath='{target}';$l.Arguments='{args}';$l.WorkingDirectory='{work}';$l.Description='Start TalkToAi Code in the Windows notification area';$l.Save()'''
            subprocess.run(['powershell.exe','-NoProfile','-Command',script],check=True,creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            link.unlink(missing_ok=True)
        self.config['start_with_windows']=bool(enabled);self.write_config()

    def faq_dialog(self):
        dialog=QDialog(self);dialog.setWindowTitle('TalkToAi Code · FAQ / How to');dialog.resize(820,650)
        layout=QVBoxLayout(dialog)
        text=QTextBrowser();text.setOpenExternalLinks(True);text.setMarkdown('''# TalkToAi Code quick guide

## Start a coding task

1. Click **Open project** or type `open score arena`.
2. Choose **Auto** for the tested AMD/local route.
3. Choose **Act** when you want edits, commands, tests or game launches. Choose **Plan** for read-only investigation.
4. Describe the outcome, not a list of guessed commands. For example: `Inspect this Godot project, add a pause menu, run the import check, and capture evidence.`

## Steer a running task

While the agent is working, the Send button becomes **Steer**. Type a correction such as `Keep the existing art and only change the input code`, then press Enter. The current turn is stopped and the new instruction continues from the saved task history.

## Use the desktop

The left sidebar shows **Desktop tools · ON** when the current-user workspace is enabled. In Act mode the agent can list, read, write and run PowerShell commands under your signed-in Windows profile. Commands use your normal Windows account and are not an operating-system sandbox. Credential/private configuration files remain excluded from file reads and writes.

Try `check my desktop for server logins` for a non-secret inventory, or `open desktop` when you want to work in the Desktop folder as a project.

## Use SSH / AMD

Remote Pilot is ready for the configured **AMD OpenZero server**. Select **Act** and say: `Use my AMD server: inspect ~/my-project, fix the failing test, run it, and report evidence.` It verifies the saved SSH alias first, inspects the remote project read-only, then works on the requested task. You do not need to open Connections for the configured profile. **Open connections** remains available if you want to add or change a metadata-only alias; it never reads password/key contents.

## Models and API providers

**Auto** uses the measured coding route. **Local** uses the PC Ollama model. **AMD** uses the SSH-tunnelled server Ollama model. **API** is optional: add an OpenAI-compatible endpoint from **API providers** and store only the environment-variable name for its key. TalkToAi Code does not know whether an external provider is free, so check its terms yourself.

## Games and evidence

Use `run tests`, `launch game`, `take a screenshot`, `map project`, `show git changes`, or the Game tab. Godot import results, screenshots, task reports and tool output appear in the workspace tabs and remain attached to the task.

## Browser testing and research

In Act mode ask: `Use the browser to open http://localhost:3000, test the Start button, report page errors and save a screenshot.` The agent uses a separate Edge session, reads page structure, clicks visible text and fills labelled fields. The browser closes after each turn. Screenshots are saved to Evidence. When you choose **Local**, the installed Qwen3.5 model can inspect the next screenshot; AMD is faster for code/tool work but is tools-only, so it verifies through page and accessibility text instead.

## Computer use on Windows

In **Act** mode with **Desktop / user access** and **PC Pilot** enabled, simply state the outcome: `Open my game, test the main menu, take evidence screenshots, and report what works.` PC Pilot operates its own Windows tools: it lists windows, inspects the correct app, clicks/fills/selects controls, waits for transitions, and verifies the result after each input. You do not need to manually call tools or click through its normal workflow. **Stop** terminates the computer worker; a delivered input is not undone. Each computer call has a 20-second limit. Please do not use the mouse while the agent is operating an app.

This is an original open-source-based integration, not Codex's proprietary skill. Accessibility-based actions work without a vision model. Local Qwen3.5 can additionally inspect the immediately following screenshot; custom game canvases and inaccessible controls may still expose little useful information. Password fields are excluded from the control listing. It cannot bypass Windows permissions, a locked desktop, logins, passwords, security prompts, payments or final external submissions.

## Subagents for coding and games

Ask: `Use a reviewer to inspect the combat code and a test planner to identify missing tests, then fix the confirmed issues.` The main agent can delegate focused local-project questions to reviewer, investigator or test_planner workers. Each gets a separate conversation context and can inspect project files. Findings return to the main conversation's tool activity; the main agent handles changes and tests. Workers run sequentially on the selected model, with at most two workers per turn and five model steps each. Stop cancels the active worker too. Workers cannot edit, run commands, operate the desktop, access SSH, or create more workers. They use additional model inference; Local/AMD uses your own runtime, while an optional API route follows that provider's pricing.

## Find commands quickly

Press **Ctrl+K** for searchable actions: project tools, models, API providers, exports, task rename and conversation branching. **F1** opens this guide. Search tasks in the sidebar. A conversation branch shares the same project files; it does not create a Git worktree.

## If something is slow

Use Auto or AMD, stop a task, or steer it into a smaller request. The AMD route has been faster in measured coding acceptance. Large local models are slow on this CPU; model choices shows disk sizes and tested alternatives.
''');layout.addWidget(text,1)
        self.button('Close',dialog.accept,layout);dialog.exec()

    def settings(self):
        if self.busy:return
        dialog=QDialog(self);dialog.setWindowTitle('TalkToAi Code settings');dialog.resize(620,420)
        layout=QVBoxLayout(dialog)
        intro=QLabel('Control the defaults that make the agent feel automatic while keeping remote access explicit.')
        intro.setWordWrap(True);intro.setObjectName('muted');layout.addWidget(intro)
        form=QVBoxLayout();layout.addLayout(form)
        policy=QComboBox();policy.addItem('Remote agent commands off','ask_remote');policy.addItem('Allow agent remote commands','auto_remote');policy.addItem('Plan / read-only default','plan')
        policy.setCurrentIndex(max(0,policy.findData(self.config.get('approval_policy','ask_remote'))))
        form.addWidget(QLabel('Permission default'));form.addWidget(policy)
        access=QComboBox();access.addItem('Project files only','project');access.addItem('Desktop / signed-in user tools','full_user')
        access.setCurrentIndex(max(0,access.findData(self.config.get('access_mode','full_user'))))
        access.setToolTip('Desktop mode allows the agent to list/read/write user-profile files and run PowerShell as this Windows account. Credential files remain excluded from file tools.')
        form.addWidget(QLabel('Workspace access'));form.addWidget(access)
        pilot=QCheckBox('PC Pilot: automatically operate accessible Windows app controls for requested tasks')
        pilot.setChecked(bool(self.config.get('pc_pilot',True)))
        pilot.setToolTip('In Act mode, the agent carries out its own observe → act → verify loop. It does not bypass sign-in, passwords, security prompts, payments or final external submissions.')
        form.addWidget(pilot)
        remote_pilot=QCheckBox('Remote Pilot: automatically use the configured SSH server for requested AMD / server tasks')
        remote_pilot.setChecked(bool(self.config.get('remote_pilot',True)) and bool(self.config.get('remote_enabled')))
        remote_pilot.setToolTip('The agent verifies the configured SSH alias and remote project before commands. Authentication stays in OpenSSH; the app does not read keys, passwords or server API configuration.')
        form.addWidget(remote_pilot)
        auto=QCheckBox('Automatically include project map/search tools for coding requests');auto.setChecked(bool(self.config.get('auto_context',True)));form.addWidget(auto)
        activity=QCheckBox('Show tool activity in the Tools panel');activity.setChecked(bool(self.config.get('show_tool_activity',True)));form.addWidget(activity)
        startup=QCheckBox('Start TalkToAi Code with Windows (minimized to the notification area)')
        startup.setChecked(bool(self.config.get('start_with_windows',False)))
        startup.setToolTip('This creates a per-user Startup shortcut. It does not run as administrator and can be disabled here or from the Startup folder.')
        form.addWidget(startup)
        form.addWidget(QLabel('Say “use my AMD server” in Act mode; no Connections step is needed for the configured profile.'))
        status=QLabel('Current active SSH: '+(self.config.get('active_ssh_alias') or 'none'));status.setObjectName('muted');form.addWidget(status);form.addStretch()
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()==QDialog.Accepted:
            self.config['approval_policy']=policy.currentData();self.config['auto_context']=auto.isChecked();self.config['show_tool_activity']=activity.isChecked()
            self.config['access_mode']=access.currentData();self.config['pc_pilot']=pilot.isChecked();self.config['remote_pilot']=remote_pilot.isChecked();self.config['remote_enabled']=remote_pilot.isChecked() or self.config.get('remote_enabled',False)
            try:self.set_start_with_windows(startup.isChecked())
            except Exception as exc:self.error('Could not update Windows startup: '+str(exc))
            if remote_pilot.isChecked() and policy.currentData()!='plan':self.config['approval_policy']='auto_remote'
            if self.config['approval_policy']=='plan':self.mode.setCurrentText('Plan')
            self.write_config();self.refresh_connection_label();self.refresh_access_label();self.status.setText('Settings saved')

    def models_dialog(self):
        import shutil
        dialog=QDialog(self);dialog.setWindowTitle('Model choices & storage');dialog.resize(720,440)
        layout=QVBoxLayout(dialog)
        free=shutil.disk_usage(HOME).free/(1024**3)
        layout.addWidget(QLabel(f'{free:.1f} GiB free on this drive · downloads are optional'))
        info=QTextBrowser();info.setOpenExternalLinks(True)
        info.setMarkdown('''## Choose a model for your hardware

**AMD Qwen3-Coder 30B-A3B** — default coding route. This is a 30B total / 3B active MoE model and stays on your AMD server. TalkToAi Code checks the server inventory before every task.

**Qwen3.8 27B** — the larger local option for this PC. Select **Local · Qwen3.8 27B** and TalkToAi Code will run `ollama pull smtek/Qwen3.8-27B` once if it is missing. It is a large download and needs substantial RAM; the first run can be slow.

**Qwen3.5 4B · about 3.4 GB** — compact local option with tool and image support. Suitable for trials on this PC; check local test results before expecting AMD performance. [Model details](https://ollama.com/library/qwen3.5:4b)

**Qwen3.5 9B · about 6.6 GB** — optional larger local alternative with tool and image support. More memory and CPU work; not speed-tested here. [Model details](https://ollama.com/library/qwen3.5:9b)

**Qwen3 4B · about 2.5 GB** — small tool-capable alternative. Older generation; test results vary by task. [Model details](https://ollama.com/library/qwen3:4b)

**Phi-4-mini 3.8B · about 2.5 GB** — optional Microsoft model with function calling. A different model family to compare on small tasks; not installed or benchmarked here. [Model details](https://ollama.com/library/phi4-mini)

**Granite 4 micro 3.4B · about 2.1 GB** — optional IBM tool-capable model for smaller tasks. Not installed or benchmarked here. [Model details](https://ollama.com/library/granite4:micro)

The compact model is intentionally kept as the weak-CPU fallback. TalkToAi Code never pulls a large model merely by opening this panel: downloads happen only when you choose that route and send a task, with progress shown in the status line.
''');layout.addWidget(info)
        row=QHBoxLayout();self.button('Use installed local model…',self.select_installed_model,row);self.button('Close',dialog.accept,row);layout.addLayout(row)
        dialog.exec()

    def select_installed_model(self):
        if self.busy:return
        try:
            from routing import inventory
            names=inventory(11434)
            if not names:raise ValueError('No local models available.')
            name,ok=QInputDialog.getItem(self,'Installed local model','Choose a downloaded model:',names,editable=False)
            if ok:
                self.config['local_model']=name;(HOME/'config.json').write_text(json.dumps(self.config,indent=2),encoding='utf-8');self.route.setCurrentIndex(1);self.health()
        except Exception as exc:self.error(exc)

    def export_task(self):
        folder=Path(self.task['project'])/'.talktoai-code/reports';folder.mkdir(parents=True,exist_ok=True)
        path=folder/(self.task['id']+'.md')
        lines=['# '+self.task['title'],'','Project: '+self.task['project'],'']
        for message in self.task['messages']:
            if message['role'] in ('user','assistant') and message.get('content'):
                lines+=['## '+message['role'].title(),'',message['content'],'']
        lines+=['## File changes','']
        lines += ['- '+change['path']+(' (restored)' if change.get('restored') else '') for change in self.task['changes']]
        lines+=['','## Tool evidence','']
        for item in self.task.get('activity',[]):lines+=['```text',item.replace('```','` ` `'),'```','']
        path.write_text('\n'.join(lines),encoding='utf-8');self.status.setText('Saved task report: '+str(path))
        self.task.setdefault('artifacts',[]).append({'artifact':str(path),'type':'report'});self.refresh_artifacts();self.persist()

    def cline(self):
        path=Path(os.environ.get('LOCALAPPDATA',''))/'Cline'/'cline-app.exe'
        if path.exists():os.startfile(path)
        else:self.error('Cline is not installed at the expected path.')

    def game_command(self,smoke):
        if self.busy:return
        root=Path(self.task['project']);godot,_=ProjectTools(root).engine_paths()
        if not (root/'project.godot').exists() or not godot:self.error('Select a Godot project and install Godot on PATH or set TALKTOAI_GODOT.');return
        import subprocess
        if not smoke:subprocess.Popen([str(godot),'--path',str(root)],cwd=root);self.status.setText('Game launched');return
        self.prompt.setPlainText('Run a Godot headless import check for this project using this executable and report errors: '+str(godot)+' . Use --headless --editor --quit --path .');self.mode.setCurrentText('Act');self.send()

    def blender(self):
        path=Path('C:/Program Files/Blender Foundation/Blender 5.2/blender.exe')
        if path.exists():os.startfile(path)
        else:self.error('Blender 5.2 was not found at its configured path.')

    def capture(self):
        folder=Path(self.task['project'])/'.talktoai-code/screenshots';folder.mkdir(parents=True,exist_ok=True)
        path=folder/(uuid.uuid4().hex+'.png')
        if self.grab().save(str(path)):self.output.appendPlainText('App screenshot: '+str(path));self.status.setText('Screenshot saved')

    def capture_desktop(self):
        folder=Path(self.task['project'])/'.talktoai-code/screenshots';folder.mkdir(parents=True,exist_ok=True)
        path=folder/(uuid.uuid4().hex+'.png')
        self.status.setText('Capturing primary display in 3 seconds · switch to the game')
        def snap():
            if QApplication.primaryScreen().grabWindow(0).save(str(path)):
                self.output.appendPlainText('Desktop screenshot: '+str(path));self.status.setText('Desktop screenshot saved')
        QTimer.singleShot(3000,snap)

    def error(self,exc):QMessageBox.warning(self,'TalkToAi Code',str(exc))

    def closeEvent(self,event):
        if self.task:self.task['draft']=self.prompt.toPlainText()
        self.persist()
        if self.tray and not self.allow_quit:
            self.hide();event.ignore();return
        self.cancel.set();event.accept()

if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    if '--self-test' in sys.argv:
        from release_checks import smoke
        sys.exit(0 if smoke(HOME/'packaged-release-check.json') else 1)
    app=QApplication(sys.argv); app.setApplicationName('TalkToAi Code'); app.setStyleSheet(STYLE)
    instance=None
    if '--preview' not in sys.argv:
        # Keep this release independently launchable while an older tray build
        # is still running. This avoids force-closing a potentially active task.
        instance_name='TalkToAiCode.Studio.v6.'+os.environ.get('USERNAME','user')
        client=QLocalSocket();client.connectToServer(instance_name)
        if client.waitForConnected(300):
            client.write(b'show');client.flush();client.waitForBytesWritten(300);sys.exit(0)
        instance=QLocalServer()
        if not instance.listen(instance_name):
            QLocalServer.removeServer(instance_name)
            if not instance.listen(instance_name):raise RuntimeError('Cannot create desktop instance channel.')
    window=Studio()
    if '--tray' not in sys.argv or not window.tray:window.show()
    if instance:
        def activate():
            client=instance.nextPendingConnection();window.show_from_tray();client.close();client.deleteLater()
        instance.newConnection.connect(activate)
    if '--preview' in sys.argv:
        def preview():
            window.grab().save(str(HOME/'studio-preview.png'));window.allow_quit=True
            if window.tray:window.tray.hide()
            app.quit()
        QTimer.singleShot(1800,preview)
    sys.exit(app.exec())
