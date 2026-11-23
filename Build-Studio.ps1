[CmdletBinding()]
param(
    [string]$OutputDirectory = '',
    [string]$BuildName = 'v6',
    [string]$AssetCollection = ''
)
$ErrorActionPreference = 'Stop'
$studioRoot = Split-Path -Parent $PSCommandPath
if ($BuildName -notmatch '^[A-Za-z0-9.-]+$') { throw 'Invalid build name.' }
$destination = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) } else { "$studioRoot\dist-studio-v6" }
$work = "$studioRoot\build-studio-$BuildName"
$sampleData = @()
foreach ($relative in @('project.godot', 'main.gd', 'main.gd.uid', 'main.tscn', 'tests/score_test.gd')) {
    $sampleDestination = if ($relative.StartsWith('tests/')) { 'samples/score-arena/tests' } else { 'samples/score-arena' }
    $sampleData += '--add-data', "$studioRoot\samples\score-arena\${relative};$sampleDestination"
}
python -m PyInstaller --noconfirm --noconsole --name TalkToAiCode --manifest "$studioRoot\store\TalkToAiCode.manifest" --collect-all playwright --collect-submodules pywinauto --exclude-module numpy --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --distpath $destination --workpath $work --specpath $work @sampleData "$studioRoot\studio.py"
if ($LASTEXITCODE -ne 0) { throw 'Studio packaging failed.' }
python "$studioRoot\scripts\package-project-python.py" (Join-Path $destination 'TalkToAiCode')
if ($LASTEXITCODE -ne 0) { throw 'Project Python packaging failed.' }
python "$studioRoot\scripts\package-runtime-notices.py" (Join-Path $destination 'TalkToAiCode')
if ($LASTEXITCODE -ne 0) { throw 'Runtime license notice packaging failed.' }
$assetArgs = if ($AssetCollection) { @('--source', $AssetCollection) } else { @() }
python "$studioRoot\scripts\package-game-assets.py" (Join-Path $destination 'TalkToAiCode') @assetArgs
if ($LASTEXITCODE -ne 0) { throw 'Game asset packaging failed.' }
