from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class AgentKind(StrEnum):
    CODEX = "codex"
    CLAUDE_CODE = "claude_code"
    CLAUDE_DESKTOP = "claude_desktop"
    GEMINI_CLI = "gemini_cli"
    CURSOR = "cursor"
    OPENCODE = "opencode"


class SourceKind(StrEnum):
    MEMORY_NOTE = "memory_note"
    CONVERSATION_LOG = "conversation_log"
    SESSION_STORE = "session_store"
    STATE_STORE = "state_store"


class SourceFormat(StrEnum):
    MARKDOWN = "markdown"
    JSONL = "jsonl"
    JSON = "json"
    SQLITE = "sqlite"
    LEVELDB = "leveldb"
    PROTOBUF = "protobuf"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AgentMemorySource:
    agent: AgentKind
    kind: SourceKind
    format: SourceFormat
    path: Path
    size_bytes: int
    file_count: int = 1
    last_modified: datetime | None = None
    priority: int = 0
    note: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "agent": self.agent.value,
            "kind": self.kind.value,
            "format": self.format.value,
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "file_count": self.file_count,
            "last_modified": (
                self.last_modified.isoformat() if self.last_modified is not None else None
            ),
            "priority": self.priority,
            "note": self.note,
        }


@dataclass(frozen=True)
class RecognitionResult:
    sources: tuple[AgentMemorySource, ...]
    scanned_roots: tuple[str, ...]

    def by_agent(self) -> dict[str, list[AgentMemorySource]]:
        grouped: dict[str, list[AgentMemorySource]] = defaultdict(list)
        for source in self.sources:
            grouped[source.agent.value].append(source)
        return dict(grouped)

    def as_dict(self) -> dict[str, object]:
        by_agent = self.by_agent()
        return {
            "items": [source.as_dict() for source in self.sources],
            "scanned_roots": list(self.scanned_roots),
            "summary": {
                "source_count": len(self.sources),
                "agent_count": len(by_agent),
                "total_size_bytes": sum(s.size_bytes for s in self.sources),
                "by_agent": {agent: len(items) for agent, items in by_agent.items()},
            },
        }
