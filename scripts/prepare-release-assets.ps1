param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("alpha", "stable")]
    [string]$Channel,
    [string]$InstallerPath,
    [string]$ReleaseNotes,
    [string]$ReleaseNotesPath,
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$package = Get-Content -LiteralPath (Join-Path $projectRoot "desktop\package.json") -Raw | ConvertFrom-Json
$version = [string]$package.version
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Release version must be stable SemVer without build metadata: $version"
}

if (-not $InstallerPath) {
    $InstallerPath = Join-Path $projectRoot "desktop\src-tauri\target\release\bundle\nsis\PersonalMemory_${version}_x64-setup.exe"
}
$installer = Get-Item -LiteralPath $InstallerPath
if ($installer.Name -ne "PersonalMemory_${version}_x64-setup.exe") {
    throw "Installer name/version mismatch: $($installer.Name), expected PersonalMemory_${version}_x64-setup.exe"
}

$signaturePath = "$($installer.FullName).sig"
if (-not (Test-Path -LiteralPath $signaturePath -PathType Leaf)) {
    throw "Tauri updater signature is missing: $signaturePath"
}
$signature = (Get-Content -LiteralPath $signaturePath -Raw -Encoding ASCII).Trim()
if (-not $signature) {
    throw "Tauri updater signature is empty: $signaturePath"
}

$tauriConfig = Get-Content -LiteralPath (Join-Path $projectRoot "desktop\src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
$verifyExample = Join-Path $projectRoot "desktop\src-tauri\examples\verify_updater_artifact.rs"
if (-not (Test-Path -LiteralPath $verifyExample -PathType Leaf)) {
    throw "Updater signature acceptance helper is missing: $verifyExample"
}
Push-Location (Join-Path $projectRoot "desktop\src-tauri")
try {
    & cargo run --quiet --locked --example verify_updater_artifact -- `
        $installer.FullName $signaturePath $tauriConfig.plugins.updater.pubkey
    if ($LASTEXITCODE -ne 0) {
        throw "Updater signature acceptance failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$channelTag = "$Channel-channel"
$feedName = "latest-$Channel.json"
$downloadUrl = "https://github.com/JiaRan027/PersonalMemory/releases/download/$channelTag/$($installer.Name)"
$notes = "PersonalMemory $version ($Channel Alpha). See the repository README for installation, data handling, and verified limitations."
if ($ReleaseNotes) {
    $notes = $ReleaseNotes.Trim()
}
if ($ReleaseNotesPath) {
    $notes = (Get-Content -LiteralPath $ReleaseNotesPath -Raw -Encoding UTF8).Trim()
}
if (-not $notes) {
    throw "Release notes must not be empty"
}

$sha256 = (Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumPath = "$($installer.FullName).sha256"
[System.IO.File]::WriteAllText(
    $checksumPath,
    "$sha256 *$($installer.Name)`n",
    [System.Text.UTF8Encoding]::new($false)
)

$feed = [ordered]@{
    version = $version
    notes = $notes
    pub_date = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", [Globalization.CultureInfo]::InvariantCulture)
    platforms = [ordered]@{
        "windows-x86_64" = [ordered]@{
            signature = $signature
            url = $downloadUrl
        }
    }
}
$feedPath = Join-Path $installer.DirectoryName $feedName
$feedJson = $feed | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($feedPath, "$feedJson`n", [System.Text.UTF8Encoding]::new($false))

$parsed = Get-Content -LiteralPath $feedPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($parsed.version -ne $version -or
    $parsed.platforms.'windows-x86_64'.signature -ne $signature -or
    $parsed.platforms.'windows-x86_64'.url -ne $downloadUrl) {
    throw "Generated update feed failed its self-check"
}
$actualHash = (Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $sha256) {
    throw "Installer SHA-256 changed while preparing release assets"
}

if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $projectRoot "release-assets\$Channel"
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
foreach ($asset in @($installer.FullName, $signaturePath, $checksumPath, $feedPath)) {
    Copy-Item -LiteralPath $asset -Destination $OutputDirectory -Force
}

Write-Output "Release channel: $Channel"
Write-Output "Version: $version"
Write-Output "Installer SHA-256: $sha256"
Write-Output "Updater manifest: $feedPath"
Write-Output "Release assets: $OutputDirectory"
Write-Output "Signature source: $signaturePath"
