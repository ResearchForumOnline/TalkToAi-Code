"""Opt-in Windows UIA acceptance against a disposable native test window.

Set TALKTOAI_NATIVE_ACCEPTANCE=1 to run; never drives an existing user window.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from computer_tools import ComputerSession


@unittest.skipUnless(os.name == 'nt' and os.environ.get('TALKTOAI_NATIVE_ACCEPTANCE') == '1',
                     'Native Windows test window is opt-in')
class NativeDesktopAcceptance(unittest.TestCase):
    def test_observe_fill_click_observe(self):
        with tempfile.TemporaryDirectory(prefix='TalkToAi-Control-Test-') as folder:
            script=Path(folder)/'fixture.ps1'
            script.write_text('''Add-Type -AssemblyName System.Windows.Forms
$form = New-Object System.Windows.Forms.Form
$form.Text = 'TalkToAi disposable control acceptance'
$form.Width = 520; $form.Height = 220
$inputBox = New-Object System.Windows.Forms.TextBox
$inputBox.AccessibleName = 'Acceptance player name'
$inputBox.Left = 20; $inputBox.Top = 20; $inputBox.Width = 250
$button = New-Object System.Windows.Forms.Button
$button.Text = 'Apply fixture'; $button.Left = 290; $button.Top = 20; $button.Width = 150
$label = New-Object System.Windows.Forms.Label
$label.Text = 'Fixture ready'; $label.Left = 20; $label.Top = 80; $label.Width = 440
$button.Add_Click({ $label.Text = 'Hello ' + $inputBox.Text })
$form.Controls.AddRange(@($inputBox,$button,$label))
[System.Windows.Forms.Application]::Run($form)
''',encoding='utf-8')
            fixture=subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script)],
                                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            session=ComputerSession(folder,threading.Event())
            try:
                target=None; deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    windows=json.loads(session.execute('windows'))
                    target=next((w for w in windows if w['title']=='TalkToAi disposable control acceptance'),None)
                    if target:break
                    time.sleep(.2)
                self.assertIsNotNone(target,'Disposable native window was not observable')
                observed=json.loads(session.execute('inspect',target['handle']))
                control=next(c for c in observed['controls'] if c['type']=='Edit')
                session.execute('fill',control['id'],'Builder')
                observed=json.loads(session.execute('inspect',target['handle']))
                button=next(c for c in observed['controls'] if c['name']=='Apply fixture')
                session.execute('click',button['id'])
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    observed=json.loads(session.execute('inspect',target['handle']))
                    if any(c['name']=='Hello Builder' for c in observed['controls']):break
                    time.sleep(.1)
                self.assertTrue(any(c['name']=='Hello Builder' for c in observed['controls']),str(observed['controls']))
            finally:
                session.close()
                if fixture.poll() is None:fixture.terminate()
                fixture.wait(timeout=10)


if __name__=='__main__':unittest.main()
