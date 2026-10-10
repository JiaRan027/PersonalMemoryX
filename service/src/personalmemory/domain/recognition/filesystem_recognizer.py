from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from personalmemory.domain.recognition.models import (
    AgentKind,
    AgentMemorySource,
    RecognitionResult,
    SourceFormat,
    SourceKind,
)

_SKIP_DIR_PARTS = {".git", "PersonalMemoryData"}


@dataclass(frozen=True)
class DiscoveryRule:
    agent: AgentKind
    kind: SourceKind
    format: SourceFormat
    dir_relative: str
    file_glob: str | None = None
    aggregate: bool = False
    group_by: str = "parent"
    priority: int = 0
    note: str = ""
    max_files: int = 2000


DEFAULT_RULES = (
    DiscoveryRule(
        AgentKind.CODEX,
        SourceKind.MEMORY_NOTE,
        SourceFormat.MARKDOWN,
        "{home}/.codex/memories",
        "*.md",
        priority=100,
        note="Curated memory notes",
    ),
    DiscoveryRule(
        AgentKind.CODEX,
        SourceKind.SESSION_STORE,
        SourceFormat.JSONL,
        "{home}/.codex/sessions",
        "**/*.jsonl",
        aggregate=True,
        group_by="base",
        priority=10,
        note="Session transcripts",
    ),
    DiscoveryRule(
        AgentKind.CLAUDE_CODE,
        SourceKind.CONVERSATION_LOG,
        SourceFormat.JSONL,
        "{home}/.claude",
        "history.jsonl",
        priority=90,
        note="Command history log",
    ),
    DiscoveryRule(
        AgentKind.CLAUDE_CODE,
        SourceKind.MEMORY_NOTE,
        SourceFormat.MARKDOWN,
        "{home}/.claude/projects",
        "*/memory/*.md",
        aggregate=True,
        priority=80,
        note="Per-project memory notes",
    ),
    DiscoveryRule(
        AgentKind.CLAUDE_DESKTOP,
        SourceKind.STATE_STORE,
        SourceFormat.LEVELDB,
        "{appdata}/Claude/Local Storage/leveldb",
        None,
        priority=40,
        note="LevelDB store; decoder required",
    ),
    DiscoveryRule(
        AgentKind.GEMINI_CLI,
        SourceKind.SESSION_STORE,
        SourceFormat.SQLITE,
        "{home}/.gemini/antigravity/conversations",
        "*.db",
        aggregate=True,
        group_by="base",
        priority=50,
        note="SQLite conversation store",
    ),
    DiscoveryRule(
        AgentKind.GEMINI_CLI,
        SourceKind.SESSION_STORE,
        SourceFormat.PROTOBUF,
        "{home}/.gemini/antigravity/conversations",
        "*.pb",
        aggregate=True,
        group_by="base",
        priority=20,
        note="Protobuf conversation store",
    ),
    DiscoveryRule(
        AgentKind.CURSOR,
        SourceKind.STATE_STORE,
        SourceFormat.SQLITE,
        "{home}/.cursor/ai-tracking",
        "*.db",
        priority=60,
        note="Code tracking database",
    ),
)


def _default_roots() -> dict[str, Path]:
    roots: dict[str, Path] = {"home": Path.home()}
    for key, env_name in (("appdata", "APPDATA"), ("localappdata", "LOCALAPPDATA")):
        value = os.environ.get(env_name)
        if value:
            roots[key] = Path(value)
    return roots


def _should_skip(path: Path) -> bool:
    lowered = {part.casefold() for part in path.parts}
    return bool(lowered & _SKIP_DIR_PARTS)


class FilesystemAgentMemoryRecognizer:
    """Scans well-known local agent data directories and classifies memory sources.

    Read-only by design: it never opens, writes or moves agent files. Opaque
    formats (leveldb / protobuf) are reported with a format tag so a later
    decoding step can attach to them.
    """

    def __init__(
        self,
        roots: dict[str, Path | str] | None = None,
        rules: tuple[DiscoveryRule, ...] = DEFAULT_RULES,
    ) -> None:
        if roots is None:
            self.roots = _default_roots()
        else:
            self.roots = {key: Path(value) for key, value in roots.items()}
        self.rules = rules

    def recognize(self) -> RecognitionResult:
        sources: list[AgentMemorySource] = []
        scanned: list[str] = []
        for rule in self.rules:
            parts = rule.dir_relative.split("/", 1)
            root_name = parts[0]
            root = self.roots.get(root_name) or self.roots.get(root_name.strip("{}"))
            if root is None:
                continue
            base = root / parts[1] if len(parts) == 2 else root
            scanned.append(str(base))
            self._apply_rule(rule, base, sources)
        scanned.sort()
        sources.sort(key=lambda s: (s.agent.value, -s.priority, str(s.path)))
        return RecognitionResult(
            sources=tuple(sources),
            scanned_roots=tuple(scanned),
        )

    def _apply_rule(
        self,
        rule: DiscoveryRule,
        base: Path,
        sources: list[AgentMemorySource],
    ) -> None:
        if not base.exists():
            return
        if rule.file_glob is None:
            self._emit_directory_source(rule, base, sources)
            return
        matched = self._collect(base, rule.file_glob, rule.max_files)
        if not matched:
            return
        if rule.aggregate:
            if rule.group_by == "base":
                sources.append(self._build_source(rule, base, matched))
            else:
                by_parent: dict[Path, list[Path]] = {}
                for path in matched:
                    by_parent.setdefault(path.parent, []).append(path)
                for parent, files in by_parent.items():
                    sources.append(self._build_source(rule, parent, files))
            return
        for path in matched:
            sources.append(self._build_source(rule, path, [path]))

    def _emit_directory_source(
        self,
        rule: DiscoveryRule,
        directory: Path,
        sources: list[AgentMemorySource],
    ) -> None:
        if not directory.is_dir():
            return
        files = [p for p in directory.iterdir() if p.is_file() and not _should_skip(p)]
        if files:
            sources.append(self._build_source(rule, directory, files))

    def _collect(self, base: Path, pattern: str, max_files: int) -> list[Path]:
        results: list[Path] = []
        iterator = base.rglob(pattern[3:]) if pattern.startswith("**/") else base.glob(pattern)
        for path in iterator:
            if _should_skip(path) or not path.is_file():
                continue
            results.append(path)
            if len(results) >= max_files:
                break
        return results

    @staticmethod
    def _build_source(
        rule: DiscoveryRule,
        path: Path,
        files: list[Path],
    ) -> AgentMemorySource:
        total = sum(p.stat().st_size for p in files)
        newest = max(p.stat().st_mtime for p in files)
        return AgentMemorySource(
            agent=rule.agent,
            kind=rule.kind,
            format=rule.format,
            path=path,
            size_bytes=total,
            file_count=len(files),
            last_modified=datetime.fromtimestamp(newest),
            priority=rule.priority,
            note=rule.note,
        )
