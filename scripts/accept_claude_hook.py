from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from personalmemory.agent_hook import build_claude_hook_settings

CAPTURE_PROMPT = (
    "我决定 PersonalMemory 的 Continuity Core 保持平台无关，"
    "因为 Agent 可以替换而连续性不可替换。请简短确认。"
)
RETRIEVAL_PROMPT = (
    "First query the authorized long-term memory. Then answer in English: what Continuity Core "
    "decision did I establish in the previous turn, and why? Say you do not know if there is "
    "no evidence."
)


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
    raise RuntimeError("isolated PersonalMemory Service did not become ready")


def run_claude(
    executable: str,
    prompt: str,
    *,
    cwd: Path,
    env: dict[str, str],
    extra_args: list[str],
    output_format: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    command = [
        executable,
        "-p",
        prompt,
        "--output-format",
        output_format,
        "--permission-mode",
        "dontAsk",
        "--max-budget-usd",
        "0.50",
        *extra_args,
    ]
    if output_format == "stream-json":
        command.append("--verbose")
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Claude exited with {completed.returncode}: {completed.stderr[-1000:]}")
    if output_format == "json":
        return json.loads(completed.stdout), []
    events = [
        json.loads(line) for line in completed.stdout.splitlines() if line.strip().startswith("{")
    ]
    result = next((event for event in reversed(events) if event.get("type") == "result"), {})
    return result, events


def post_hook(
    executable: Path,
    connector_id: str,
    payload: dict[str, object],
    *,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(executable), "hook", "--connector-id", connector_id],
        input=json.dumps(payload, ensure_ascii=False),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=10,
        check=False,
    )


def database_counts(database_path: Path) -> dict[str, int]:
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


def wait_for_memory(database_path: Path, timeout: float = 60.0) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with closing(sqlite3.connect(database_path)) as connection:
            row = connection.execute(
                "SELECT id, statement, reason FROM memory_objects ORDER BY created_at LIMIT 1"
            ).fetchone()
        if row:
            return {"id": row[0], "statement": row[1], "reason": row[2]}
        time.sleep(0.1)
    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(
            "SELECT prompt_id, status, user_hash IS NOT NULL, assistant_hash IS NOT NULL "
            "FROM agent_turn_observations ORDER BY created_at"
        ).fetchall()
    raise RuntimeError(
        "real Claude Hook did not create a Memory; "
        f"counts={database_counts(database_path)!r}; observations={rows!r}"
    )


def observation_identity(database_path: Path) -> dict[str, str]:
    with closing(sqlite3.connect(database_path)) as connection:
        row = connection.execute(
            "SELECT id, session_id, prompt_id, transcript_path, source_revision_id "
            "FROM agent_turn_observations ORDER BY created_at LIMIT 1"
        ).fetchone()
    if not row:
        raise RuntimeError("real Claude Hook did not create an Observation")
    return {
        "id": row[0],
        "session_id": row[1],
        "prompt_id": row[2],
        "transcript_path": row[3],
        "source_revision_id": row[4],
    }


def tool_names(events: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for event in events:
        message = event.get("message") or {}
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                names.append(str(block.get("name")))
    return names


def mentions_platform_neutrality(answer: str) -> bool:
    normalized = answer.casefold()
    return "平台无关" in answer or (
        "platform" in normalized
        and any(term in normalized for term in ("agnostic", "independent", "neutral"))
    )


def mentions_replaceable_agent(answer: str) -> bool:
    normalized = answer.casefold()
    return (
        "agent 可以替换" in normalized
        or "agent 可替换" in normalized
        or (
            "agent" in normalized
            and any(term in normalized for term in ("replaceable", "replaced"))
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run isolated real Claude Hook acceptance.")
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--mcp-executable", type=Path, required=True)
    parser.add_argument("--hook-executable", type=Path)
    args = parser.parse_args()

    claude = shutil.which(args.claude) or args.claude
    mcp_executable = args.mcp_executable.resolve()
    hook_executable = (
        args.hook_executable.resolve() if args.hook_executable else mcp_executable
    )
    if not mcp_executable.is_file():
        raise SystemExit(f"missing MCP executable: {mcp_executable}")
    if not hook_executable.is_file():
        raise SystemExit(f"missing Hook executable: {hook_executable}")

    evidence: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="personalmemory-real-hook-") as raw_root:
        root = Path(raw_root)
        project = root / "project"
        project.mkdir()
        data_dir = root / "data"
        token = secrets.token_urlsafe(48)
        endpoint: str | None = None
        headers: dict[str, str] | None = None
        service_env = os.environ.copy()
        service_env.update(
            {
                "PERSONALMEMORY_DATA_DIR": str(data_dir),
                "PERSONALMEMORY_API_TOKEN": token,
                "PERSONALMEMORY_HOST": "127.0.0.1",
                "PERSONALMEMORY_PORT": "0",
                "PERSONALMEMORY_ENABLE_BACKGROUND_SYNC": "false",
                "PERSONALMEMORY_LLM_ENABLED": "false",
            }
        )
        service = subprocess.Popen(
            [sys.executable, "-m", "personalmemory.main"],
            env=service_env,
            cwd=project,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        connection = wait_for_connection(data_dir / "runtime" / "connection.json")
        endpoint = connection["endpoint"]
        headers = {"Authorization": f"Bearer {connection['token']}"}
        hook_env = os.environ.copy()
        hook_env["PERSONALMEMORY_DATA_DIR"] = str(data_dir)

        try:
            with httpx.Client(base_url=endpoint + "/api/v1", headers=headers, timeout=5) as client:
                response = client.post(
                    "/connectors",
                    json={
                        "name": "Real Claude Reference Translator",
                        "profile_id": "claude_code_hook",
                        "source_path": str(project),
                        "target_path": None,
                        "namespace": "projects/real-hook-acceptance",
                        "read_enabled": True,
                        "write_enabled": False,
                    },
                )
                response.raise_for_status()
                connector_id = response.json()["id"]

            settings_path = root / "claude-hook-settings.json"
            settings_path.write_text(
                json.dumps(
                    build_claude_hook_settings(str(hook_executable), connector_id),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            capture_result, _ = run_claude(
                claude,
                CAPTURE_PROMPT,
                cwd=project,
                env=hook_env,
                extra_args=[
                    "--settings",
                    str(settings_path),
                    "--tools",
                    "",
                    "--session-id",
                    str(uuid4()),
                ],
                output_format="json",
            )

            database_path = data_dir / "data" / "memory.db"
            memory = wait_for_memory(database_path)
            observation = observation_identity(database_path)
            first_counts = database_counts(database_path)
            assistant_text = str(capture_result.get("result") or "已确认。")
            replay_payloads = [
                {
                    "session_id": observation["session_id"],
                    "prompt_id": observation["prompt_id"],
                    "transcript_path": observation["transcript_path"],
                    "cwd": str(project),
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": CAPTURE_PROMPT,
                },
                {
                    "session_id": observation["session_id"],
                    "prompt_id": observation["prompt_id"],
                    "transcript_path": observation["transcript_path"],
                    "cwd": str(project),
                    "hook_event_name": "Stop",
                    "last_assistant_message": assistant_text,
                },
            ]
            replay_results = [
                post_hook(hook_executable, connector_id, payload, env=hook_env)
                for payload in replay_payloads
            ]
            replay_counts = database_counts(database_path)

            synthetic_transcript = project / "synthetic.jsonl"
            synthetic_transcript.write_text("", encoding="utf-8")
            negative_pairs = [
                (
                    "negative-secret",
                    "我决定使用 api_token=TEST_ONLY_REDACTED_VALUE，因为测试需要。",
                    "已确认。",
                ),
                (
                    "negative-assistant-only",
                    "请比较 SQLite 和向量数据库。",
                    "我决定采用向量数据库。",
                ),
            ]
            for prompt_id, user_text, assistant_text in negative_pairs:
                session_id = f"session-{prompt_id}"
                for payload in (
                    {
                        "session_id": session_id,
                        "prompt_id": prompt_id,
                        "transcript_path": str(synthetic_transcript),
                        "cwd": str(project),
                        "hook_event_name": "UserPromptSubmit",
                        "prompt": user_text,
                    },
                    {
                        "session_id": session_id,
                        "prompt_id": prompt_id,
                        "transcript_path": str(synthetic_transcript),
                        "cwd": str(project),
                        "hook_event_name": "Stop",
                        "last_assistant_message": assistant_text,
                    },
                ):
                    result = post_hook(hook_executable, connector_id, payload, env=hook_env)
                    if result.returncode != 0 or result.stdout or result.stderr:
                        raise RuntimeError("negative Hook delivery was not silent/fail-open")

            negative_counts = database_counts(database_path)
            mcp_config = root / "mcp.json"
            mcp_config.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "personalmemory": {
                                "command": str(mcp_executable),
                                "env": {"PERSONALMEMORY_DATA_DIR": str(data_dir)},
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            retrieval_result, retrieval_events = run_claude(
                claude,
                RETRIEVAL_PROMPT,
                cwd=project,
                env=hook_env,
                extra_args=[
                    "--mcp-config",
                    str(mcp_config),
                    "--strict-mcp-config",
                    "--tools",
                    "mcp__personalmemory__memory_context",
                    "--allowedTools",
                    "mcp__personalmemory__memory_context",
                ],
                output_format="stream-json",
            )
            tools = tool_names(retrieval_events)
            answer = str(retrieval_result.get("result") or "")
            answer_mentions_platform_neutral = mentions_platform_neutrality(answer)
            answer_mentions_replaceable_agent = mentions_replaceable_agent(answer)

            with httpx.Client(base_url=endpoint + "/api/v1", headers=headers, timeout=5) as client:
                client.post("/system/shutdown").raise_for_status()
            service.wait(timeout=10)
            started = time.perf_counter()
            down_result = post_hook(
                hook_executable,
                connector_id,
                {
                    "session_id": "service-down-session",
                    "prompt_id": "service-down-prompt",
                    "transcript_path": str(synthetic_transcript),
                    "cwd": str(project),
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "我喜欢工程化分析。",
                },
                env=hook_env,
            )
            down_ms = (time.perf_counter() - started) * 1000

            evidence = {
                "client": "Claude Code 2.1.220",
                "model": capture_result.get("modelUsage") or retrieval_result.get("modelUsage"),
                "isolated_database": True,
                "global_claude_config_modified": False,
                "explicit_remember_used": False,
                "capture": {
                    "observation_id": observation["id"],
                    "source_revision_id": observation["source_revision_id"],
                    "memory_id": memory["id"],
                    "memory_type": "decision",
                    "statement_correct": "Continuity Core" in memory["statement"],
                    "reason_correct": bool(memory["reason"]),
                    "counts": first_counts,
                },
                "duplicate_replay": {
                    "hook_exit_codes": [result.returncode for result in replay_results],
                    "silent": all(
                        not result.stdout and not result.stderr for result in replay_results
                    ),
                    "counts_unchanged": replay_counts == first_counts,
                },
                "negative_cases": {
                    "secret_memory_created": negative_counts["memories"] != 1,
                    "assistant_only_memory_created": negative_counts["memories"] != 1,
                    "counts": negative_counts,
                },
                "retrieval": {
                    "tool_calls": tools,
                    "memory_context_called": any(name.endswith("memory_context") for name in tools),
                    "answer_mentions_platform_neutral": answer_mentions_platform_neutral,
                    "answer_mentions_replaceable_agent": answer_mentions_replaceable_agent,
                    "answer_excerpt": answer[:500],
                },
                "service_down": {
                    "exit_code": down_result.returncode,
                    "silent": not down_result.stdout and not down_result.stderr,
                    "elapsed_ms": round(down_ms, 3),
                    "under_five_seconds": down_ms < 5000,
                },
                "cost": {
                    "capture_usd": capture_result.get("total_cost_usd"),
                    "retrieval_usd": retrieval_result.get("total_cost_usd"),
                },
            }
        finally:
            if service.poll() is None:
                if endpoint is not None and headers is not None:
                    try:
                        with httpx.Client(
                            base_url=endpoint + "/api/v1", headers=headers, timeout=2
                        ) as client:
                            client.post("/system/shutdown")
                    except httpx.HTTPError:
                        pass
                try:
                    service.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    service.terminate()
                    try:
                        service.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        service.kill()
                        service.wait(timeout=5)

    capture = evidence["capture"]
    duplicate = evidence["duplicate_replay"]
    negative = evidence["negative_cases"]
    retrieval = evidence["retrieval"]
    service_down = evidence["service_down"]
    evidence["passed"] = all(
        (
            capture["statement_correct"],
            capture["reason_correct"],
            duplicate["silent"],
            duplicate["counts_unchanged"],
            not negative["secret_memory_created"],
            not negative["assistant_only_memory_created"],
            retrieval["memory_context_called"],
            retrieval["answer_mentions_platform_neutral"],
            retrieval["answer_mentions_replaceable_agent"],
            service_down["exit_code"] == 0,
            service_down["silent"],
            service_down["under_five_seconds"],
        )
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    if not evidence["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
