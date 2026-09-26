$ErrorActionPreference='Stop'
$appRoot=Split-Path -Parent $PSCommandPath
Set-Location -LiteralPath $appRoot
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Install Python 3.12 with the Python launcher, then run setup again.' }
& py -3.12 -m venv (Join-Path $appRoot '.venv')
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 virtual environment creation failed.' }
$appPython=Join-Path $appRoot '.venv\Scripts\python.exe'
& $appPython -m pip install --disable-pip-version-check -r (Join-Path $appRoot 'requirements-runtime.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your internet connection and available disk space.' }
$appConfig=Join-Path $appRoot 'config.json'
if (-not (Test-Path -LiteralPath $appConfig)) {
    $projectRoot=[Environment]::GetFolderPath('MyDocuments')
    if (-not (Test-Path -LiteralPath $projectRoot)) { $projectRoot=$appRoot }
    @{project=$projectRoot;local_model='qwen3.5:4b';local_large_model='qwen3:4b';server_model='qwen3-coder:30b';approval_policy='ask_remote';remote_enabled=$false;remote_pilot=$false;pc_pilot=$true;access_mode='project';auto_context=$true;show_tool_activity=$true} | ConvertTo-Json | Set-Content -LiteralPath $appConfig -Encoding UTF8
}
$shortcutShell=New-Object -ComObject WScript.Shell
$shortcutPath=Join-Path ([Environment]::GetFolderPath('Desktop')) 'TalkToAi Code.lnk'
$appShortcut=$shortcutShell.CreateShortcut($shortcutPath)
$appShortcut.TargetPath=Join-Path $appRoot '.venv\Scripts\pythonw.exe'
$appShortcut.Arguments='"'+(Join-Path $appRoot 'studio.py')+'"'
$appShortcut.WorkingDirectory=$appRoot
$appShortcut.Description='TalkToAi Code native desktop assistant'
$appShortcut.Save()
Write-Host 'Installed. Open TalkToAi Code from your desktop. Choose a project and local model, or link ZeroThink.'
