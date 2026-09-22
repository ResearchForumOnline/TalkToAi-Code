import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

VERSION = '0.1.4'
ASSET = 'TalkToAi-Code-0.1.3-Windows-Portable.zip'
URL = 'https://github.com/ResearchForumOnline/TalkToAi-Code/releases/download/v0.1.3-preview/' + ASSET

UNINSTALL = r'''param([switch]$RemoveUserData)
$root = Join-Path $env:LOCALAPPDATA 'TalkToAiCode'
$dataNames = @('studio.json','connections.json','providers.json','config.json')
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $env:LOCALAPPDATA ("TalkToAiCode-backup-" + $stamp)
$desktop = Join-Path $env:USERPROFILE 'Desktop\TalkToAi Code.lnk'
$start = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\TalkToAi Code.lnk'
Remove-Item -LiteralPath $desktop,$start -Force -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $root) {
  if (-not $RemoveUserData) {
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    foreach ($name in $dataNames) {
      $src = Join-Path $root $name
      if (Test-Path -LiteralPath $src) { Move-Item -LiteralPath $src -Destination $backup -Force }
    }
    Get-ChildItem -LiteralPath $root -Force | Where-Object { $_.Name -notin $dataNames } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "TalkToAi Code removed. User data was preserved in $backup"
  } else {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host 'TalkToAi Code and its local data were removed.'
  }
}
Read-Host 'Press Enter to close'
'''

def shortcut(path, target):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    script = f'''$s=New-Object -ComObject WScript.Shell;$l=$s.CreateShortcut('{path}');$l.TargetPath='{target}';$l.WorkingDirectory='{Path(target).parent}';$l.Description='TalkToAi Code native coding and game workspace';$l.Save()'''
    subprocess.run(['powershell.exe','-NoProfile','-Command',script], check=True, creationflags=subprocess.CREATE_NO_WINDOW)

def powershell_shortcut(path, target, arguments='', working_directory=None, description='TalkToAi Code'):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    working_directory = working_directory or Path(target).parent
    script = f'''$s=New-Object -ComObject WScript.Shell;$l=$s.CreateShortcut('{path}');$l.TargetPath='{target}';$l.Arguments='{arguments}';$l.WorkingDirectory='{working_directory}';$l.Description='{description}';$l.Save()'''
    subprocess.run(['powershell.exe','-NoProfile','-Command',script], check=True, creationflags=subprocess.CREATE_NO_WINDOW)

def install():
    root = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'TalkToAiCode'
    archive = Path(tempfile.gettempdir()) / ASSET
    status.set('Downloading the verified Windows runtime...')
    window.update_idletasks()
    urllib.request.urlretrieve(URL, archive)
    status.set('Installing and creating shortcuts...')
    window.update_idletasks()
    # Keep user data safe during upgrades. The portable payload is unpacked
    # into a staging folder and only runtime files are refreshed.
    staging = Path(tempfile.mkdtemp(prefix='talktoai-code-install-'))
    root.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z: z.extractall(staging)
    payload = staging / 'TalkToAiCode'
    target = root / 'TalkToAiCode'
    if target.exists(): shutil.rmtree(target)
    shutil.copytree(payload, target)
    (root / 'uninstall.ps1').write_text(UNINSTALL, encoding='utf-8')
    exe = root / 'TalkToAiCode' / 'TalkToAiCode.exe'
    desktop_link = Path(os.environ['USERPROFILE']) / 'Desktop' / 'TalkToAi Code.lnk'
    start_menu = Path(os.environ.get('APPDATA', root)) / 'Microsoft/Windows/Start Menu/Programs'
    shortcut(desktop_link, exe)
    shortcut(start_menu / 'TalkToAi Code.lnk', exe)
    # Create a safe, data-preserving uninstall shortcut.
    uninstall_link = Path(os.environ.get('APPDATA', root)) / 'Microsoft/Windows/Start Menu/Programs/Uninstall TalkToAi Code.lnk'
    powershell_shortcut(uninstall_link, 'powershell.exe',
        f'-NoProfile -ExecutionPolicy Bypass -File "{root / "uninstall.ps1"}"',
        root, 'Uninstall TalkToAi Code while preserving local data')
    archive.unlink(missing_ok=True)
    shutil.rmtree(staging, ignore_errors=True)
    status.set('Installed. Launching TalkToAi Code...')
    subprocess.Popen([str(exe)], cwd=str(exe.parent))
    window.after(700, window.destroy)

window = tk.Tk(); window.title(f'TalkToAi Code {VERSION} Installer'); window.geometry('470x170'); window.resizable(False, False)
tk.Label(window, text=f'TalkToAi Code {VERSION}', font=('Segoe UI', 18, 'bold')).pack(pady=(22, 4))
status = tk.StringVar(value='Ready to install the native Windows app.')
tk.Label(window, textvariable=status, wraplength=420).pack(pady=8)
button = ttk.Button(window, text='Install', command=lambda: (button.config(state='disabled'), install()))
button.pack(pady=8)
window.mainloop()
