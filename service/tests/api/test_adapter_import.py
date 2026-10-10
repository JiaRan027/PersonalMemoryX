"""End-to-end: adapter profiles decode agent files and ingest memories."""

from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient
from personalmemory.api.app import create_app
from personalmemory.settings import Settings


def make_client(tmp_path):
    return TestClient(
        create_app(
            Settings(
                data_dir=tmp_path / "data",
                api_token="adapter-test-token",
                enable_background_sync=False,
            )
        )
    )


def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer adapter-test-token"}


def create_and_sync(client, profile_id: str, source_path: str, namespace: str) -> dict:
    response = client.post(
        "/api/v1/connectors",
        headers=auth_headers(),
        json={
            "name": "Adapter source",
            "profile_id": profile_id,
            "source_path": source_path,
            "target_path": None,
            "namespace": namespace,
            "read_enabled": True,
            "write_enabled": False,
        },
    )
    assert response.status_code == 201, response.text
    connector_id = response.json()["id"]
    synced = client.post(
        f"/api/v1/connectors/{connector_id}/sync", headers=auth_headers()
    )
    assert synced.status_code == 200, synced.text
    return synced.json()


def test_codex_jsonl_connector_ingests_memories(tmp_path):
    source = tmp_path / "rollout.jsonl"
    lines = [
        {"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "我完成了 Agent 项目。"}],
        }},
        {"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "我决定采用结构化查询优先。"}],
        }},
        {"type": "response_item", "payload": {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "这个选择很合理。"}],
        }},
    ]
    source.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines),
        encoding="utf-8",
    )
    with make_client(tmp_path) as client:
        result = create_and_sync(client, "codex_jsonl", str(source), "codex")
        assert result["ingested"] == 1
        memories = client.get(
            "/api/v1/memories", headers=auth_headers()
        ).json()["items"]
        statements = [item["statement"] for item in memories]
        assert any("Agent 项目" in statement for statement in statements)
        assert any("结构化查询优先" in statement for statement in statements)
        # assistant statements are not ingested as user memories
        assert not any("这个选择很合理" in statement for statement in statements)


def test_gemini_sqlite_connector_ingests_memories(tmp_path):
    db_path = tmp_path / "conversations.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE conversations (id INTEGER PRIMARY KEY, role TEXT, content TEXT)"
    )
    conn.executemany(
        "INSERT INTO conversations (role, content) VALUES (?, ?)",
        [
            ("user", "我完成了 PersonalMemory 原型开发。"),
            ("user", "我总结出 Agent 不能主动写长期记忆。"),
            ("assistant", "这是一个好结论。"),
        ],
    )
    conn.commit()
    conn.close()

    with make_client(tmp_path) as client:
        result = create_and_sync(client, "gemini_sqlite", str(db_path), "gemini")
        assert result["ingested"] == 1
        memories = client.get(
            "/api/v1/memories", headers=auth_headers()
        ).json()["items"]
        statements = [item["statement"] for item in memories]
        assert any("PersonalMemory 原型" in statement for statement in statements)
        assert any("不能主动写长期记忆" in statement for statement in statements)



def _write_rollout(path, user_texts):
    lines = [
        {"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": text}],
        }}
        for text in user_texts
    ]
    path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines),
        encoding="utf-8",
    )


def test_import_endpoint_ingests_jsonl_file(tmp_path):
    source = tmp_path / "rollout.jsonl"
    _write_rollout(source, ["我完成了 Agent 项目。", "我决定采用结构化查询优先。"])
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/sources/import",
            headers=auth_headers(),
            json={
                "profile_id": "codex_jsonl",
                "source_path": str(source),
                "namespace": "codex",
            },
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert payload["ingested"] == 1
    assert payload["failed"] == 0
    assert payload["files"][0]["status"] == "ingested"

    with make_client(tmp_path) as client:
        memories = client.get("/api/v1/memories", headers=auth_headers()).json()["items"]
    assert len(memories) == 2


def test_import_directory_supports_many_files_and_dedupe(tmp_path):
    directory = tmp_path / "sessions"
    directory.mkdir()
    _write_rollout(directory / "a.jsonl", ["我完成了 PersonalMemory 原型。"])
    _write_rollout(directory / "b.jsonl", ["我掌握 Python。"])
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/sources/import",
            headers=auth_headers(),
            json={
                "profile_id": "codex_jsonl",
                "source_path": str(directory),
                "namespace": "codex",
            },
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 2
    assert payload["ingested"] == 2
    assert payload["failed"] == 0

    second = client.post(
        "/api/v1/sources/import",
        headers=auth_headers(),
        json={
            "profile_id": "codex_jsonl",
            "source_path": str(directory),
            "namespace": "codex",
        },
    ).json()
    assert second["total"] == 2
    assert second["duplicate"] == 2
    assert second["ingested"] == 0


def test_import_directory_skips_oversized_files(tmp_path):
    directory = tmp_path / "mixed"
    directory.mkdir()
    _write_rollout(directory / "small.jsonl", ["我完成了 Agent 项目。"])
    (directory / "huge.jsonl").write_bytes(b"x" * (64 * 1024 * 1024 + 1))
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/sources/import",
            headers=auth_headers(),
            json={
                "profile_id": "codex_jsonl",
                "source_path": str(directory),
                "namespace": "codex",
            },
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 2
    assert payload["ingested"] == 1
    assert payload["skipped"] == 1
    assert payload["failed"] == 0
    statuses = {item["status"] for item in payload["files"]}
    assert statuses == {"ingested", "skipped"}


def test_import_codex_memory_markdown_profile(tmp_path):
    """Codex 真实记忆文件一键导入：Profile/Preferences/General Tips 全部入库。"""
    source = tmp_path / "MEMORY.md"
    source.write_text(
        "## User Profile\n\n- The user works on ExampleProject across uniapp and backend.\n\n"
        "## User preferences\n\n- Update the project development log in the same change.\n\n"
        "## General Tips\n\n- Keep verification status precise.\n",
        encoding="utf-8",
    )
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/sources/import",
            headers=auth_headers(),
            json={
                "profile_id": "codex_memory_markdown",
                "source_path": str(source),
                "namespace": "codex_memories",
            },
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert payload["ingested"] == 1
    memories = client.get(
        "/api/v1/memories", headers=auth_headers()
    ).json()["items"]
    types = [item["type"] for item in memories]
    assert sorted(types) == ["fact", "preference"], types
    assert all("Keep verification" not in item["statement"] for item in memories)


def test_import_claude_memory_markdown_profile(tmp_path):
    """Claude 记忆笔记一键导入：feedback -> preference（含 reason），索引文件 0 提取。"""
    feedback = tmp_path / "feedback_language.md"
    feedback.write_text(
        "---\ntype: feedback\n---\n始终用中文回答用户的问题。\n\n"
        "**Why:** 用户明确要求使用中文交流。\n",
        encoding="utf-8",
    )
    index = tmp_path / "MEMORY.md"
    index.write_text(
        "- [始终用中文回答](feedback_language.md) — 用户要求所有回复使用中文\n",
        encoding="utf-8",
    )
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/sources/import",
            headers=auth_headers(),
            json={
                "profile_id": "claude_memory_markdown",
                "source_path": str(feedback),
                "namespace": "claude_memory",
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["ingested"] == 1
    memories = client.get("/api/v1/memories", headers=auth_headers()).json()["items"]
    assert len(memories) == 1
    assert memories[0]["type"] == "preference"
    assert "始终用中文回答" in memories[0]["statement"]
    assert memories[0]["reason"] == "用户明确要求使用中文交流。"

    # 索引文件应 0 提取
    response_index = client.post(
        "/api/v1/sources/import",
        headers=auth_headers(),
        json={
            "profile_id": "claude_memory_markdown",
            "source_path": str(index),
            "namespace": "claude_memory",
        },
    )
    assert response_index.status_code == 200, response_index.text
    assert response_index.json()["processed"] == 1


def test_import_reports_processed_when_no_memories_extracted(tmp_path):
    """会话只有任务指令（如我会先…）时，如实报告 processed 而非 ingested。"""
    source = tmp_path / "rollout.jsonl"
    _write_rollout(
        source,
        ["我会先检查构建日志，再重跑测试。", "继续等待到构建成功，不会把进行中当作完成。"],
    )
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/sources/import",
            headers=auth_headers(),
            json={
                "profile_id": "codex_jsonl",
                "source_path": str(source),
                "namespace": "codex",
            },
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert payload["ingested"] == 0
    assert payload["processed"] == 1
    assert payload["files"][0]["status"] == "processed"


def test_import_rejects_missing_path(tmp_path):
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/sources/import",
            headers=auth_headers(),
            json={
                "profile_id": "codex_jsonl",
                "source_path": str(tmp_path / "nope.jsonl"),
                "namespace": "codex",
            },
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "SOURCE_PATH_NOT_FOUND"

def test_adapter_connector_rejects_oversized_sqlite(tmp_path):
    db_path = tmp_path / "big.db"
    db_path.write_bytes(b"x" * (64 * 1024 * 1024 + 1))
    client = make_client(tmp_path)
    with client:
        response = client.post(
            "/api/v1/connectors",
            headers=auth_headers(),
            json={
                "name": "Too big",
                "profile_id": "gemini_sqlite",
                "source_path": str(db_path),
                "target_path": None,
                "namespace": "general",
                "read_enabled": True,
                "write_enabled": False,
            },
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "SOURCE_TOO_LARGE"
