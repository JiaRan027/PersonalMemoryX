param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$desktopDir = Join-Path $projectRoot "desktop"
$manifestPath = Join-Path $desktopDir "src-tauri\Cargo.toml"
$bundleDir = Join-Path $desktopDir "src-tauri\target\release\bundle\nsis"
$buildStarted = [DateTime]::UtcNow

Push-Location $projectRoot
try {
    & $Python -m ruff check service
    if ($LASTEXITCODE -ne 0) { throw "Ruff failed" }
    & $Python -m pytest service/tests -q
    if ($LASTEXITCODE -ne 0) { throw "Service tests failed" }

    npm --prefix desktop ci
    if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
    npm --prefix desktop run typecheck
    if ($LASTEXITCODE -ne 0) { throw "TypeScript check failed" }
    npm --prefix desktop run test:unit
    if ($LASTEXITCODE -ne 0) { throw "Desktop unit tests failed" }
    npm --prefix desktop run build
    if ($LASTEXITCODE -ne 0) { throw "Desktop build failed" }

    . (Join-Path $PSScriptRoot "rust-env.ps1")
    & $PersonalMemoryCargo test @PersonalMemoryCargoArgs `
        --manifest-path $manifestPath `
        --all-targets
    if ($LASTEXITCODE -ne 0) { throw "Rust tests failed" }

    & (Join-Path $PSScriptRoot "build-sidecar.ps1") -Python $Python

    Push-Location $desktopDir
    try {
        npm run tauri:build -- --bundles nsis
        if ($LASTEXITCODE -ne 0) { throw "Tauri NSIS build failed" }
    }
    finally {
        Pop-Location
    }
}
finally {
    Pop-Location
}

$installers = @(
    Get-ChildItem -LiteralPath $bundleDir -Filter "*.exe" -File |
        Where-Object { $_.LastWriteTimeUtc -ge $buildStarted.AddSeconds(-2) }
)
if ($installers.Count -ne 1) {
    throw "Expected one new NSIS installer, found $($installers.Count) in $bundleDir"
}

$installer = $installers[0]
$hash = Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256
$hashPath = "$($installer.FullName).sha256"
Set-Content -LiteralPath $hashPath -Encoding ascii -NoNewline `
    -Value "$($hash.Hash.ToLowerInvariant())  $($installer.Name)"

Write-Host "Installer: $($installer.FullName)"
Write-Host "SHA-256: $($hash.Hash.ToLowerInvariant())"
