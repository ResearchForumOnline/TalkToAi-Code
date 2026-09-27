"""Desktop explorer for reusable workflows and reproducible experiments."""
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QWidget, QLineEdit, QListWidget, QPlainTextEdit, QComboBox, QSplitter)

from project_playbooks import find_playbooks
from research_journal import read_experiments, compare_experiments


def choose_improvement(parent, goal='', iterations=2):
    dialog = QDialog(parent); dialog.setWindowTitle('Skynet Mode · checked candidate improvement'); dialog.resize(690, 480)
    layout = QVBoxLayout(dialog)
    label = QLabel('Create a separate code candidate, measure baseline checks, and retain a passing candidate when a later attempt fails. Review the resulting diff before applying it.'); label.setWordWrap(True); layout.addWidget(label)
    editor = QPlainTextEdit(); editor.setObjectName('improvement_goal'); editor.setPlaceholderText('Describe a concrete improvement and how you will judge it.'); editor.setPlainText(goal); layout.addWidget(editor, 1)
    row = QHBoxLayout(); row.addWidget(QLabel('Candidate iterations'))
    count = QComboBox(); count.setObjectName('improvement_iterations')
    for value in range(1, 6): count.addItem(str(value), value)
    count.setCurrentIndex(max(0, min(4, int(iterations) - 1))); row.addWidget(count); layout.addLayout(row)
    note = QLabel('Each iteration uses model inference and project commands. Existing tests and check configuration are frozen for comparison. A SKYNET-EVALUATOR.json contract also requires a better recorded score; without one, selection uses passing checks.'); note.setWordWrap(True); layout.addWidget(note)
    row = QHBoxLayout(); cancel = QPushButton('Cancel'); cancel.clicked.connect(dialog.reject); row.addWidget(cancel)
    start = QPushButton('Start candidate run'); start.setObjectName('accent'); start.clicked.connect(dialog.accept); row.addWidget(start); layout.addLayout(row)
    if dialog.exec() != QDialog.Accepted: return None
    return editor.toPlainText().strip(), count.currentData()


class ProjectWorkbench(QDialog):
    def __init__(self, project, task=None, parent=None):
        super().__init__(parent)
        self.project = Path(project).resolve()
        self.task = task or {}
        self.selected_prompt = ''
        self.playbooks = []
        self.experiments = []
        self.setWindowTitle('Project workbench')
        self.resize(920, 680)
        layout = QVBoxLayout(self)
        heading = QLabel('Project workbench'); heading.setStyleSheet('font-size:24px;font-weight:600;')
        layout.addWidget(heading)
        self.project_label = QLabel(str(self.project)); self.project_label.setTextFormat(Qt.PlainText); self.project_label.setWordWrap(True)
        layout.addWidget(self.project_label)
        self.tabs = QTabWidget(); layout.addWidget(self.tabs, 1)
        self._overview_tab()
        self._playbooks_tab()
        self._research_tab()
        self.notice = QLabel('Local project records. Loading a record does not run a command.'); self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        close = QPushButton('Close'); close.clicked.connect(self.reject); layout.addWidget(close)
        self.refresh_playbooks()
        self.refresh_experiments()

    def _button(self, text, callback, layout):
        button = QPushButton(text); button.clicked.connect(callback); layout.addWidget(button); return button

    def _text_view(self):
        text = QPlainTextEdit(); text.setReadOnly(True); text.setLineWrapMode(QPlainTextEdit.WidgetWidth); return text

    def _overview_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        view = self._text_view()
        checkpoint = self.task.get('goal_checkpoint') or {}
        changes = self.task.get('workspace_changes') or checkpoint.get('workspace_changes') or {}
        lines = ['CURRENT TASK', self.task.get('title', 'New task'), '',
                 'Saved state: ' + str(checkpoint.get('state', 'No checkpoint yet')),
                 'Observed source changes: ' + str(changes.get('count', 0))]
        if changes.get('reason'): lines.append(str(changes['reason']))
        if checkpoint.get('blockers'): lines += ['', 'BLOCKERS'] + [str(x) for x in checkpoint['blockers']]
        if changes.get('paths'): lines += ['', 'CHANGED FILES'] + [str(x) for x in changes['paths'][:80]]
        goal = self.task.get('task_goal') or {}
        if goal.get('objective'): lines += ['', 'OBJECTIVE', str(goal['objective'])]
        lines += ['', 'USE THIS WORKBENCH',
                  'Playbooks save a useful project workflow with evidence files so the next task can find it.',
                  'Experiments compare recorded baseline and candidate measurements, including the conditions and evidence.',
                  'Skynet Mode creates a separate code candidate and checks it before you review its diff.']
        view.setPlainText('\n'.join(lines)); layout.addWidget(view, 1)
        self._button('Prepare to continue unfinished work', lambda: self.choose_prompt(
            'Read the saved checkpoint, current project files and Git changes. Continue the unfinished objective. '
            'Verify current evidence before repeating commands, and report the remaining blockers.'), layout)
        self.tabs.addTab(page, 'Task')

    def _playbooks_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        row = QHBoxLayout(); self.search = QLineEdit(); self.search.setPlaceholderText('Find a saved workflow, e.g. Godot import')
        self.search.returnPressed.connect(self.refresh_playbooks); row.addWidget(self.search, 1)
        self._button('Find', self.refresh_playbooks, row); layout.addLayout(row)
        split = QSplitter(); self.playbook_list = QListWidget(); self.playbook_detail = self._text_view()
        split.addWidget(self.playbook_list); split.addWidget(self.playbook_detail); split.setStretchFactor(1, 2)
        self.playbook_list.currentRowChanged.connect(self.show_playbook); layout.addWidget(split, 1)
        row = QHBoxLayout(); self._button('Use selected workflow', self.use_playbook, row)
        self._button('Ask agent to save this workflow', lambda: self.choose_prompt(
            'Review this task and its actual check results. If we established a useful repeatable workflow, '
            'save a project playbook with when to use it, concise steps, verification notes and non-sensitive evidence files. '
            'Describe missing evidence honestly. Do not save credentials or perform additional external actions.'), row)
        layout.addLayout(row); self.tabs.addTab(page, 'Playbooks')

    def refresh_playbooks(self):
        try:
            result = find_playbooks(self.project, self.search.text(), 20)
            self.playbooks = result['entries']; self.playbook_list.clear()
            for item in self.playbooks:
                self.playbook_list.addItem(item['title'] + '  ·  ' + item['evidence_status'].replace('_', ' '))
            if self.playbooks: self.playbook_list.setCurrentRow(0)
            else: self.playbook_detail.setPlainText('No matching project playbooks yet. After a successful workflow, ask the agent to save its steps and evidence here.')
        except (OSError, ValueError, TypeError) as exc:
            self.playbooks = []; self.playbook_list.clear(); self.playbook_detail.setPlainText(str(exc))

    def show_playbook(self, row):
        if not 0 <= row < len(self.playbooks): return
        item = self.playbooks[row]
        lines = [item['title'], '', str(item.get('when_to_use', '')), '', 'STEPS']
        lines += [f'{i + 1}. {step}' for i, step in enumerate(item['steps'])]
        lines += ['', 'VERIFICATION NOTES', str(item.get('verification', '')), '', 'EVIDENCE NOW']
        lines += [e['path'] + ' · ' + e['current_status'] for e in item['evidence']]
        lines += ['', item['notice']]; self.playbook_detail.setPlainText('\n'.join(lines))

    def use_playbook(self):
        row = self.playbook_list.currentRow()
        if not 0 <= row < len(self.playbooks): return
        item = self.playbooks[row]
        self.choose_prompt('Find the project playbook titled ' + json.dumps(item['title']) +
                           '. Check its current evidence and relevance to this task before following its steps. '
                           'Apply it only within the current request and report the verification result.')

    def _research_tab(self):
        page = QWidget(); layout = QVBoxLayout(page)
        intro = QLabel('Compare recorded measurements under the same conditions. A recorded metric is a reported result; evidence hashes check file consistency.')
        intro.setWordWrap(True); layout.addWidget(intro)
        row = QHBoxLayout(); self.baseline = QComboBox(); self.candidate = QComboBox()
        for label, combo in [('Baseline', self.baseline), ('Candidate', self.candidate)]:
            row.addWidget(QLabel(label)); row.addWidget(combo, 1)
            combo.currentIndexChanged.connect(self.update_metrics)
        layout.addLayout(row)
        row = QHBoxLayout(); self.metric_choice = QComboBox(); self.direction = QComboBox()
        self.direction.addItem('Lower is better', 'minimize'); self.direction.addItem('Higher is better', 'maximize')
        row.addWidget(self.metric_choice, 1); row.addWidget(self.direction)
        self._button('Compare', self.compare, row); self._button('Reload', self.refresh_experiments, row); layout.addLayout(row)
        self.research_detail = self._text_view(); layout.addWidget(self.research_detail, 1)
        self._button('Plan a measured experiment', lambda: self.choose_prompt(
            'Inspect this project and the experiment journal. Propose one falsifiable improvement to the current objective. '
            'Record a baseline, metric and direction, fixed controls, dataset or fixture, environment, seed if relevant, '
            'and finite compute budget. Run the baseline and a candidate using available project tools, record their '
            'actual outputs as evidence, and compare them. Keep unsupported findings explicitly unverified.'), layout)
        self.tabs.addTab(page, 'Experiments')

    def refresh_experiments(self):
        self.experiments = []
        try:
            data = json.loads(read_experiments(self.project, 20, True))
            self.experiments = data['entries']
            self.baseline.blockSignals(True); self.candidate.blockSignals(True)
            self.baseline.clear(); self.candidate.clear()
            for item in self.experiments:
                label = f"#{item.get('sequence', '?')} {item['hypothesis'][:65]}"
                self.baseline.addItem(label, item['id']); self.candidate.addItem(label, item['id'])
            if self.experiments: self.candidate.setCurrentIndex(len(self.experiments) - 1)
            self.baseline.blockSignals(False); self.candidate.blockSignals(False)
            self.update_metrics()
            if self.experiments:
                lines = []
                for item in self.experiments:
                    lines += [f"#{item.get('sequence', '?')} {item['hypothesis']}",
                              item.get('reported_result', ''), 'Metrics: ' + json.dumps(item.get('reported_metrics', {})),
                              'Evidence: ' + item.get('evidence_check', {}).get('status', 'not checked'), '']
                self.research_detail.setPlainText('\n'.join(lines))
            else: self.research_detail.setPlainText('No experiments recorded for this project yet. Plan a measured experiment to establish a baseline and compare a candidate.')
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.experiments = []; self.baseline.clear(); self.candidate.clear(); self.metric_choice.clear()
            self.research_detail.setPlainText(str(exc))
        finally:
            self.baseline.blockSignals(False); self.candidate.blockSignals(False)

    def update_metrics(self):
        if not hasattr(self, 'metric_choice'): return
        a = next((e for e in self.experiments if e['id'] == self.baseline.currentData()), {})
        b = next((e for e in self.experiments if e['id'] == self.candidate.currentData()), {})
        self.metric_choice.clear(); self.metric_choice.addItems(sorted(set(a.get('reported_metrics', {})) & set(b.get('reported_metrics', {}))))

    def compare(self):
        if not self.metric_choice.currentText():
            self.research_detail.setPlainText('Choose two records with a shared numeric metric.'); return
        try:
            result = json.loads(compare_experiments(self.project, self.baseline.currentData(), self.candidate.currentData(),
                                self.metric_choice.currentText(), self.direction.currentData(), True))
            lines = [result['metric'], '',
                     'Baseline reported: ' + str(result['baseline_reported']),
                     'Candidate reported: ' + str(result['candidate_reported']),
                     'Candidate minus baseline: ' + str(result['candidate_minus_baseline']),
                     'Direction wanted: ' + self.direction.currentText(),
                     'Reported numeric result: ' + ('improved' if result['reported_improvement'] else 'no improvement'),
                     '', 'COMPARISON', result['comparison_status'].replace('_', ' ')]
            if result.get('warnings'): lines += ['', 'LIMITATIONS'] + ['• ' + w for w in result['warnings']]
            lines += ['', 'EVIDENCE NOW']
            for name, check in result.get('evidence_checks', {}).items():
                lines.append(name.title() + ': ' + check['status'].replace('_', ' '))
                lines += ['  ' + e.get('path', 'invalid record') + ' · ' + e['status'] for e in check.get('files', [])]
            lines += ['', result['verification']]
            self.research_detail.setPlainText('\n'.join(lines))
        except (OSError, ValueError, TypeError) as exc:
            self.research_detail.setPlainText(str(exc))

    def choose_prompt(self, text):
        self.selected_prompt = text
        self.accept()
