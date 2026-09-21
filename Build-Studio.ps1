$ErrorActionPreference = 'Stop'
$studioRoot = Split-Path -Parent $PSCommandPath
python -m PyInstaller --noconfirm --noconsole --name TalkToAiCode --collect-all playwright --collect-submodules pywinauto --exclude-module numpy --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --distpath "$studioRoot\dist-studio-v6" --workpath "$studioRoot\build-studio-v6" --specpath "$studioRoot\build-studio-v6" "$studioRoot\studio.py"
if ($LASTEXITCODE -ne 0) { throw 'Studio packaging failed.' }
