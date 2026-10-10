param(
    [string]$ServiceExe = (Join-Path $env:LOCALAPPDATA "PersonalMemory\personalmemory-service\personalmemory-service.exe")
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ServiceExe -PathType Leaf)) {
    throw "Service executable not found: $ServiceExe"
}

$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()

$token = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
$dataDir = Join-Path ([System.IO.Path]::GetTempPath()) ("personalmemory-sync-bench-" + [guid]::NewGuid().ToString("N"))
[System.IO.Directory]::CreateDirectory($dataDir) | Out-Null
$sourceFile = Join-Path $dataDir "source.md"
[System.IO.File]::WriteAllText(
    $sourceFile,
    "# Personal Memory`n`n- durable experience asset one`n- decision log two`n",
    [System.Text.UTF8Encoding]::new($false)
)

$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $ServiceExe
$startInfo.WorkingDirectory = (Split-Path -Parent $ServiceExe)
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.EnvironmentVariables["PERSONALMEMORY_DATA_DIR"] = $dataDir
$startInfo.EnvironmentVariables["PERSONALMEMORY_HOST"] = "127.0.0.1"
$startInfo.EnvironmentVariables["PERSONALMEMORY_PORT"] = $port.ToString()
$startInfo.EnvironmentVariables["PERSONALMEMORY_API_TOKEN"] = $token
$process = [System.Diagnostics.Process]::Start($startInfo)

try {
    $headers = @{ Authorization = "Bearer $token" }
    $ready = $false
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
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
        throw "Sidecar did not become ready"
    }

    $connectorBody = @{
        profile_id = "generic_markdown_readonly"
        source_path = $sourceFile
        read_enabled = $true
        write_enabled = $false
        name = "bench-nochange"
        namespace = "general"
    } | ConvertTo-Json
    Invoke-RestMethod `
        -Uri "http://127.0.0.1:$port/api/v1/connectors" `
        -Headers $headers `
        -Method Post `
        -ContentType "application/json" `
        -Body $connectorBody `
        -TimeoutSec 5 | Out-Null

    $first = Invoke-RestMethod `
        -Uri "http://127.0.0.1:$port/api/v1/system/sync" `
        -Headers $headers `
        -Method Post `
        -TimeoutSec 10
    Write-Host "first sync (warm-up): ingested=$($first.ingested) projected=$($first.projected)"

    $latencies = @()
    $counts = @()
    for ($i = 0; $i -lt 20; $i++) {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $resp = Invoke-RestMethod `
            -Uri "http://127.0.0.1:$port/api/v1/system/sync" `
            -Headers $headers `
            -Method Post `
            -TimeoutSec 10
        $sw.Stop()
        $latencies += [math]::Round($sw.Elapsed.TotalMilliseconds, 3)
        $counts += "ingested=$($resp.ingested)"
    }

    $sorted = @($latencies | Sort-Object)
    $p50 = $sorted[[int][math]::Floor($sorted.Count * 0.50)]
    $p95 = $sorted[[int][math]::Floor($sorted.Count * 0.95)]
    $max = $sorted[-1]
    $uniqueCounts = @($counts | Sort-Object -Unique) -join ", "

    Write-Host "no-change sync samples: $($sorted.Count)"
    Write-Host "all ms: $($latencies -join ', ')"
    Write-Host "P50: $p50 ms"
    Write-Host "P95: $p95 ms"
    Write-Host "max: $max ms"
    Write-Host "per-run results: $uniqueCounts"

    Invoke-RestMethod `
        -Uri "http://127.0.0.1:$port/api/v1/system/shutdown" `
        -Headers $headers `
        -Method Post `
        -TimeoutSec 2 | Out-Null
    if (-not $process.WaitForExit(5000)) {
        throw "Sidecar did not exit after authenticated shutdown"
    }
}
finally {
    if (-not $process.HasExited) {
        $process.Kill()
        $process.WaitForExit()
    }
    if (Test-Path -LiteralPath $dataDir) {
        Remove-Item -LiteralPath $dataDir -Recurse -Force
    }
}