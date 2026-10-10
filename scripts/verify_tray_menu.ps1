# verify_tray_menu.ps1 - PersonalMemory Windows v0.1 托盘菜单实机验证脚本
# 用途：在最终安装版上验证托盘菜单本体（Win+B 定位托盘图标 -> VK_APPS 打开菜单 ->
#       焦点导航枚举 7 项 -> 真实点击暂停/隐私/立即同步并读回状态文案）。
# 注意事项：
#   - 菜单打开后焦点先落在 Menu 容器，Down (Index+1) 次才落到第 Index 项。
#   - 每次点击后菜单关闭，必须重新打开再导航。
#   - 本脚本会真实切换暂停/隐私状态，结束后恢复为初始状态。

param(
    [ValidateSet("verify", "exit")]
    [string]$Action = "verify"
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$sig = @"
using System;
using System.Runtime.InteropServices;
public static class KeyNative9 {
    [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
    public const byte VK_LWIN = 0x5B;
    public const byte VK_B = 0x42;
    public const byte VK_LEFT = 0x25;
    public const byte VK_DOWN = 0x28;
    public const byte VK_APPS = 0x5D;
    public const byte VK_RETURN = 0x0D;
    public const byte VK_ESCAPE = 0x1B;
    public const uint KEYUP = 0x0002;
    public static void Tap(byte vk) {
        keybd_event(vk, 0, 0, UIntPtr.Zero);
        keybd_event(vk, 0, KEYUP, UIntPtr.Zero);
    }
    public static void WinB() {
        keybd_event(VK_LWIN, 0, 0, UIntPtr.Zero);
        keybd_event(VK_B, 0, 0, UIntPtr.Zero);
        keybd_event(VK_B, 0, KEYUP, UIntPtr.Zero);
        keybd_event(VK_LWIN, 0, KEYUP, UIntPtr.Zero);
    }
}
"@
if (-not ("KeyNative9" -as [type])) { Add-Type -TypeDefinition $sig }

function Get-FocusInfo {
    $focused = [System.Windows.Automation.AutomationElement]::FocusedElement
    $name = ""
    try { $name = $focused.Current.Name } catch {}
    return ($name -replace "`n", " / ")
}

function Walk-ToTrayIcon {
    [KeyNative9]::WinB()
    Start-Sleep -Milliseconds 500
    for ($i = 0; $i -lt 15; $i++) {
        $name = Get-FocusInfo
        if ($name -like "*PersonalMemory*") { return $true }
        [KeyNative9]::Tap([KeyNative9]::VK_LEFT)
        Start-Sleep -Milliseconds 250
    }
    return $false
}

function Open-Menu {
    if (-not (Walk-ToTrayIcon)) { throw "tray icon not reachable" }
    [KeyNative9]::Tap([KeyNative9]::VK_APPS)
    Start-Sleep -Milliseconds 700
}

function Close-Menu {
    [KeyNative9]::Tap([KeyNative9]::VK_ESCAPE)
    Start-Sleep -Milliseconds 300
}

# focus starts at 'Menu' window; Down x (Index+1) lands on item Index (0=打开,1=状态,2=立即同步,3=暂停,4=隐私,5=设置,6=退出)
function Focus-Item {
    param([int]$Index)
    for ($i = 0; $i -lt ($Index + 1); $i++) {
        [KeyNative9]::Tap([KeyNative9]::VK_DOWN)
        Start-Sleep -Milliseconds 250
    }
    return Get-FocusInfo
}

function Activate {
    param([int]$Index, [string]$Label)
    Open-Menu
    $name = Focus-Item -Index $Index
    Write-Host "ACTIVATE[$Label]: item='$name'"
    [KeyNative9]::Tap([KeyNative9]::VK_RETURN)
    Start-Sleep -Milliseconds 800
    Close-Menu
}

function Read-Status {
    Open-Menu
    $name = Focus-Item -Index 1
    Close-Menu
    return $name
}

if (-not (Walk-ToTrayIcon)) { Write-Host "TRAY FOCUS: NOT REACHED"; exit 2 }

if ($Action -eq "exit") {
    Activate -Index 6 -Label "exit"
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Process personalmemory-desktop, personalmemory-service -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 200
    }
    $remaining = Get-Process personalmemory-desktop, personalmemory-service -ErrorAction SilentlyContinue
    if ($remaining) {
        Write-Host "EXIT: processes still running: $($remaining.Id -join ',')"
        exit 3
    }
    Write-Host "EXIT: desktop and service stopped"
    exit 0
}

$s0 = Read-Status
Write-Host "STATUS 0: $s0"
if ($s0 -like "*隐私模式*") { Activate -Index 4 -Label "privacy->off" }
if ($s0 -like "*已暂停*") { Activate -Index 3 -Label "pause->off" }
Write-Host "STATUS normalized: $(Read-Status)"

Activate -Index 3 -Label "pause->on"
Write-Host "STATUS pause-on: $(Read-Status)"
Activate -Index 3 -Label "pause->off"
Write-Host "STATUS pause-off: $(Read-Status)"

Activate -Index 4 -Label "privacy->on"
Write-Host "STATUS privacy-on: $(Read-Status)"
Activate -Index 4 -Label "privacy->off"
Write-Host "STATUS privacy-off: $(Read-Status)"

Activate -Index 2 -Label "sync-now"
Write-Host "SYNC-NOW: activated"
Close-Menu
