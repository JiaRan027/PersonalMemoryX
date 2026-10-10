from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

HOOK_EVENTS = ("UserPromptSubmit", "Stop")
CONNECTOR_PATTERN = re.compile(r"--connector-id\s+(connector_[A-Za-z0-9._:-]+)")
SHELL_PREFIX = (
    "powershell.exe -NoLogo -NoProfile -NonInteractive "
    '-ExecutionPolicy Bypass -Command "'
)


def is_managed_hook(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    command = value.get("command")
    return bool(
        isinstance(command, str)
        and "--connector-id" in command
        and ("personalmemory-hook" in command or "personalmemory.agent_hook" in command)
    )


def managed_hooks(root: dict[str, Any]) -> list[dict[str, Any]]:
    hooks = root.get("hooks")
    if not isinstance(hooks, dict):
        return []
    found: list[dict[str, Any]] = []
    for event_name in HOOK_EVENTS:
        groups = hooks.get(event_name)
        if not isinstance(groups, list):
            continue
        event_hooks: list[dict[str, Any]] = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            event_hooks.extend(item for item in group["hooks"] if is_managed_hook(item))
        if len(event_hooks) != 1:
            raise ValueError(
                f"Expected exactly one PersonalMemory Hook in hooks.{event_name}; "
                f"found {len(event_hooks)}"
            )
        found.extend(event_hooks)
    return found


def build_command(executable: Path, connector_id: str) -> str:
    path = executable.resolve(strict=True).as_posix().replace("'", "''")
    return f"{SHELL_PREFIX}& '{path}' hook --connector-id {connector_id}\""


def repair(settings: Path, executable: Path, *, dry_run: bool) -> dict[str, Any]:
    settings = settings.resolve(strict=True)
    root = json.loads(settings.read_text(encoding="utf-8-sig"))
    if not isinstance(root, dict):
        raise ValueError("Claude settings root must be a JSON object")

    hooks = managed_hooks(root)
    if len(hooks) != len(HOOK_EVENTS):
        raise ValueError(
            f"Expected {len(HOOK_EVENTS)} PersonalMemory Hooks; found {len(hooks)}"
        )
    changed = 0
    for hook in hooks:
        match = CONNECTOR_PATTERN.search(str(hook["command"]))
        if match is None:
            raise ValueError("PersonalMemory Hook has an invalid Connector ID")
        command = build_command(executable, match.group(1))
        if hook["command"] != command:
            hook["command"] = command
            changed += 1

    summary: dict[str, Any] = {
        "managed_hooks": len(hooks),
        "changed_hooks": changed,
        "dry_run": dry_run,
        "backup_created": False,
        "all_use_explicit_powershell": all(
            str(hook["command"]).startswith(SHELL_PREFIX) for hook in hooks
        ),
    }
    if dry_run or changed == 0:
        return summary

    rendered = json.dumps(root, ensure_ascii=False, indent=2) + "\n"
    descriptor, raw_temp = tempfile.mkstemp(
        prefix=f".{settings.name}.personalmemory-", suffix=".tmp", dir=settings.parent
    )
    temp = Path(raw_temp)
    backup = settings.with_name(
        f"{settings.name}.personalmemory-{datetime.now().strftime('%Y%m%dT%H%M%S')}.bak"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        candidate = json.loads(temp.read_text(encoding="utf-8"))
        candidate_hooks = managed_hooks(candidate)
        if len(candidate_hooks) != len(HOOK_EVENTS) or not all(
            str(hook["command"]).startswith(SHELL_PREFIX) for hook in candidate_hooks
        ):
            raise ValueError("Claude settings read-back validation failed")
        shutil.copy2(settings, backup)
        os.replace(temp, settings)
        final = json.loads(settings.read_text(encoding="utf-8"))
        if len(managed_hooks(final)) != len(HOOK_EVENTS):
            raise ValueError("Claude settings final validation failed")
    except Exception:
        temp.unlink(missing_ok=True)
        if backup.is_file():
            shutil.copy2(backup, settings)
        raise

    summary["backup_created"] = True
    summary["backup_path"] = str(backup)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Safely repair installed PersonalMemory Claude Hook commands."
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=Path.home() / ".claude" / "settings.json",
    )
    parser.add_argument(
        "--hook-executable",
        type=Path,
        default=Path(os.environ["LOCALAPPDATA"])
        / "PersonalMemory"
        / "personalmemory-hook"
        / "personalmemory-hook.exe",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            repair(args.settings, args.hook_executable, dry_run=args.dry_run),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
