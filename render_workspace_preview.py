"""Render the actual Qt UI using a disposable demo profile, without model calls."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import argparse
from contextlib import ExitStack
from pathlib import Path
import tempfile
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
import studio


def render(path, width=1440, height=900):
    app=QApplication.instance() or QApplication([])
    # Qt's offscreen Windows platform needs explicit font registration.
    fonts=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
    for name in ('segoeui.ttf','segoeuib.ttf','seguisb.ttf','seguisym.ttf','consola.ttf'):
        if (fonts/name).is_file():QFontDatabase.addApplicationFont(str(fonts/name))
    app.setStyleSheet(studio.STYLE)
    with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
        root=Path(folder)
        for name,value in [('HOME',root),('STATE',root),('SESSION',root/'studio.json'),('CONNECTIONS',root/'connections.json'),('PROVIDERS',root/'providers.json')]:
            stack.enter_context(patch.object(studio,name,value))
        stack.enter_context(patch.object(studio.Studio,'health'))
        stack.enter_context(patch.object(studio.Studio,'install_tray'))
        window=studio.Studio();window.resize(width,height)
        window.task['title']='Game project · UI demonstration'
        window.task['project']=str(root)
        window.title.setText(window.task['title']);window.project_label.setText('Demo project')
        window.task['messages']=[
            {'role':'user','content':'Add a pause menu to my game, check the project, and capture evidence.'},
            {'role':'assistant','content':'**UI demonstration — not an executed model response**\n\nA game-development task can bring together project inspection, edits, checks and evidence.\n\n1. Inspect the engine and project conventions.\n2. Implement the requested feature.\n3. Run the available project checks.\n4. Test the affected flow and report what was actually verified.\n\nKeep reusable decisions in **Project memory**, use **Steer** during a run, and review file changes in the workspace.'}]
        window.refresh_tasks();window.render();window.right.setCurrentIndex(3)
        window.health_label.setText('Choose your local or self-hosted model')
        window.connection_label.setText('Your own SSH connections')
        window.use_starter('Build a game feature')
        window.show();app.processEvents()
        target=Path(path);target.parent.mkdir(parents=True,exist_ok=True)
        if not window.grab().save(str(target)):raise RuntimeError('Preview rendering failed')
        window.allow_quit=True;window.close();window.deleteLater();app.processEvents()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('path');parser.add_argument('--width',type=int,default=1440);parser.add_argument('--height',type=int,default=900)
    args=parser.parse_args();render(args.path,args.width,args.height)
