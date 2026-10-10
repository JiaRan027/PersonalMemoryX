from __future__ import annotations

from pathlib import Path

import pytest
from personalmemory.domain.recognition import (
    AgentKind,
    FilesystemAgentMemoryRecognizer,
    SourceFormat,
    SourceKind,
)


def build_fixture(root: Path) -> None:
    def touch(rel: str, content: str = "x") -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    touch(".codex/memories/MEMORY.md", "# Memory\n")
    touch(".codex/memories/.git/notes.md", "must be skipped")
    touch(".codex/sessions/2026/05/rollout-1.jsonl", "{}\n")
    touch(".codex/sessions/2026/05/rollout-2.jsonl", "{}\n")
    touch(".claude/history.jsonl", "{}\n")
    touch(".claude/projects/C--Users-test-user/memory/MEMORY.md", "# p\n")
    touch(".claude/projects/C--Users-test-user/memory/feedback_x.md", "# f\n")
    touch(".gemini/antigravity/conversations/a.db", "sqlite")
    touch(".gemini/antigravity/conversations/b.pb", "proto")
    touch(".cursor/ai-tracking/ai-code-tracking.db", "sqlite")
    touch(".config/opencode/opencode.json", "{}")


def make_recognizer(tmp_path: Path) -> FilesystemAgentMemoryRecognizer:
    home = tmp_path / "home"
    appdata = tmp_path / "appdata"
    build_fixture(home)
    (appdata / "Claude" / "Local Storage" / "leveldb").mkdir(parents=True)
    (appdata / "Claude" / "Local Storage" / "leveldb" / "000001.ldb").write_bytes(b"ldb")
    return FilesystemAgentMemoryRecognizer(
        roots={"home": home, "appdata": appdata, "localappdata": tmp_path / "local"}
    )


def find(sources, agent: AgentKind, kind: SourceKind):
    for source in sources:
        if source.agent is agent and source.kind is kind:
            return source
    pytest.fail(f"missing source for {agent}/{kind}")


def test_recognizes_known_agent_memories(tmp_path: Path) -> None:
    result = make_recognizer(tmp_path).recognize()

    codex_notes = find(result.sources, AgentKind.CODEX, SourceKind.MEMORY_NOTE)
    assert codex_notes.format is SourceFormat.MARKDOWN
    assert codex_notes.file_count == 1  # .git/notes.md skipped
    assert codex_notes.priority == 100

    codex_sessions = find(result.sources, AgentKind.CODEX, SourceKind.SESSION_STORE)
    assert codex_sessions.format is SourceFormat.JSONL
    assert codex_sessions.file_count == 2
    assert codex_sessions.path.name == "sessions"

    claude_history = find(result.sources, AgentKind.CLAUDE_CODE, SourceKind.CONVERSATION_LOG)
    assert claude_history.format is SourceFormat.JSONL
    assert claude_history.file_count == 1

    claude_notes = find(result.sources, AgentKind.CLAUDE_CODE, SourceKind.MEMORY_NOTE)
    assert claude_notes.format is SourceFormat.MARKDOWN
    assert claude_notes.file_count == 2
    assert "projects" in str(claude_notes.path)

    gemini_sqlite = find(result.sources, AgentKind.GEMINI_CLI, SourceKind.SESSION_STORE)
    assert gemini_sqlite.format is SourceFormat.SQLITE
    gemini_proto = [
        s
        for s in result.sources
        if s.agent is AgentKind.GEMINI_CLI and s.format is SourceFormat.PROTOBUF
    ]
    assert len(gemini_proto) == 1

    cursor = find(result.sources, AgentKind.CURSOR, SourceKind.STATE_STORE)
    assert cursor.format is SourceFormat.SQLITE

    desktop = find(result.sources, AgentKind.CLAUDE_DESKTOP, SourceKind.STATE_STORE)
    assert desktop.format is SourceFormat.LEVELDB
    assert desktop.path.name == "leveldb"


def test_skips_unscanned_or_missing_roots(tmp_path: Path) -> None:
    home = tmp_path / "home"
    build_fixture(home)
    result = FilesystemAgentMemoryRecognizer(roots={"home": home}).recognize()
    agents = {s.agent for s in result.sources}
    assert AgentKind.CLAUDE_DESKTOP not in agents


def test_result_as_dict_summary(tmp_path: Path) -> None:
    result = make_recognizer(tmp_path).recognize()
    payload = result.as_dict()
    assert payload["summary"]["source_count"] == len(result.sources)
    assert payload["summary"]["agent_count"] == len(result.by_agent())
    assert payload["summary"]["total_size_bytes"] > 0
    assert all(item["path"] for item in payload["items"])
    assert payload["scanned_roots"]
