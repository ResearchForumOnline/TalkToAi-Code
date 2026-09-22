$ErrorActionPreference = 'Stop'
$studioRoot = Split-Path -Parent $PSCommandPath
$sampleData = @()
foreach ($relative in @('project.godot', 'main.gd', 'main.gd.uid', 'main.tscn', 'tests/score_test.gd')) {
    $destination = if ($relative.StartsWith('tests/')) { 'samples/score-arena/tests' } else { 'samples/score-arena' }
    $sampleData += '--add-data', "$studioRoot\samples\score-arena\${relative};$destination"
}
python -m PyInstaller --noconfirm --noconsole --name TalkToAiCode --collect-all playwright --collect-submodules pywinauto --exclude-module numpy --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --distpath "$studioRoot\dist-studio-v6" --workpath "$studioRoot\build-studio-v6" --specpath "$studioRoot\build-studio-v6" @sampleData "$studioRoot\studio.py"
if ($LASTEXITCODE -ne 0) { throw 'Studio packaging failed.' }
