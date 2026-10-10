param(
    [string]$CargoXwin = $env:PERSONALMEMORY_CARGO_XWIN,
    [string]$LlvmBin = $env:PERSONALMEMORY_LLVM_BIN
)

$ErrorActionPreference = "Stop"

$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if (-not (Test-Path -LiteralPath (Join-Path $cargoBin "cargo.exe"))) {
    throw "Rust is missing from $cargoBin"
}

$env:PATH = "$cargoBin;$env:PATH"
$script:PersonalMemoryCargo = "cargo"
$script:PersonalMemoryCargoArgs = @()
$sysrootLib = Join-Path $env:LOCALAPPDATA "cargo-xwin\windows-msvc-sysroot\windows-msvc-sysroot\lib\x86_64-unknown-windows-msvc"

if (-not $CargoXwin) {
    $candidate = Join-Path $env:TEMP "cargo-xwin-v0.23.0\cargo-xwin.exe"
    if (Test-Path -LiteralPath $candidate) {
        $CargoXwin = $candidate
    }
}
if (-not $LlvmBin) {
    $candidate = Join-Path $env:LOCALAPPDATA "PersonalMemoryDevTools\clang+llvm-22.1.8-x86_64-pc-windows-msvc\bin"
    if (Test-Path -LiteralPath (Join-Path $candidate "clang.exe")) {
        $LlvmBin = $candidate
    }
}

if ($CargoXwin -or (Test-Path -LiteralPath (Join-Path $sysrootLib "kernel32.lib"))) {
    if ($CargoXwin -and -not (Test-Path -LiteralPath $CargoXwin)) {
        $CargoXwin = $null
    }
    if (-not $LlvmBin -or -not (Test-Path -LiteralPath (Join-Path $LlvmBin "clang.exe"))) {
        throw "LLVM toolchain is missing: $LlvmBin"
    }

    if (-not (Test-Path -LiteralPath (Join-Path $sysrootLib "kernel32.lib"))) {
        throw "cargo-xwin Windows SDK cache is missing: $sysrootLib"
    }

    $env:PATH = "$LlvmBin;$env:PATH"
    $env:LIB = $sysrootLib
    $env:RC = Join-Path $LlvmBin "llvm-rc.exe"
    $env:CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER = "lld-link"
    $env:CC_x86_64_pc_windows_msvc = "clang"
    $env:CXX_x86_64_pc_windows_msvc = "clang"
    $env:AR_x86_64_pc_windows_msvc = "llvm-lib"

    $sysroot = Split-Path -Parent (Split-Path -Parent $sysrootLib)
    $includeFlags = @(
        (Join-Path $sysroot "include"),
        (Join-Path $sysroot "include\c++\stl"),
        (Join-Path $sysroot "include\__msvc_vcruntime_intrinsics")
    ) | ForEach-Object { "-I$($_.Replace('\', '/'))" }
    $compilerFlags = "--target=x86_64-windows-msvc -fuse-ld=lld-link $($includeFlags -join ' ')"
    $env:CFLAGS_x86_64_pc_windows_msvc = $compilerFlags
    $env:CXXFLAGS_x86_64_pc_windows_msvc = $compilerFlags
    Remove-Item Env:CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_RUNNER -ErrorAction SilentlyContinue
}
