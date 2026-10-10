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
$dataDir = Join-Path ([System.IO.Path]::GetTempPath()) ("personalmemory-ingest-bench-" + [guid]::NewGuid().ToString("N"))
[System.IO.Directory]::CreateDirectory($dataDir) | Out-Null
$sourceFile = Join-Path $dataDir "source.md"
$utf8 = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($sourceFile, "# Personal Memory`n`n- baseline memory one`n", $utf8)
Start-Sleep -Milliseconds 300

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
        name = "bench-ingest"
        namespace = "general"
    } | ConvertTo-Json
    Invoke-RestMethod `
        -Uri "http://127.0.0.1:$port/api/v1/connectors" `
        -Headers $headers `
        -Method Post `
        -ContentType "application/json" `
        -Body $connectorBody `
        -TimeoutSec 5 | Out-Null

    Invoke-RestMethod `
        -Uri "http://127.0.0.1:$port/api/v1/system/sync" `
        -Headers $headers `
        -Method Post `
        -TimeoutSec 10 | Out-Null

    $count = 0
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ([DateTime]::UtcNow -lt $deadline) {
        $memories = Invoke-RestMethod `
            -Uri "http://127.0.0.1:$port/api/v1/memories" `
            -Headers $headers `
            -Method Get `
            -TimeoutSec 3
        $count = $memories.total
        if ($count -eq 1) { break }
        Start-Sleep -Milliseconds 50
    }
    if ($count -ne 1) {
        throw "Initial ingest failed: total=$count"
    }

    $latencies = @()
    for ($run = 0; $run -lt 5; $run++) {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        [System.IO.File]::WriteAllText($sourceFile, "# Personal Memory`n`n- baseline memory one`n- change marker run$run token$run`n", $utf8)
        $found = $false
        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        while ([DateTime]::UtcNow -lt $deadline) {
            $memories = Invoke-RestMethod `
                -Uri "http://127.0.0.1:$port/api/v1/memories" `
                -Headers $headers `
                -Method Get `
                -TimeoutSec 3
            if ($memories.total -ge (2 + $run)) {
                $found = $true
                break
            }
            Start-Sleep -Milliseconds 50
        }
        $sw.Stop()
        if (-not $found) {
            throw "Change run $run was not ingested in 10s"
        }
        $latencies += [math]::Round($sw.Elapsed.TotalMilliseconds, 1)
        Write-Host "run ${run}: change -> ingested in $($latencies[-1]) ms (total=$($memories.total))"
        Start-Sleep -Milliseconds 200
    }

    $sorted = @($latencies | Sort-Object)
    Write-Host "P50: $($sorted[[int][math]::Floor($sorted.Count * 0.50)]) ms"
    Write-Host "max: $($sorted[-1]) ms"

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