from __future__ import annotations

import json
from pathlib import Path


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_nsis_preinstall_hook_releases_all_personalmemory_files() -> None:
    root = repository_root()
    config = json.loads(
        (root / "desktop" / "src-tauri" / "tauri.conf.json").read_text(
            encoding="utf-8"
        )
    )
    nsis = config["bundle"]["windows"]["nsis"]
    assert nsis["installerHooks"] == "windows/installer-hooks.nsh"

    hooks = (
        root / "desktop" / "src-tauri" / nsis["installerHooks"]
    ).read_text(encoding="utf-8")
    assert "!macro NSIS_HOOK_PREINSTALL" in hooks
    assert "/IM personalmemory-desktop.exe" in hooks
    assert "/IM personalmemory-service.exe" in hooks
    assert "/IM personalmemory-mcp.exe" in hooks
    assert "/IM personalmemory-hook.exe" in hooks
    assert "/T" in hooks
    assert "/F" in hooks
    assert "claude.exe" not in hooks
    assert "codex.exe" not in hooks
