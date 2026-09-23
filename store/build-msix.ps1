[CmdletBinding()]
param(
    [string]$Version = '1.3.0.0',
    [string]$IdentityName = 'talktoai.TalkToAiCode-AICodingStudio',
    [string]$Publisher = 'CN=9B704446-6F62-4D52-954E-775436315422',
    [string]$PublisherDisplayName = 'talktoai',
    [string]$BuildLabel = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$payload = Join-Path $root 'dist-studio-store\TalkToAiCode'
$stage = Join-Path $root ("store-staging-" + $Version + $BuildLabel)
$outDir = Join-Path $root 'dist-store'
$output = Join-Path $outDir ("TalkToAi-Code-" + $Version + "-x64" + $BuildLabel + ".msix")
$makeappx = 'C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\makeappx.exe'

if ($IdentityName -ne 'talktoai.TalkToAiCode-AICodingStudio' -or
    $Publisher -ne 'CN=9B704446-6F62-4D52-954E-775436315422' -or
    $PublisherDisplayName -ne 'talktoai') {
    throw 'Package identity must match the exact Partner Center product identity.'
}
if ($BuildLabel -ne '' -and $BuildLabel -notmatch '^-[a-zA-Z0-9]+$') { throw 'BuildLabel must be empty or a short alphanumeric suffix beginning with a hyphen.' }
if ($Version -notmatch '^([1-9]\d*)\.(\d+)\.(\d+)\.0$') {
    throw 'Use a four-part Microsoft Store version with nonzero major and zero revision.'
}
foreach ($component in $Version.Split('.')) {
    if ([int]$component -gt 65535) { throw 'MSIX version component exceeds 65535.' }
}
if (-not (Test-Path -LiteralPath $makeappx -PathType Leaf)) { throw 'Windows SDK MakeAppx is missing.' }
if (-not (Test-Path -LiteralPath (Join-Path $payload 'TalkToAiCode.exe') -PathType Leaf)) {
    throw 'Rebuild the Store payload before packaging.'
}
if (Test-Path -LiteralPath $stage) { throw "Staging directory already exists; review it before another build: $stage" }
if (Test-Path -LiteralPath $output) { throw "Output package already exists; review it before another build: $output" }

New-Item -ItemType Directory -Path $stage, (Join-Path $stage 'Assets'), $outDir -Force | Out-Null
Copy-Item -Path (Join-Path $payload '*') -Destination $stage -Recurse -Force
foreach ($size in @(44,50,150)) {
    $source = Join-Path $root ("assets\store\icon-" + $size + ".png")
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing icon: $source" }
    Copy-Item -LiteralPath $source -Destination (Join-Path $stage ("Assets\icon-" + $size + ".png"))
}
Copy-Item -LiteralPath (Join-Path $root 'LICENSE') -Destination (Join-Path $stage 'LICENSE.txt')
Copy-Item -LiteralPath (Join-Path $root 'THIRD-PARTY-NOTICES.md') -Destination (Join-Path $stage 'THIRD-PARTY-NOTICES.md')

$manifest = @"
<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
         xmlns:uap10="http://schemas.microsoft.com/appx/manifest/uap/windows10/10"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
         IgnorableNamespaces="uap uap10 rescap">
  <Identity Name="$IdentityName" Version="$Version" Publisher="$Publisher" ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>TalkToAi Code - AI Coding Studio</DisplayName>
    <PublisherDisplayName>$PublisherDisplayName</PublisherDisplayName>
    <Description>Local-first coding and game-development AI workspace</Description>
    <Logo>Assets\icon-50.png</Logo>
  </Properties>
  <Resources><Resource Language="en-US" /></Resources>
  <Dependencies><TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.26100.0" /></Dependencies>
  <Capabilities><rescap:Capability Name="runFullTrust" /></Capabilities>
  <Applications>
    <Application Id="TalkToAiCode" Executable="TalkToAiCode.exe" uap10:RuntimeBehavior="packagedClassicApp" uap10:TrustLevel="mediumIL">
      <uap:VisualElements DisplayName="TalkToAi Code" Description="Coding and game development with your chosen AI" BackgroundColor="#081016" Square150x150Logo="Assets\icon-150.png" Square44x44Logo="Assets\icon-44.png" />
    </Application>
  </Applications>
</Package>
"@
[IO.File]::WriteAllText((Join-Path $stage 'AppxManifest.xml'), $manifest, [Text.UTF8Encoding]::new($false))
& $makeappx pack /d $stage /p $output /o
if ($LASTEXITCODE -ne 0) { throw "MakeAppx failed: $LASTEXITCODE" }
Get-Item -LiteralPath $output | Select-Object FullName,Length
Get-FileHash -LiteralPath $output -Algorithm SHA256 | Select-Object Hash
