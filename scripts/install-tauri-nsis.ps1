param(
    [string]$TauriToolsDirectory = (Join-Path $env:LOCALAPPDATA "tauri")
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Keep this bootstrap aligned with the pinned Tauri CLI 2.12.1 bundler source:
# crates/tauri-bundler/src/bundle/windows/nsis/mod.rs.
$nsisUrl = "https://github.com/tauri-apps/binary-releases/releases/download/nsis-3.11/nsis-3.11.zip"
$nsisSha1 = "EF7FF767E5CBD9EDD22ADD3A32C9B8F4500BB10D"
$tauriUtilsUrl = "https://github.com/tauri-apps/nsis-tauri-utils/releases/download/nsis_tauri_utils-v0.5.3/nsis_tauri_utils.dll"
$tauriUtilsSha1 = "75197FEE3C6A814FE035788D1C34EAD39349B860"
$nsisRoot = Join-Path $TauriToolsDirectory "NSIS"

$requiredFiles = @(
    "COPYING",
    "makensis.exe",
    "Bin/makensis.exe",
    "Stubs/lzma-x86-unicode",
    "Stubs/lzma_solid-x86-unicode",
    "Plugins/x86-unicode/additional/nsis_tauri_utils.dll",
    "Include/MUI2.nsh",
    "Include/FileFunc.nsh",
    "Include/x64.nsh",
    "Include/nsDialogs.nsh",
    "Include/WinMessages.nsh",
    "Include/Win/COM.nsh",
    "Include/Win/Propkey.nsh",
    "Include/Win/RestartManager.nsh"
)

if (Test-Path -LiteralPath $nsisRoot) {
    $missing = @($requiredFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $nsisRoot $_) -PathType Leaf) })
    if ($missing.Count -gt 0) {
        throw "Existing Tauri NSIS cache is incomplete; refusing to replace it. Missing: $($missing -join ', ')"
    }
    $existingVersion = ((& (Join-Path $nsisRoot "makensis.exe") /VERSION | Out-String).Trim() -replace '^v', '')
    if ($LASTEXITCODE -ne 0 -or $existingVersion -ne "3.11") {
        throw "Existing Tauri NSIS cache is not the expected 3.11 toolchain (reported '$existingVersion')."
    }
    Write-Output "Tauri NSIS 3.11 toolchain already present: $nsisRoot"
    return
}

if (-not (Test-Path -LiteralPath $TauriToolsDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $TauriToolsDirectory -Force | Out-Null
}

$stagingRoot = Join-Path $TauriToolsDirectory ".nsis-bootstrap-$([Guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $stagingRoot | Out-Null

$archivePath = Join-Path $stagingRoot "nsis-3.11.zip"
$pluginPath = Join-Path $stagingRoot "nsis_tauri_utils.dll"
Invoke-WebRequest -Uri $nsisUrl -OutFile $archivePath
$actualNsisSha1 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA1).Hash
if ($actualNsisSha1 -ne $nsisSha1) {
    throw "Tauri NSIS archive checksum mismatch: expected $nsisSha1, got $actualNsisSha1"
}

Expand-Archive -LiteralPath $archivePath -DestinationPath $stagingRoot
$extractedRoot = Join-Path $stagingRoot "nsis-3.11"
if (-not (Test-Path -LiteralPath (Join-Path $extractedRoot "COPYING") -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $extractedRoot "makensis.exe") -PathType Leaf)) {
    throw "Verified Tauri NSIS archive did not contain COPYING and makensis.exe"
}

Invoke-WebRequest -Uri $tauriUtilsUrl -OutFile $pluginPath
$actualPluginSha1 = (Get-FileHash -LiteralPath $pluginPath -Algorithm SHA1).Hash
if ($actualPluginSha1 -ne $tauriUtilsSha1) {
    throw "Tauri NSIS utility plug-in checksum mismatch: expected $tauriUtilsSha1, got $actualPluginSha1"
}

$pluginDirectory = Join-Path $extractedRoot "Plugins/x86-unicode/additional"
New-Item -ItemType Directory -Path $pluginDirectory -Force | Out-Null
Copy-Item -LiteralPath $pluginPath -Destination (Join-Path $pluginDirectory "nsis_tauri_utils.dll")

$missing = @($requiredFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $extractedRoot $_) -PathType Leaf) })
if ($missing.Count -gt 0) {
    throw "Verified Tauri NSIS package is missing required files: $($missing -join ', ')"
}

$version = ((& (Join-Path $extractedRoot "makensis.exe") /VERSION | Out-String).Trim() -replace '^v', '')
if ($LASTEXITCODE -ne 0 -or $version -ne "3.11") {
    throw "Tauri NSIS reported unexpected version '$version'; expected 3.11."
}

Move-Item -LiteralPath $extractedRoot -Destination $nsisRoot
Write-Output "Installed Tauri NSIS $version after checksum verification: $nsisRoot"
