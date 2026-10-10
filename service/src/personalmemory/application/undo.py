from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select

from personalmemory.application.ingestion import new_id, semantic_fingerprint
from personalmemory.application.projection import ProjectionService
from personalmemory.domain.memory.fingerprint import exact_content_fingerprint
from personalmemory.domain.memory.model import MemoryDraft
from personalmemory.infrastructure.database.models import (
    ActivityRecord,
    ConnectorInstanceRecord,
    ConnectorIssueRecord,
    MemoryCandidateRecord,
    MemoryObjectRecord,
    MemoryRevisionRecord,
    OperationEffectRecord,
    TombstoneRecord,
    utc_now,
)
from personalmemory.infrastructure.database.session import Database
from personalmemory.infrastructure.filesystem.writer import (
    ManagedBlockMissingError,
    ProjectionConflictError,
    ProjectionVerificationError,
)


class RevisionConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class UndoResult:
    undone: int
    operation_id: str


class MemoryOperations:
    def __init__(self, database: Database) -> None:
        self.database = database

    def revise(
        self,
        memory_id: str,
        *,
        expected_revision: int,
        statement: str,
        reason: str | None,
    ) -> MemoryObjectRecord:
        operation_id = new_id("operation")
        with self.database.session_factory.begin() as session:
            memory = session.get(MemoryObjectRecord, memory_id)
            if memory is None:
                raise KeyError(memory_id)
            if memory.revision != expected_revision:
                raise RevisionConflictError(memory_id)

            old_fingerprint = memory.semantic_fingerprint
            session.add(
                TombstoneRecord(
                    id=new_id("tombstone"),
                    memory_space_id=memory.memory_space_id,
                    object_id=memory.id,
                    object_kind="superseded_revision",
                    semantic_fingerprint=old_fingerprint,
                )
            )
            draft = MemoryDraft(
                type=memory.type,
                statement=statement,
                reason=reason,
                source_revision_id="user_revision",
                confidence=1.0,
                importance=0.8,
                namespace=memory.namespace,
                payload=memory.payload,
            )
            memory.statement = draft.statement
            memory.reason = draft.reason
            memory.semantic_fingerprint = semantic_fingerprint(draft)
            memory.exact_content_fingerprint = exact_content_fingerprint(
                memory.type, memory.statement
            )
            quality_issues = memory.payload.get("quality_issues")
            if isinstance(quality_issues, list):
                payload = dict(memory.payload)
                payload["quality_issues"] = [
                    issue
                    for issue in quality_issues
                    if not isinstance(issue, dict)
                    or issue.get("code") != "CROSS_NAMESPACE_EXACT_DUPLICATE"
                ]
                memory.payload = payload
            memory.epistemic_state = "asserted"
            memory.revision += 1
            snapshot = self._snapshot(memory)
            session.add(
                MemoryRevisionRecord(
                    id=new_id("revision"),
                    memory_id=memory.id,
                    revision=memory.revision,
                    snapshot=snapshot,
                    actor_kind="user",
                )
            )
            session.add(
                ActivityRecord(
                    id=new_id("activity"),
                    operation_id=operation_id,
                    connector_id=None,
                    kind="memory_revised",
                    status="completed",
                    summary="用户纠正 1 条记忆",
                    details={
                        "memory_id": memory.id,
                        "revision": memory.revision,
                        "memory_changes": [
                            {
                                "id": memory.id,
                                "action": "revised",
                                "type": memory.type,
                                "statement": memory.statement,
                            }
                        ],
                    },
                )
            )
            session.flush()
            session.refresh(memory)
            session.expunge(memory)

        self._refresh_projections()
        return memory

    def delete(self, memory_id: str) -> None:
        operation_id = new_id("operation")
        with self.database.session_factory.begin() as session:
            memory = session.get(MemoryObjectRecord, memory_id)
            if memory is None:
                raise KeyError(memory_id)
            statement = memory.statement
            memory_type = memory.type
            session.add(
                TombstoneRecord(
                    id=new_id("tombstone"),
                    memory_space_id=memory.memory_space_id,
                    object_id=memory.id,
                    object_kind="memory",
                    semantic_fingerprint=memory.semantic_fingerprint,
                )
            )
            candidates = session.scalars(select(MemoryCandidateRecord)).all()
            for candidate in candidates:
                if candidate.draft and candidate.draft.get("statement") == statement:
                    session.delete(candidate)
            session.execute(
                delete(OperationEffectRecord).where(
                    OperationEffectRecord.object_id == memory.id
                )
            )
            session.execute(
                delete(MemoryRevisionRecord).where(
                    MemoryRevisionRecord.memory_id == memory.id
                )
            )
            session.delete(memory)
            session.add(
                ActivityRecord(
                    id=new_id("activity"),
                    operation_id=operation_id,
                    connector_id=None,
                    kind="memory_deleted",
                    status="completed",
                    summary="用户删除 1 条记忆",
                    details={
                        "deleted": 1,
                        "memory_changes": [
                            {
                                "id": memory_id,
                                "action": "deleted",
                                "type": memory_type,
                                "statement": statement,
                            }
                        ],
                    },
                )
            )

        self._refresh_projections()

    def undo(self, operation_id: str) -> UndoResult:
        undo_operation_id = new_id("operation")
        undone = 0
        with self.database.session_factory.begin() as session:
            effects = session.scalars(
                select(OperationEffectRecord).where(
                    OperationEffectRecord.operation_id == operation_id,
                    OperationEffectRecord.effect_kind == "created",
                )
            ).all()
            for effect in effects:
                memory = session.get(MemoryObjectRecord, effect.object_id)
                if memory is None:
                    continue
                session.add(
                    TombstoneRecord(
                        id=new_id("tombstone"),
                        memory_space_id=memory.memory_space_id,
                        object_id=memory.id,
                        object_kind="undo",
                        semantic_fingerprint=memory.semantic_fingerprint,
                    )
                )
                session.execute(
                    delete(MemoryRevisionRecord).where(
                        MemoryRevisionRecord.memory_id == memory.id
                    )
                )
                session.delete(memory)
                session.delete(effect)
                undone += 1
            session.add(
                ActivityRecord(
                    id=new_id("activity"),
                    operation_id=undo_operation_id,
                    connector_id=None,
                    kind="operation_undone",
                    status="completed",
                    summary=f"撤销自动操作，移除 {undone} 条记忆",
                    details={"undone": undone, "target_operation_id": operation_id},
                )
            )

        self._refresh_projections()
        return UndoResult(undone=undone, operation_id=undo_operation_id)

    def _refresh_projections(self) -> None:
        with self.database.session_factory() as session:
            connector_ids = session.scalars(
                select(ConnectorInstanceRecord.id).where(
                    ConnectorInstanceRecord.enabled.is_(True),
                    ConnectorInstanceRecord.can_write.is_(True),
                    ConnectorInstanceRecord.write_enabled.is_(True),
                )
            ).all()
        for connector_id in connector_ids:
            try:
                ProjectionService(self.database).project(connector_id)
                self._resolve_issues(connector_id, "projection")
                self._set_status(connector_id, "healthy")
            except (
                OSError,
                UnicodeError,
                ValueError,
                PermissionError,
                RuntimeError,
            ) as error:
                self._record_issue(connector_id, "projection", error)
                self._set_status(connector_id, "attention")

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

    def _set_status(self, connector_id: str, status: str) -> None:
        with self.database.session_factory.begin() as session:
            connector = session.get(ConnectorInstanceRecord, connector_id)
            if connector is not None:
                connector.status = status

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

    @staticmethod
    def _snapshot(memory: MemoryObjectRecord) -> dict[str, object]:
        return {
            "id": memory.id,
            "type": memory.type,
            "statement": memory.statement,
            "reason": memory.reason,
            "namespace": memory.namespace,
            "sensitivity": memory.sensitivity,
            "epistemic_state": memory.epistemic_state,
            "status": memory.status,
            "exact_content_fingerprint": memory.exact_content_fingerprint,
            "payload": memory.payload,
            "revision": memory.revision,
        }
