from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class MemorySpaceRecord(Base):
    __tablename__ = "memory_spaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ConnectorInstanceRecord(Base):
    __tablename__ = "connector_instances"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    memory_space_id: Mapped[str] = mapped_column(ForeignKey("memory_spaces.id"), nullable=False)
    profile_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    source_path: Mapped[str | None] = mapped_column(Text)
    target_path: Mapped[str | None] = mapped_column(Text)
    namespace: Mapped[str] = mapped_column(String(240), default="general")
    can_read: Mapped[bool] = mapped_column(Boolean, default=True)
    can_write: Mapped[bool] = mapped_column(Boolean, default=False)
    read_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    write_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="healthy")
    last_source_hash: Mapped[str | None] = mapped_column(String(64))
    last_target_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ConnectorIssueRecord(Base):
    __tablename__ = "connector_issues"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    connector_id: Mapped[str] = mapped_column(
        ForeignKey("connector_instances.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceRevisionRecord(Base):
    __tablename__ = "source_revisions"
    __table_args__ = (UniqueConstraint("connector_id", "content_hash"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    connector_id: Mapped[str] = mapped_column(
        ForeignKey("connector_instances.id"), nullable=False
    )
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EnvelopeIdempotencyRecord(Base):
    __tablename__ = "envelope_idempotency"
    __table_args__ = (UniqueConstraint("connector_id", "idempotency_key"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    connector_id: Mapped[str] = mapped_column(
        ForeignKey("connector_instances.id"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_revision_id: Mapped[str | None] = mapped_column(String(64))
    operation_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AgentTurnObservationRecord(Base):
    __tablename__ = "agent_turn_observations"

    id: Mapped[str] = mapped_column(String(68), primary_key=True)
    connector_id: Mapped[str] = mapped_column(
        ForeignKey("connector_instances.id"), nullable=False, index=True
    )
    agent_family: Mapped[str] = mapped_column(String(40), nullable=False)
    session_id: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    prompt_id: Mapped[str] = mapped_column(String(180), nullable=False)
    cwd: Mapped[str] = mapped_column(Text, nullable=False)
    transcript_path: Mapped[str] = mapped_column(Text, nullable=False)
    user_text: Mapped[str | None] = mapped_column(Text)
    assistant_text: Mapped[str | None] = mapped_column(Text)
    user_hash: Mapped[str | None] = mapped_column(String(64))
    assistant_hash: Mapped[str | None] = mapped_column(String(64))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    source_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_revisions.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MemoryCandidateRecord(Base):
    __tablename__ = "memory_candidates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_revision_id: Mapped[str] = mapped_column(
        ForeignKey("source_revisions.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    draft: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MemoryObjectRecord(Base):
    __tablename__ = "memory_objects"
    __table_args__ = (UniqueConstraint("memory_space_id", "semantic_fingerprint"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    memory_space_id: Mapped[str] = mapped_column(ForeignKey("memory_spaces.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    namespace: Mapped[str] = mapped_column(String(240), nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(32), nullable=False)
    epistemic_state: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active")
    semantic_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    exact_content_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    semantic_key: Mapped[str | None] = mapped_column(String(240), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class MemoryRevisionRecord(Base):
    __tablename__ = "memory_revisions"
    __table_args__ = (UniqueConstraint("memory_id", "revision"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memory_objects.id"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MemoryRelationRecord(Base):
    __tablename__ = "memory_relations"
    __table_args__ = (
        UniqueConstraint(
            "memory_space_id", "source_memory_id", "target_memory_id", "relation_type"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    memory_space_id: Mapped[str] = mapped_column(
        ForeignKey("memory_spaces.id"), nullable=False, index=True
    )
    source_memory_id: Mapped[str] = mapped_column(
        ForeignKey("memory_objects.id"), nullable=False, index=True
    )
    target_memory_id: Mapped[str] = mapped_column(
        ForeignKey("memory_objects.id"), nullable=False, index=True
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    validity_state: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ActivityRecord(Base):
    __tablename__ = "activities"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    connector_id: Mapped[str | None] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(String(240), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OperationEffectRecord(Base):
    __tablename__ = "operation_effects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    object_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    effect_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    semantic_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProjectionReceiptRecord(Base):
    __tablename__ = "projection_receipts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    connector_id: Mapped[str] = mapped_column(
        ForeignKey("connector_instances.id"), nullable=False
    )
    projection_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    target_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_revision_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TombstoneRecord(Base):
    __tablename__ = "tombstones"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    memory_space_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_spaces.id"), index=True
    )
    object_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    object_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    semantic_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LoopGuardRecord(Base):
    __tablename__ = "loop_guard_records"
    __table_args__ = (UniqueConstraint("connector_id", "content_hash"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    connector_id: Mapped[str] = mapped_column(
        ForeignKey("connector_instances.id"), nullable=False
    )
    projection_id: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
