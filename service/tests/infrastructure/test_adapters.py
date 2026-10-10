"""Format adapter unit tests: decode agent memory files into conversation text."""

from __future__ import annotations

import json
import sqlite3

import pytest
from personalmemory.infrastructure.filesystem.adapters import (
    ADAPTER_FORMATS,
    decode_agent_memory,
)

CODEX_ROLLOUT_LINES = [
    {
        "timestamp": "2026-07-01T00:00:00Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "我的目标是进入人工智能行业。"}],
        },
    },
    {
        "timestamp": "2026-07-01T00:00:01Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "我可以帮你规划学习路径。"}],
        },
    },
    {
        "timestamp": "2026-07-01T00:00:02Z",
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "shell",
            "arguments": "ls",
        },
    },
]


def _write_jsonl(tmp_path, lines):
    path = tmp_path / "rollout.jsonl"
    path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines),
        encoding="utf-8",
    )
    return path


def test_adapter_formats_cover_recognized_sources():
    assert {"jsonl", "sqlite", "json"} <= ADAPTER_FORMATS


def test_decode_codex_rollout_new_format(tmp_path):
    source = _write_jsonl(tmp_path, CODEX_ROLLOUT_LINES)
    text = decode_agent_memory(source, "jsonl")
    assert "用户: 我的目标是进入人工智能行业。" in text
    assert "助手: 我可以帮你规划学习路径。" in text
    # function_call entries are skipped
    assert "function_call" not in text


def test_decode_codex_rollout_legacy_format(tmp_path):
    lines = [
        {"type": "user_message", "payload": {"content": "我完成了 Agent 项目。"}},
        {"type": "agent_message", "payload": {"content": "很好，接下来可以做记忆层。"}},
        {"type": "session_meta", "payload": {"id": "x"}},
    ]
    source = _write_jsonl(tmp_path, lines)
    text = decode_agent_memory(source, "jsonl")
    assert "用户: 我完成了 Agent 项目。" in text
    assert "助手: 很好，接下来可以做记忆层。" in text
    assert "session_meta" not in text


def test_decode_claude_history_jsonl(tmp_path):
    lines = [
        {
            "type": "user",
            "message": {"role": "user", "content": "我决定采用结构化查询优先。"},
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "这个选择很合理。"}],
            },
        },
    ]
    source = _write_jsonl(tmp_path, lines)
    text = decode_agent_memory(source, "jsonl")
    assert "用户: 我决定采用结构化查询优先。" in text
    assert "助手: 这个选择很合理。" in text


def test_decode_claude_command_history_uses_display_not_pasted_contents(tmp_path):
    lines = [
        {
            "display": "I decided to use local-first storage.",
            "pastedContents": {"1": "must not be extracted"},
            "timestamp": 1,
            "project": "project",
            "sessionId": "session",
        }
    ]
    source = _write_jsonl(tmp_path, lines)

    text = decode_agent_memory(source, "jsonl")

    assert text.endswith("I decided to use local-first storage.")
    assert "must not be extracted" not in text


def test_decode_jsonl_skips_malformed_lines(tmp_path):
    source = tmp_path / "broken.jsonl"
    source.write_text(
        "{bad json\n{\"role\": \"user\", \"content\": \"我掌握 Python。\"}\n",
        encoding="utf-8",
    )
    text = decode_agent_memory(source, "jsonl")
    assert "我掌握 Python" in text
    assert text.startswith("用户: ")


def test_decode_sqlite_conversation_store(tmp_path):
    db_path = tmp_path / "conversations.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY, role TEXT, content TEXT)")
    conn.executemany(
        "INSERT INTO conversations (role, content) VALUES (?, ?)",
        [
            ("user", "我完成了 PersonalMemory 原型开发。"),
            ("assistant", "很好，原型已经可以本地运行。"),
            ("user", '{"type":"text","text":"我总结出 Agent 不能主动写长期记忆。"}'),
        ],
    )
    conn.commit()
    conn.close()

    text = decode_agent_memory(db_path, "sqlite")
    assert "用户: 我完成了 PersonalMemory 原型开发。" in text
    assert "助手: 很好，原型已经可以本地运行。" in text
    assert "我总结出 Agent 不能主动写长期记忆。" in text


def test_decode_sqlite_missing_tables_returns_empty(tmp_path):
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    conn.commit()
    conn.close()
    assert decode_agent_memory(db_path, "sqlite") == ""


def test_decode_unsupported_format_rejected(tmp_path):
    source = tmp_path / "x.bin"
    source.write_bytes(b"data")
    with pytest.raises(ValueError):
        decode_agent_memory(source, "leveldb")
