param(
    [string]$InstallerPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $InstallerPath) {
    $packageJson = Get-Content -LiteralPath (Join-Path $projectRoot "desktop\package.json") -Raw | ConvertFrom-Json
    $InstallerPath = Join-Path $projectRoot "desktop\src-tauri\target\release\bundle\nsis\PersonalMemory_$($packageJson.version)_x64-setup.exe"
}

$installer = Get-Item -LiteralPath $InstallerPath
if (-not $installer.Name.EndsWith(".exe", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Checksum target must be a Windows installer: $($installer.FullName)"
}

$stream = [System.IO.File]::OpenRead($installer.FullName)
$sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    $hashBytes = $sha256.ComputeHash($stream)
    $hash = -join ($hashBytes | ForEach-Object { $_.ToString("x2") })
}
finally {
    $sha256.Dispose()
    $stream.Dispose()
}
$checksumPath = "$($installer.FullName).sha256"
$line = "$hash *$($installer.Name)`n"
[System.IO.File]::WriteAllText(
    $checksumPath,
    $line,
    [System.Text.UTF8Encoding]::new($false)
)

Write-Output "SHA-256: $hash"
Write-Output "Checksum file: $checksumPath"
