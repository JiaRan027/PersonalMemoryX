$ErrorActionPreference = "Stop"

function Invoke-VerifyStep {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command,
        [Parameter(Mandatory = $true)]
        [string]$FailureMessage
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

Invoke-VerifyStep -Command { python -m ruff check service } -FailureMessage "Ruff failed"
Invoke-VerifyStep -Command { python -m pytest service/tests -q } -FailureMessage "Service tests failed"
Invoke-VerifyStep -Command { python scripts/demo_mcp_handoff.py } -FailureMessage "MCP handoff demo failed"

if (Test-Path -LiteralPath "desktop/package.json") {
    Push-Location desktop
    try {
        Invoke-VerifyStep -Command { npm run typecheck } -FailureMessage "Desktop typecheck failed"
        Invoke-VerifyStep -Command { npm run test:unit } -FailureMessage "Desktop unit tests failed"
        Invoke-VerifyStep -Command { npm run build } -FailureMessage "Desktop build failed"
    }
    finally {
        Pop-Location
    }
}

if (Test-Path -LiteralPath "desktop/src-tauri/Cargo.toml") {
    . (Join-Path $PSScriptRoot "rust-env.ps1")
    Invoke-VerifyStep `
        -Command {
            & $PersonalMemoryCargo test @PersonalMemoryCargoArgs `
                --manifest-path desktop/src-tauri/Cargo.toml `
                --all-targets
        } `
        -FailureMessage "Rust tests failed"
}

if (Test-Path -LiteralPath "desktop/native/personalmemory-hook/Cargo.toml") {
    . (Join-Path $PSScriptRoot "rust-env.ps1")
    Invoke-VerifyStep `
        -Command {
            & $PersonalMemoryCargo @PersonalMemoryCargoArgs `
                test `
                --manifest-path desktop/native/personalmemory-hook/Cargo.toml `
                --all-targets
        } `
        -FailureMessage "Native Hook tests failed"
}
