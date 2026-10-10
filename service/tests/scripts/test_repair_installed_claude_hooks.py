from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def load_repair_module():
    script = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "repair_installed_claude_hooks.py"
    )
    spec = importlib.util.spec_from_file_location("repair_installed_claude_hooks", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def managed_group(command: str) -> dict[str, object]:
    return {
        "hooks": [
            {
                "type": "command",
                "command": command,
                "async": False,
                "timeout": 5,
            }
        ]
    }


def test_repair_preserves_unmanaged_values_and_is_idempotent(tmp_path: Path) -> None:
    repair_module = load_repair_module()
    settings = tmp_path / "settings.json"
    hook = tmp_path / "PersonalMemory" / "personalmemory-hook.exe"
    hook.parent.mkdir()
    hook.write_bytes(b"hook")
    old = '"C:/Old/personalmemory-hook.exe" hook --connector-id connector_demo'
    settings.write_text(
        json.dumps(
            {
                "theme": "dark",
                "hooks": {
                    "UserPromptSubmit": [managed_group(old)],
                    "Stop": [
                        managed_group("other-command"),
                        managed_group(old),
                    ],
                },
                "unrelated": {"keep": True},
            }
        ),
        encoding="utf-8",
    )

    first = repair_module.repair(settings, hook, dry_run=False)
    updated = json.loads(settings.read_text(encoding="utf-8"))
    assert first["managed_hooks"] == 2
    assert first["changed_hooks"] == 2
    assert first["backup_created"] is True
    assert Path(first["backup_path"]).is_file()
    assert updated["theme"] == "dark"
    assert updated["unrelated"] == {"keep": True}
    assert updated["hooks"]["Stop"][0]["hooks"][0]["command"] == "other-command"
    assert all(
        item["command"].startswith(repair_module.SHELL_PREFIX)
        for item in repair_module.managed_hooks(updated)
    )

    second = repair_module.repair(settings, hook, dry_run=False)
    assert second["managed_hooks"] == 2
    assert second["changed_hooks"] == 0
    assert second["backup_created"] is False


def test_repair_refuses_missing_managed_event_without_writing(tmp_path: Path) -> None:
    repair_module = load_repair_module()
    settings = tmp_path / "settings.json"
    hook = tmp_path / "personalmemory-hook.exe"
    hook.write_bytes(b"hook")
    original = json.dumps(
        {
            "hooks": {
                "UserPromptSubmit": [
                    managed_group(
                        '"C:/Old/personalmemory-hook.exe" hook '
                        "--connector-id connector_demo"
                    )
                ]
            }
        }
    )
    settings.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="Expected 2 PersonalMemory Hooks; found 1"):
        repair_module.repair(settings, hook, dry_run=False)

    assert settings.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("settings.json.personalmemory-*.bak"))
