param(
    [string]$Python = "python",
    [string]$VisualStudioPath
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$specPath = Join-Path $projectRoot "service\personalmemory-service.spec"
$mcpSpecPath = Join-Path $projectRoot "service\personalmemory-mcp.spec"
$hookManifestPath = Join-Path $projectRoot "desktop\native\personalmemory-hook\Cargo.toml"
$codexAcceptancePath = Join-Path $projectRoot "scripts\accept_codex_hook.py"
$hookBuildExe = Join-Path $projectRoot `
    "desktop\native\personalmemory-hook\target\release\personalmemory-hook.exe"
$distPath = Join-Path $projectRoot "desktop\src-tauri\binaries"
$workPath = Join-Path $projectRoot "build\pyinstaller"
$serviceDir = Join-Path $distPath "personalmemory-service"
$serviceExe = Join-Path $serviceDir "personalmemory-service.exe"
$mcpDir = Join-Path $distPath "personalmemory-mcp"
$mcpExe = Join-Path $mcpDir "personalmemory-mcp-host.exe"
$hookDir = Join-Path $distPath "personalmemory-hook"
$hookExe = Join-Path $hookDir "personalmemory-hook.exe"

if (-not (Test-Path -LiteralPath $specPath -PathType Leaf)) {
    throw "PyInstaller spec is missing: $specPath"
}
if (-not (Test-Path -LiteralPath $mcpSpecPath -PathType Leaf)) {
    throw "MCP PyInstaller spec is missing: $mcpSpecPath"
}
if (-not (Test-Path -LiteralPath $hookManifestPath -PathType Leaf)) {
    throw "Native Hook Cargo manifest is missing: $hookManifestPath"
}

& $Python -m PyInstaller --clean --noconfirm `
    --distpath $distPath `
    --workpath $workPath `
    $specPath
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $serviceExe -PathType Leaf)) {
    throw "Sidecar executable is missing after build: $serviceExe"
}

& $Python -m PyInstaller --clean --noconfirm `
    --distpath $distPath `
    --workpath $workPath `
    $mcpSpecPath
if ($LASTEXITCODE -ne 0) {
    throw "MCP PyInstaller failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $mcpExe -PathType Leaf)) {
    throw "MCP executable is missing after build: $mcpExe"
}

if (-not $VisualStudioPath) {
    $VisualStudioPath = $env:VSINSTALLDIR
}
if (-not $VisualStudioPath) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path -LiteralPath $vswhere -PathType Leaf) {
        $VisualStudioPath = (& $vswhere -latest -products '*' `
            -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
            -property installationPath | Select-Object -First 1).Trim()
    }
}
if (-not $VisualStudioPath -or -not (Test-Path -LiteralPath $VisualStudioPath -PathType Container)) {
    throw "A Visual Studio 2026 installation with the MSVC x64 tools is required to source redistributable runtimes"
}

$vcRedistRoot = Join-Path $VisualStudioPath "VC\Redist\MSVC"
$vcRedist = Get-ChildItem -LiteralPath $vcRedistRoot -Directory -ErrorAction Stop |
    Where-Object {
        $_.Name -match '^\d+\.\d+\.\d+$' -and
        (Test-Path -LiteralPath (Join-Path $_.FullName "x64\Microsoft.VC145.CRT") -PathType Container)
    } |
    Sort-Object -Property @{ Expression = { [version]$_.Name } } -Descending |
    Select-Object -First 1
if (-not $vcRedist) {
    throw "Could not find the Visual Studio 2026 x64 Microsoft.VC145.CRT REDIST directory under $vcRedistRoot"
}
$vcCrtSource = Join-Path $vcRedist.FullName "x64\Microsoft.VC145.CRT"
$vcRuntimeNames = @(
    "concrt140.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll",
    "vccorlib140.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "vcruntime140_threads.dll"
)
foreach ($sidecarName in @("personalmemory-service", "personalmemory-mcp")) {
    $internalDir = Join-Path (Join-Path $distPath $sidecarName) "_internal"
    if (-not (Test-Path -LiteralPath $internalDir -PathType Container)) {
        throw "PyInstaller runtime directory is missing: $internalDir"
    }

    Get-ChildItem -LiteralPath $internalDir -Recurse -File |
        Where-Object { $_.Name -match '^(api-ms-win-crt-.*|ucrtbase\.dll)$' } |
        Remove-Item -Force

    foreach ($runtimeName in $vcRuntimeNames) {
        $source = Join-Path $vcCrtSource $runtimeName
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "Required Visual Studio REDIST file is missing: $source"
        }
        Copy-Item -LiteralPath $source -Destination (Join-Path $internalDir $runtimeName) -Force
        $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
        $copyHash = (Get-FileHash -LiteralPath (Join-Path $internalDir $runtimeName) -Algorithm SHA256).Hash
        if ($sourceHash -ne $copyHash) {
            throw "Visual Studio REDIST file changed while staging: $runtimeName"
        }
    }

    $unapprovedVcFiles = Get-ChildItem -LiteralPath $internalDir -Recurse -File |
        Where-Object {
            $_.Name -match '^(vcruntime|msvcp|concrt|vccorlib)' -and
            $_.Name -notin $vcRuntimeNames
        }
    if ($unapprovedVcFiles) {
        $names = ($unapprovedVcFiles | ForEach-Object { $_.Name }) -join ", "
        throw "Unreviewed Visual C++ runtime files remain in ${sidecarName}: $names"
    }
}
Write-Host "VC runtime source: Visual Studio REDIST $($vcRedist.Name) / Microsoft.VC145.CRT (copied unchanged)"
Write-Host "App-local UCRT copies removed; supported Windows 10/11 provides the UCRT system component"

. (Join-Path $PSScriptRoot "rust-env.ps1")
& $PersonalMemoryCargo @PersonalMemoryCargoArgs test `
    --locked `
    --manifest-path $hookManifestPath
if ($LASTEXITCODE -ne 0) {
    throw "Native Hook tests failed with exit code $LASTEXITCODE"
}
& $PersonalMemoryCargo @PersonalMemoryCargoArgs build `
    --release `
    --locked `
    --manifest-path $hookManifestPath
if ($LASTEXITCODE -ne 0) {
    throw "Native Hook build failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $hookBuildExe -PathType Leaf)) {
    throw "Native Hook executable is missing after build: $hookBuildExe"
}

$resolvedDistPath = [System.IO.Path]::GetFullPath($distPath)
$resolvedHookDir = [System.IO.Path]::GetFullPath($hookDir)
if (-not $resolvedHookDir.StartsWith(
    $resolvedDistPath + [System.IO.Path]::DirectorySeparatorChar,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Refusing to replace Hook directory outside bundled binaries: $resolvedHookDir"
}
if (Test-Path -LiteralPath $resolvedHookDir) {
    Remove-Item -LiteralPath $resolvedHookDir -Recurse -Force
}
New-Item -ItemType Directory -Path $resolvedHookDir | Out-Null
Copy-Item -LiteralPath $hookBuildExe -Destination $hookExe

$hookStartInfo = [System.Diagnostics.ProcessStartInfo]::new()
$hookStartInfo.FileName = $hookExe
$hookStartInfo.WorkingDirectory = $hookDir
$hookStartInfo.UseShellExecute = $false
$hookStartInfo.CreateNoWindow = $true
$hookStartInfo.RedirectStandardInput = $true
$hookStartInfo.RedirectStandardOutput = $true
$hookStartInfo.RedirectStandardError = $true
$hookStartInfo.Arguments = "hook --connector-id connector-package-smoke"
$hookProcess = [System.Diagnostics.Process]::Start($hookStartInfo)
$hookProcess.StandardInput.WriteLine("{}")
$hookProcess.StandardInput.Close()
if (-not $hookProcess.WaitForExit(5000)) {
    $hookProcess.Kill()
    $hookProcess.WaitForExit()
    throw "Packaged hook command did not fail open within 5 seconds"
}
$hookStdout = $hookProcess.StandardOutput.ReadToEnd()
$hookStderr = $hookProcess.StandardError.ReadToEnd()
if ($hookProcess.ExitCode -ne 0 -or $hookStdout -ne "" -or $hookStderr -ne "") {
    throw "Packaged hook command was not silent and fail-open"
}

$listener = [System.Net.Sockets.TcpListener]::new(
    [System.Net.IPAddress]::Loopback,
    0
)
$listener.Start()
$port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()

$token = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
$smokeDataDir = Join-Path ([System.IO.Path]::GetTempPath()) `
    ("personalmemory-sidecar-smoke-" + [guid]::NewGuid().ToString("N"))
[System.IO.Directory]::CreateDirectory($smokeDataDir) | Out-Null

$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $serviceExe
$startInfo.WorkingDirectory = $serviceDir
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.EnvironmentVariables["PERSONALMEMORY_DATA_DIR"] = $smokeDataDir
$startInfo.EnvironmentVariables["PERSONALMEMORY_HOST"] = "127.0.0.1"
$startInfo.EnvironmentVariables["PERSONALMEMORY_PORT"] = $port.ToString()
$startInfo.EnvironmentVariables["PERSONALMEMORY_API_TOKEN"] = $token
$process = [System.Diagnostics.Process]::Start($startInfo)

try {
    $headers = @{ Authorization = "Bearer $token" }
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    $ready = $false
    while ([DateTime]::UtcNow -lt $deadline -and -not $process.HasExited) {
        try {
            $health = Invoke-RestMethod `
                -Uri "http://127.0.0.1:$port/api/v1/health" `
                -Headers $headers `
                -Method Get `
                -TimeoutSec 1
            if ($health.status -eq "ready") {
                $ready = $true
                break
            }
        }
        catch {
            Start-Sleep -Milliseconds 100
        }
    }
    if (-not $ready) {
        throw "Packaged sidecar did not become ready"
    }

    Invoke-RestMethod `
        -Uri "http://127.0.0.1:$port/api/v1/system/shutdown" `
        -Headers $headers `
        -Method Post `
        -TimeoutSec 2 | Out-Null
    if (-not $process.WaitForExit(5000)) {
        throw "Packaged sidecar did not exit after authenticated shutdown"
    }
    if ($process.ExitCode -ne 0) {
        throw "Packaged sidecar exited with code $($process.ExitCode)"
    }
}
finally {
    if (-not $process.HasExited) {
        $process.Kill()
        $process.WaitForExit()
    }
    if (Test-Path -LiteralPath $smokeDataDir) {
        Remove-Item -LiteralPath $smokeDataDir -Recurse -Force
    }
}

Write-Host "Sidecar smoke passed: $serviceExe"
Write-Host "MCP sidecar build passed: $mcpExe"
Write-Host "Native Hook sidecar fail-open smoke passed: $hookExe"

& $Python $codexAcceptancePath `
    --service-executable $serviceExe `
    --hook-executable $hookExe
if ($LASTEXITCODE -ne 0) {
    throw "Packaged Codex Hook acceptance failed with exit code $LASTEXITCODE"
}
Write-Host "Packaged Codex selective-capture acceptance passed"
