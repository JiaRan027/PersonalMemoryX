# Building PersonalMemory on Windows

This guide covers the current Windows x64 source tree. It is intended for contributors and users who want to build or inspect the project; it is not required to install the Alpha release.

## Requirements

- Windows 10/11 x64
- Python 3.12
- `uv` 0.11.1
- Node.js 20 and npm
- Rust stable
- Visual Studio 2026 or Build Tools with **Desktop development with C++** for the desktop installer
- WebView2 Runtime for running the desktop application
- The matching MSVC redistributable files are required for packaging the installer

Service tests and frontend checks do not require the native installer toolchain. The installer build uses the project's PowerShell scripts to locate and validate the required MSVC runtime files; do not substitute files from an unrelated installation.

## Set up dependencies

From the repository root in PowerShell:

```powershell
python -m pip install uv==0.11.1
$env:UV_PROJECT_ENVIRONMENT = ".venv"
uv sync --locked --extra dev --extra build --python 3.12
npm --prefix desktop ci
. .\.venv\Scripts\Activate.ps1
```

## Run the verification suite

```powershell
.\scripts\verify.ps1
```

The verification script runs service lint/tests, the MCP handoff demo, desktop type-check/build/unit tests, and Rust tests for the desktop shell and native Hook. Some end-to-end acceptance scripts require an isolated running Service and are not part of this command.

## Build a local Windows installer

```powershell
.\scripts\build-sidecar.ps1 -Python .\.venv\Scripts\python.exe
. .\scripts\rust-env.ps1
Push-Location desktop
try {
  npm run tauri -- build --config src-tauri/tauri.local.conf.json
  if ($LASTEXITCODE -ne 0) { throw "Local Tauri build failed" }
}
finally { Pop-Location }
```

The local NSIS installer is written under `desktop/src-tauri/target/release/bundle/nsis/`. This command disables creation of updater artifacts, so it does not require the official updater private key. The resulting local installer is unsigned and is for local testing only; it is not an official Alpha package and must not be presented or uploaded as one.

The current Alpha package was built and published by the protected GitHub Actions release workflow. The workflow run, package signature, generated update feed, and release assets were verified for this release. This confirms the release pipeline, but does not constitute end-to-end installation, rollback, or Authenticode validation. Contributors do not need—and must never request, copy, commit, or place in a local build—the official updater private key. See the README for the Authenticode and updater-signature limitations of the current release.

## Safe contribution practices

- Use synthetic data in tests and examples. Never commit a real memory database, private conversation, user-specific path, API credential, or updater private key.
- Keep build output, local data, internal review notes, and acceptance captures out of commits. The repository ignore rules intentionally exclude internal project documents; only the small public documentation set is allow-listed.
- Before submitting, run `git diff --check` and inspect `git status --short` for generated files or local data.
- For product behavior or integration changes, describe what was actually tested and what remains unverified. Do not present a synthetic MCP demo as a real-agent acceptance test.
