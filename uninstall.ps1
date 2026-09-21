param([switch]$RemoveUserData)

# Default behavior is reversible: remove the runtime and shortcuts, preserving
# local conversations, connections, providers, and settings in a timestamped backup.
$root = Join-Path $env:LOCALAPPDATA 'TalkToAiCode'
$dataNames = @('studio.json','connections.json','providers.json','config.json')
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $env:LOCALAPPDATA ("TalkToAiCode-backup-" + $stamp)
$desktop = Join-Path $env:USERPROFILE 'Desktop\TalkToAi Code.lnk'
$start = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\TalkToAi Code.lnk'
$uninstall = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Uninstall TalkToAi Code.lnk'
Remove-Item -LiteralPath $desktop,$start,$uninstall -Force -ErrorAction SilentlyContinue

if (Test-Path -LiteralPath $root) {
    if (-not $RemoveUserData) {
        New-Item -ItemType Directory -Path $backup -Force | Out-Null
        foreach ($name in $dataNames) {
            $src = Join-Path $root $name
            if (Test-Path -LiteralPath $src) { Move-Item -LiteralPath $src -Destination $backup -Force }
        }
        Get-ChildItem -LiteralPath $root -Force |
            Where-Object { $_.Name -notin $dataNames } |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "TalkToAi Code removed. User data was preserved in $backup"
    } else {
        Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host 'TalkToAi Code and its local data were removed.'
    }
}

Read-Host 'Press Enter to close'
