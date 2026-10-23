[CmdletBinding()]
param([string]$BuildName = '8.6.0')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($BuildName -notmatch '^\d+\.\d+\.\d+(?:-[a-zA-Z0-9]+)?$') { throw 'BuildName must identify the source release.' }
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$work = Join-Path $root ('build-studio-store-' + $BuildName)
$destination = Join-Path $root 'dist-studio-store'
$payload = Join-Path $destination 'TalkToAiCode'
if (Test-Path -LiteralPath $payload) { throw 'Store payload already exists. Preserve and review it before building again.' }
$samples = @()
foreach ($relative in @('project.godot','main.gd','main.gd.uid','main.tscn','tests/score_test.gd')) {
    $folder = if ($relative.StartsWith('tests/')) { 'samples/score-arena/tests' } else { 'samples/score-arena' }
    $sampleSource = Join-Path $root ('samples\score-arena\' + $relative)
    $samples += '--add-data', ($sampleSource + ';' + $folder)
}
python -m PyInstaller --noconfirm --noconsole --name TalkToAiCode --collect-all playwright --collect-submodules pywinauto --hidden-import api_router --hidden-import attachment_ingest --exclude-module numpy --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --distpath $destination --workpath $work --specpath $work @samples (Join-Path $root 'studio.py')
if ($LASTEXITCODE -ne 0) { throw 'Store executable build failed.' }
python (Join-Path $root 'scripts\package-runtime-notices.py') $payload
if ($LASTEXITCODE -ne 0) { throw 'Store dependency notice packaging failed.' }
