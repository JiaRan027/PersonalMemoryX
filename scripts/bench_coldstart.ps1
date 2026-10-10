$ErrorActionPreference = "Stop"

$desktopExe = Join-Path $env:LOCALAPPDATA "PersonalMemory\personalmemory-desktop.exe"
if (-not (Test-Path -LiteralPath $desktopExe -PathType Leaf)) {
    throw "Desktop executable not found: $desktopExe"
}

Get-Process -Name "personalmemory-desktop", "personalmemory-service" -ErrorAction SilentlyContinue |
    Stop-Process -Force
Start-Sleep -Milliseconds 800

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$proc = Start-Process -FilePath $desktopExe -PassThru

$windowMs = $null
$title = ""
$deadline = [DateTime]::UtcNow.AddSeconds(10)
while ([DateTime]::UtcNow -lt $deadline) {
    $proc.Refresh()
    if ($proc.MainWindowHandle -ne 0) {
        $windowMs = [math]::Round($sw.Elapsed.TotalMilliseconds, 1)
        $title = $proc.MainWindowTitle
        break
    }
    Start-Sleep -Milliseconds 50
}

$serviceMs = $null
$servicePid = $null
$deadline = [DateTime]::UtcNow.AddSeconds(10)
while ([DateTime]::UtcNow -lt $deadline) {
    $svc = Get-Process -Name "personalmemory-service" -ErrorAction SilentlyContinue |
        Where-Object { $_.StartTime -ge $proc.StartTime } | Select-Object -First 1
    if ($svc) {
        $conn = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $_.OwningProcess -eq $svc.Id }
        if ($conn) {
            $serviceMs = [math]::Round($sw.Elapsed.TotalMilliseconds, 1)
            $servicePid = $svc.Id
            break
        }
    }
    Start-Sleep -Milliseconds 50
}

Write-Host "desktop pid: $($proc.Id)"
Write-Host "main window handle at: $windowMs ms (title='$title')"
Write-Host "service listening at: $serviceMs ms (pid=$servicePid)"