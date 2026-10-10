"""One-click import: turn recognized agent memory files into connectors + memories.

Supports a single file or a directory. Directory imports cap the number of
files and skip oversized sources with an explicit per-file status, so the
user always sees what happened instead of a silent partial failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from personalmemory.application.ingestion import new_id
from personalmemory.domain.connector.profile import (
    AgentMemoryProfile,
    get_builtin_profile,
)
from personalmemory.infrastructure.database.models import ConnectorInstanceRecord
from personalmemory.infrastructure.database.session import Database

MAX_FILES_PER_IMPORT = 50

_EXTENSION_MAP = {
    "jsonl": ("*.jsonl",),
    "json": ("*.json",),
    "sqlite": ("*.db",),
    "markdown": ("*.md",),
    "conversation": ("*.txt", "*.md"),
}


@dataclass(frozen=True)
class ImportFileResult:
    path: str
    status: str  # ingested | duplicate | skipped | failed
    detail: str = ""


@dataclass(frozen=True)
class ImportResult:
    total: int
    ingested: int
    duplicate: int
    files: list[ImportFileResult]
    processed: int = 0
    skipped: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "ingested": self.ingested,
            "duplicate": self.duplicate,
            "processed": self.processed,
            "skipped": self.skipped,
            "failed": self.failed,
            "files": [
                {"path": item.path, "status": item.status, "detail": item.detail}
                for item in self.files
            ],
        }


class ImportService:
    def __init__(self, database: Database, sync_coordinator) -> None:
        self.database = database
        self.sync_coordinator = sync_coordinator

    def import_source(
        self,
        profile_id: str,
        source_path: str,
        namespace: str,
    ) -> ImportResult:
        profile = get_builtin_profile(profile_id)
        if not profile.capabilities.read_memory_file:
            raise PermissionError(f"Profile cannot read: {profile_id}")

        path = Path(source_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Source path not found: {path}")

        files = [path] if path.is_file() else self._collect_files(path, profile.format)
        results: list[ImportFileResult] = []
        ingested = duplicate = processed = skipped = failed = 0
        for file_path in files:
            try:
                if file_path.stat().st_size > profile.max_bytes:
                    skipped += 1
                    results.append(
                        ImportFileResult(
                            str(file_path), "skipped", "source exceeds profile size limit"
                        )
                    )
                    continue
                connector_id = self._ensure_connector(profile, file_path, namespace)
                sync_result = self.sync_coordinator.sync_connector(connector_id)
                new_memories = sync_result.accepted
                if new_memories > 0:
                    ingested += 1
                    results.append(
                        ImportFileResult(
                            str(file_path), "ingested", f"{new_memories} new memories"
                        )
                    )
                elif sync_result.ingested > 0:
                    processed += 1
                    results.append(
                        ImportFileResult(
                            str(file_path), "processed", "0 new memories extracted"
                        )
                    )
                else:
                    duplicate += 1
                    results.append(ImportFileResult(str(file_path), "duplicate"))
            except (OSError, UnicodeError, ValueError, PermissionError) as error:
                failed += 1
                results.append(ImportFileResult(str(file_path), "failed", str(error)))
        return ImportResult(
            total=len(files),
            ingested=ingested,
            duplicate=duplicate,
            processed=processed,
            skipped=skipped,
            failed=failed,
            files=results,
        )

    def _collect_files(self, directory: Path, format_name: str) -> list[Path]:
        patterns = _EXTENSION_MAP.get(format_name)
        if not patterns:
            raise ValueError(f"Directory import unsupported for format: {format_name}")
        files: list[Path] = []
        for pattern in patterns:
            for path in directory.rglob(pattern):
                if path.is_file():
                    files.append(path)
        files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        return files[:MAX_FILES_PER_IMPORT]

    def _ensure_connector(
        self,
        profile: AgentMemoryProfile,
        source_path: Path,
        namespace: str,
    ) -> str:
        resolved = str(source_path.resolve())
        with self.database.session_factory() as session:
            existing = session.scalar(
                select(ConnectorInstanceRecord).where(
                    ConnectorInstanceRecord.profile_id == profile.profile_id,
                    ConnectorInstanceRecord.source_path == resolved,
                )
            )
            if existing is not None:
                return existing.id
        with self.database.session_factory.begin() as session:
            connector = ConnectorInstanceRecord(
                id=new_id("connector"),
                memory_space_id="space_default",
                profile_id=profile.profile_id,
                name=f"{profile.agent_family} 记忆导入",
                source_path=resolved,
                target_path=None,
                namespace=namespace or profile.agent_family,
                can_read=profile.capabilities.read_memory_file,
                can_write=profile.capabilities.write_memory_file,
                read_enabled=True,
                write_enabled=False,
            )
            session.add(connector)
            session.flush()
            return connector.id
