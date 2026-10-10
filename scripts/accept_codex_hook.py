from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import subprocess
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx


def wait_for_connection(path: Path, timeout: float = 20.0) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            time.sleep(0.1)
            continue
        if payload.get("endpoint") and payload.get("token"):
            return {"endpoint": payload["endpoint"], "token": payload["token"]}
    raise RuntimeError("isolated packaged Service did not become ready")


def post_hook(
    executable: Path,
    connector_id: str,
    payload: dict[str, object],
    *,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(executable),
            "hook",
            "--connector-id",
            connector_id,
            "--agent-family",
            "codex",
        ],
        input=json.dumps(payload, ensure_ascii=False),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=10,
        check=False,
    )


def counts(database_path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(database_path)) as connection:
        return {
            "observations": connection.execute(
                "SELECT COUNT(*) FROM agent_turn_observations"
            ).fetchone()[0],
            "sources": connection.execute("SELECT COUNT(*) FROM source_revisions").fetchone()[0],
            "candidates": connection.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[
                0
            ],
            "memories": connection.execute("SELECT COUNT(*) FROM memory_objects").fetchone()[0],
        }


def assert_clean(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode != 0 or result.stderr:
        raise RuntimeError(
            f"{label} Hook failed: exit={result.returncode}, stderr={result.stderr[-500:]!r}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Accept the packaged Codex Hook in isolation.")
    parser.add_argument("--service-executable", type=Path, required=True)
    parser.add_argument("--hook-executable", type=Path, required=True)
    args = parser.parse_args()
    service_executable = args.service_executable.resolve()
    hook_executable = args.hook_executable.resolve()
    if not service_executable.is_file() or not hook_executable.is_file():
        raise SystemExit("packaged Service or Hook executable is missing")

    with tempfile.TemporaryDirectory(prefix="personalmemory-codex-hook-") as raw_root:
        root = Path(raw_root)
        project = root / "project"
        project.mkdir()
        data_dir = root / "data"
        service_env = os.environ.copy()
        service_env.update(
            {
                "PERSONALMEMORY_DATA_DIR": str(data_dir),
                "PERSONALMEMORY_API_TOKEN": secrets.token_urlsafe(48),
                "PERSONALMEMORY_HOST": "127.0.0.1",
                "PERSONALMEMORY_PORT": "0",
                "PERSONALMEMORY_ENABLE_BACKGROUND_SYNC": "false",
                "PERSONALMEMORY_LLM_ENABLED": "false",
            }
        )
        service = subprocess.Popen(
            [str(service_executable)],
            env=service_env,
            cwd=project,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        hook_env = os.environ.copy()
        hook_env["PERSONALMEMORY_DATA_DIR"] = str(data_dir)
        connection = wait_for_connection(data_dir / "runtime" / "connection.json")
        headers = {"Authorization": f"Bearer {connection['token']}"}
        database_path = data_dir / "data" / "memory.db"

        evidence: dict[str, Any] = {}
        try:
            with httpx.Client(
                base_url=connection["endpoint"] + "/api/v1",
                headers=headers,
                timeout=5,
                trust_env=False,
            ) as client:
                health = client.get("/health")
                health.raise_for_status()
                connector = client.post(
                    "/connectors",
                    json={
                        "name": "Isolated Codex selective capture",
                        "profile_id": "codex_global_context",
                        "source_path": str(project),
                        "target_path": None,
                        "namespace": "general",
                        "read_enabled": True,
                        "write_enabled": False,
                    },
                )
                connector.raise_for_status()
                connector_id = connector.json()["id"]

                durable = [
                    {
                        "session_id": "codex-acceptance",
                        "turn_id": "turn-durable",
                        "transcript_path": None,
                        "cwd": str(project),
                        "hook_event_name": "UserPromptSubmit",
                        "prompt": "我计划开发一个示例应用。",
                    },
                    {
                        "session_id": "codex-acceptance",
                        "turn_id": "turn-durable",
                        "transcript_path": None,
                        "cwd": str(project),
                        "hook_event_name": "Stop",
                        "last_assistant_message": "好的，我会围绕这个长期目标继续协作。",
                    },
                ]
                durable_results = [
                    post_hook(hook_executable, connector_id, payload, env=hook_env)
                    for payload in durable
                ]
                for index, result in enumerate(durable_results):
                    assert_clean(result, f"durable[{index}]")
                after_durable = counts(database_path)
                memories = client.get("/memories")
                memories.raise_for_status()
                matching = [
                    item
                    for item in memories.json()["items"]
                    if item["statement"] == "开发一个示例应用"
                    and item["namespace"] == "general"
                ]
                if len(matching) != 1:
                    raise RuntimeError(f"expected one admitted Goal, received {matching!r}")

                transient = [
                    {
                        "session_id": "codex-acceptance",
                        "turn_id": "turn-transient",
                        "transcript_path": None,
                        "cwd": str(project),
                        "hook_event_name": "UserPromptSubmit",
                        "prompt": "打开 README 看看。",
                    },
                    {
                        "session_id": "codex-acceptance",
                        "turn_id": "turn-transient",
                        "transcript_path": None,
                        "cwd": str(project),
                        "hook_event_name": "Stop",
                        "last_assistant_message": "README 已打开。",
                    },
                ]
                transient_results = [
                    post_hook(hook_executable, connector_id, payload, env=hook_env)
                    for payload in transient
                ]
                for index, result in enumerate(transient_results):
                    assert_clean(result, f"transient[{index}]")
                after_transient = counts(database_path)
                if after_transient != after_durable:
                    raise RuntimeError(
                        "ordinary Codex command changed the selective-capture database: "
                        f"{after_durable!r} -> {after_transient!r}"
                    )

                shutdown = client.post("/system/shutdown")
                shutdown.raise_for_status()
                evidence = {
                    "product_version": health.json()["version"],
                    "build_revision": health.json()["build_revision"],
                    "agent_family": "codex",
                    "correlation": "turn_id",
                    "after_durable": after_durable,
                    "after_transient": after_transient,
                    "memory": {
                        "type": matching[0]["type"],
                        "statement": matching[0]["statement"],
                        "namespace": matching[0]["namespace"],
                    },
                }
            service.wait(timeout=10)
            offline = post_hook(
                hook_executable,
                connector_id,
                durable[0],
                env=hook_env,
            )
            if offline.returncode != 0 or offline.stdout or offline.stderr:
                raise RuntimeError("Service-down packaged Codex Hook was not silent and fail-open")
            evidence["service_down_fail_open"] = True
            evidence["assertions"] = {
                "durable_goal_admitted": True,
                "ordinary_command_zero_write": True,
                "packaged_hook_silent": True,
            }
            print(json.dumps(evidence, ensure_ascii=False, indent=2))
        finally:
            if service.poll() is None:
                service.kill()
                service.wait(timeout=5)


if __name__ == "__main__":
    main()
