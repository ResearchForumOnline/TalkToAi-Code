"""Manage explicit, user-owned executable integrations."""
import json

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QPlainTextEdit,
                               QPushButton, QVBoxLayout)

from mcp_plugins import validate_plugins


FILESYSTEM_EXAMPLE = {
    'filesystem': {
        'command': 'node',
        'args': ['/absolute/path/to/installed/mcp-server-filesystem/index.js',
                 '/absolute/path/to/allowed/workspace'],
        'enabled': False,
        'pass_env': [],
    },
}


def integration_dialog(parent, configured):
    window = QDialog(parent)
    window.setWindowTitle('Integrations')
    window.resize(690, 520)
    layout = QVBoxLayout(window)
    hint = QLabel('Blender and Project Data are built in. Add an installed MCP stdio server below, '
                  'then set enabled to true. Enabled plugins run in Act with your account’s permissions. '
                  'Use pass_env to name environment variables the server needs. Keep secret values out of this editor.')
    hint.setWordWrap(True)
    layout.addWidget(hint)
    editor = QPlainTextEdit(json.dumps(configured, indent=2, ensure_ascii=False))
    editor.setObjectName('mcp_plugin_config')
    layout.addWidget(editor, 1)
    example = QPushButton('Insert filesystem server example')
    layout.addWidget(example)
    status = QLabel('');status.setWordWrap(True)
    layout.addWidget(status)
    actions = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
    layout.addWidget(actions)
    result = None

    def example_clicked():
        try:
            config = validate_plugins(json.loads(editor.toPlainText() or '{}'))
            config.setdefault('filesystem', FILESYSTEM_EXAMPLE['filesystem'])
            editor.setPlainText(json.dumps(config, indent=2, ensure_ascii=False))
            status.setText('Replace the example paths with your installed server and allowed workspace.')
        except (TypeError, ValueError) as exc:
            status.setText(str(exc))

    def save():
        nonlocal result
        try:
            if len(editor.toPlainText()) > 128000:
                raise ValueError('Plugin settings exceed 128,000 characters.')
            result = validate_plugins(json.loads(editor.toPlainText() or '{}'))
        except (TypeError, ValueError) as exc:
            status.setText(str(exc))
            return
        window.accept()

    example.clicked.connect(example_clicked)
    actions.accepted.connect(save)
    actions.rejected.connect(window.reject)
    window.exec()
    return result
