from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from sqlalchemy import select

from personalmemory.application.ingestion import IngestionService, content_hash, new_id
from personalmemory.application.projection import ProjectionService
from personalmemory.application.transcript import TranscriptIngestionService
from personalmemory.domain.connector.profile import BUILTIN_PROFILES, get_builtin_profile
from personalmemory.infrastructure.database.models import (
    ConnectorInstanceRecord,
    ConnectorIssueRecord,
    LoopGuardRecord,
    utc_now,
)
from personalmemory.infrastructure.database.session import Database
from personalmemory.infrastructure.filesystem.adapters import (
    ADAPTER_FORMATS,
    decode_agent_memory,
    decode_continuity_transcript,
)
from personalmemory.infrastructure.filesystem.writer import (
    ManagedBlockMissingError,
    ProjectionConflictError,
    ProjectionVerificationError,
    hash_bytes,
)


@dataclass(frozen=True)
class SyncResult:
    ingested: int = 0
    projected: int = 0
    failed: int = 0
    accepted: int = 0


@dataclass(frozen=True)
class RuntimeControl:
    sync_paused: bool = False
    privacy_mode: bool = False


class SyncCoordinator:
    def __init__(
        self,
        database: Database,
        *,
        poll_interval: float = 1.0,
        settings=None,
    ) -> None:
        self.database = database
        self.poll_interval = poll_interval
        self.settings = settings
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._file_signatures: dict[str, tuple[int, int]] = {}
        self._control_lock = RLock()
        self._runtime_control = RuntimeControl()
        self._projection_refresh_requested = True

    def runtime_control(self) -> RuntimeControl:
        with self._control_lock:
            return self._runtime_control

    def request_projection_refresh(self) -> None:
        with self._control_lock:
            self._projection_refresh_requested = True

    def update_runtime_control(
        self,
        *,
        sync_paused: bool | None = None,
        privacy_mode: bool | None = None,
    ) -> RuntimeControl:
        with self._control_lock:
            previous = self._runtime_control
            updated = RuntimeControl(
                sync_paused=(previous.sync_paused if sync_paused is None else sync_paused),
                privacy_mode=(previous.privacy_mode if privacy_mode is None else privacy_mode),
            )
            if previous.privacy_mode and not updated.privacy_mode:
                self._projection_refresh_requested = True
            self._runtime_control = updated
            return updated

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="personalmemory-sync")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.to_thread(self.sync_all_once)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval)
            except TimeoutError:
                continue

    def sync_all_once(self, *, force: bool = False) -> SyncResult:
        with self._control_lock:
            control = self._runtime_control
            if control.sync_paused and not force:
                return SyncResult()
            refresh_projections = self._projection_refresh_requested and not control.privacy_mode
            if refresh_projections:
                self._projection_refresh_requested = False

        with self.database.session_factory() as session:
            readable = session.scalars(
                select(ConnectorInstanceRecord).where(
                    ConnectorInstanceRecord.enabled.is_(True),
                    ConnectorInstanceRecord.can_read.is_(True),
                    ConnectorInstanceRecord.read_enabled.is_(True),
                    ConnectorInstanceRecord.profile_id.not_in(
                        [
                            profile_id
                            for profile_id, profile in BUILTIN_PROFILES.items()
                            if profile.capabilities.observe_via_hook
                        ]
                    ),
                )
            ).all()
            readable_ids = [
                connector.id
                for connector in readable
                if get_builtin_profile(connector.profile_id).watch_source_changes
            ]

        changed_spaces: set[str] = set()
        ingested = 0
        failed = 0
        accepted = 0
        for connector_id in readable_ids:
            try:
                changed, memory_space_id, added = self._sync_source(connector_id)
                self._resolve_issues(connector_id, "source")
                if changed:
                    ingested += 1
                    accepted += added
                    changed_spaces.add(memory_space_id)
            except (OSError, UnicodeError, ValueError, PermissionError) as error:
                failed += 1
                self._record_issue(connector_id, "source", error)
                self._set_status(connector_id, "attention")

        if control.privacy_mode:
            if changed_spaces:
                with self._control_lock:
                    self._projection_refresh_requested = True
            return SyncResult(ingested=ingested, failed=failed, accepted=accepted)

        projected = 0
        if changed_spaces or refresh_projections:
            with self.database.session_factory() as session:
                query = select(ConnectorInstanceRecord.id).where(
                    ConnectorInstanceRecord.enabled.is_(True),
                    ConnectorInstanceRecord.can_write.is_(True),
                    ConnectorInstanceRecord.write_enabled.is_(True),
                )
                if not refresh_projections:
                    query = query.where(ConnectorInstanceRecord.memory_space_id.in_(changed_spaces))
                writable_ids = session.scalars(query).all()
            for connector_id in writable_ids:
                try:
                    result = ProjectionService(self.database).project(connector_id)
                    if not result.duplicate:
                        projected += 1
                    self._resolve_issues(connector_id, "projection")
                    self._set_status(connector_id, "healthy")
                except (
                    OSError,
                    UnicodeError,
                    ValueError,
                    PermissionError,
                    RuntimeError,
                ) as error:
                    failed += 1
                    self._record_issue(connector_id, "projection", error)
                    self._set_status(connector_id, "attention")
                    with self._control_lock:
                        self._projection_refresh_requested = True

        return SyncResult(ingested=ingested, projected=projected, failed=failed, accepted=accepted)

    def forget_connector(self, connector_id: str) -> None:
        """连接器被移除后清理内存中的文件签名缓存。"""
        with self._control_lock:
            self._file_signatures.pop(connector_id, None)

    def sync_connector(self, connector_id: str) -> SyncResult:
        """Manually sync one source and its relevant managed projections."""
        with self.database.session_factory() as session:
            connector = session.get(ConnectorInstanceRecord, connector_id)
            if connector is None:
                raise ValueError(f"Unknown connector: {connector_id}")
            if not connector.enabled:
                raise PermissionError(f"Connector is disabled: {connector_id}")
            if get_builtin_profile(connector.profile_id).capabilities.observe_via_hook:
                raise PermissionError(f"Hook connector cannot be polled: {connector_id}")
            can_read = connector.can_read and connector.read_enabled
            can_write = connector.can_write and connector.write_enabled
            memory_space_id = connector.memory_space_id
            if not can_read and not can_write:
                raise PermissionError(f"Connector has no enabled sync direction: {connector_id}")

        changed = False
        accepted = 0
        ingested = 0
        if can_read:
            try:
                changed, memory_space_id, accepted = self._sync_source(connector_id)
                self._resolve_issues(connector_id, "source")
                ingested = 1 if changed else 0
            except (OSError, UnicodeError, ValueError, PermissionError) as error:
                self._record_issue(connector_id, "source", error)
                self._set_status(connector_id, "attention")
                return SyncResult(failed=1)

        with self.database.session_factory() as session:
            writable_ids = session.scalars(
                select(ConnectorInstanceRecord.id).where(
                    ConnectorInstanceRecord.enabled.is_(True),
                    ConnectorInstanceRecord.can_write.is_(True),
                    ConnectorInstanceRecord.write_enabled.is_(True),
                    ConnectorInstanceRecord.memory_space_id == memory_space_id,
                )
            ).all()
        # A write-only connector is itself the requested operation. A readable
        # source refreshes managed outputs only when it actually changed.
        should_project = can_write or changed
        projected = 0
        failed = 0
        if should_project:
            for target_id in writable_ids:
                try:
                    result = ProjectionService(self.database).project(target_id)
                    if not result.duplicate:
                        projected += 1
                    self._resolve_issues(target_id, "projection")
                    self._set_status(target_id, "healthy")
                except (OSError, UnicodeError, ValueError, RuntimeError) as error:
                    self._record_issue(target_id, "projection", error)
                    self._set_status(target_id, "attention")
                    failed += 1
        if not failed:
            self._set_status(connector_id, "healthy")
        return SyncResult(
            ingested=ingested,
            projected=projected,
            failed=failed,
            accepted=accepted,
        )

    def _sync_source(self, connector_id: str) -> tuple[bool, str, int]:
        with self.database.session_factory() as session:
            connector = session.get(ConnectorInstanceRecord, connector_id)
            if connector is None:
                raise ValueError(f"Unknown connector: {connector_id}")
            if not connector.source_path:
                raise ValueError(f"Connector has no source path: {connector_id}")
            source_path = Path(connector.source_path)
            memory_space_id = connector.memory_space_id
            profile = get_builtin_profile(connector.profile_id)
            if profile.capabilities.observe_via_hook:
                raise PermissionError(f"Hook connector cannot be polled: {connector_id}")
            last_source_hash = connector.last_source_hash

        stat = source_path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        if self._file_signatures.get(connector_id) == signature:
            return False, memory_space_id, 0

        raw_content = source_path.read_bytes()
        raw_digest = hash_bytes(raw_content)
        transcript = None
        if profile.profile_id in {"transcript_json", "transcript_jsonl"}:
            transcript = decode_continuity_transcript(
                source_path,
                format_name=profile.format,
                source_family=profile.agent_family,
            )
            content = transcript.normalized_content()
        elif profile.format in ADAPTER_FORMATS:
            content = decode_agent_memory(source_path, profile.format)
        else:
            content = raw_content.decode(profile.encoding)
        digest = content_hash(content)
        if digest == last_source_hash:
            self._file_signatures[connector_id] = signature
            self._set_status(connector_id, "healthy")
            return False, memory_space_id, 0

        with self.database.session_factory() as session:
            guarded = session.scalar(
                select(LoopGuardRecord.id).where(
                    LoopGuardRecord.connector_id == connector_id,
                    LoopGuardRecord.content_hash == raw_digest,
                )
            )
        if guarded is not None:
            with self.database.session_factory.begin() as session:
                connector = session.get(ConnectorInstanceRecord, connector_id)
                connector.last_source_hash = digest
                connector.status = "healthy"
            self._file_signatures[connector_id] = signature
            return False, memory_space_id, 0

        self._set_status(connector_id, "syncing")
        if transcript is not None:
            transcript_result = TranscriptIngestionService(
                self.database, settings=self.settings
            ).ingest_transcript(connector_id, transcript)
            counts = transcript_result.counts
            changed = transcript_result.envelopes > transcript_result.duplicate_envelopes
            with self.database.session_factory.begin() as session:
                connector = session.get(ConnectorInstanceRecord, connector_id)
                if connector is not None:
                    connector.last_source_hash = digest
        else:
            result = IngestionService(self.database, settings=self.settings).ingest(
                connector_id, source_path, content
            )
            counts = result.counts
            changed = not result.duplicate
        self._file_signatures[connector_id] = signature
        self._set_status(connector_id, "healthy")
        accepted = (
            counts.get("accepted", 0) + counts.get("provisional", 0) + counts.get("restricted", 0)
        )
        return changed, memory_space_id, accepted

    def _set_status(self, connector_id: str, status: str) -> None:
        with self.database.session_factory.begin() as session:
            connector = session.get(ConnectorInstanceRecord, connector_id)
            if connector is not None:
                connector.status = status

    def _record_issue(self, connector_id: str, kind: str, error: Exception) -> None:
        reason_code = self._issue_reason_code(error, kind)
        with self.database.session_factory.begin() as session:
            existing = session.scalar(
                select(ConnectorIssueRecord).where(
                    ConnectorIssueRecord.connector_id == connector_id,
                    ConnectorIssueRecord.kind == kind,
                    ConnectorIssueRecord.status == "open",
                )
            )
            if existing is None:
                session.add(
                    ConnectorIssueRecord(
                        id=new_id("issue"),
                        connector_id=connector_id,
                        kind=kind,
                        reason_code=reason_code,
                        status="open",
                    )
                )
            else:
                existing.reason_code = reason_code

    def _resolve_issues(self, connector_id: str, kind: str) -> None:
        with self.database.session_factory.begin() as session:
            issues = session.scalars(
                select(ConnectorIssueRecord).where(
                    ConnectorIssueRecord.connector_id == connector_id,
                    ConnectorIssueRecord.kind == kind,
                    ConnectorIssueRecord.status == "open",
                )
            ).all()
            for issue in issues:
                issue.status = "resolved"
                issue.resolved_at = utc_now()

    @staticmethod
    def _issue_reason_code(error: Exception, kind: str) -> str:
        if isinstance(error, ProjectionConflictError):
            return "PROJECTION_CONFLICT"
        if isinstance(error, ManagedBlockMissingError):
            return "MANAGED_BLOCK_MISSING"
        if isinstance(error, ProjectionVerificationError):
            return "PROJECTION_VERIFICATION_FAILED"
        if isinstance(error, PermissionError):
            return "FILE_ACCESS_DENIED"
        if isinstance(error, UnicodeError):
            return "ENCODING_ERROR"
        if isinstance(error, OSError):
            return "FILE_IO_ERROR"
        if isinstance(error, ValueError):
            return "CONNECTOR_CONFIGURATION_ERROR"
        return f"{kind.upper()}_FAILED"
