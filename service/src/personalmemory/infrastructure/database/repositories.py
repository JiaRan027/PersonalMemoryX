from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from personalmemory.infrastructure.database.models import (
    MemoryObjectRecord,
    SourceRevisionRecord,
    TombstoneRecord,
)


class MemoryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def source_revision_by_hash(
        self, connector_id: str, content_hash: str
    ) -> SourceRevisionRecord | None:
        return self.session.scalar(
            select(SourceRevisionRecord).where(
                SourceRevisionRecord.connector_id == connector_id,
                SourceRevisionRecord.content_hash == content_hash,
            )
        )

    def memory_by_fingerprint(
        self, memory_space_id: str, semantic_fingerprint: str
    ) -> MemoryObjectRecord | None:
        return self.memory_by_any_fingerprint(memory_space_id, (semantic_fingerprint,))

    def memory_by_any_fingerprint(
        self, memory_space_id: str, semantic_fingerprints: Collection[str]
    ) -> MemoryObjectRecord | None:
        if not semantic_fingerprints:
            return None
        return self.session.scalar(
            select(MemoryObjectRecord).where(
                MemoryObjectRecord.memory_space_id == memory_space_id,
                MemoryObjectRecord.semantic_fingerprint.in_(semantic_fingerprints),
                MemoryObjectRecord.status != "deleted",
            )
        )

    def cross_namespace_exact_match(
        self,
        memory_space_id: str,
        exact_content_fingerprint: str,
        namespace: str,
    ) -> MemoryObjectRecord | None:
        return self.session.scalar(
            select(MemoryObjectRecord)
            .where(
                MemoryObjectRecord.memory_space_id == memory_space_id,
                MemoryObjectRecord.exact_content_fingerprint
                == exact_content_fingerprint,
                MemoryObjectRecord.namespace != namespace,
                MemoryObjectRecord.status != "deleted",
            )
            .order_by(MemoryObjectRecord.created_at.asc(), MemoryObjectRecord.id.asc())
        )

    def slot_conflict(
        self,
        memory_space_id: str,
        semantic_key: str | None,
        semantic_fingerprint: str,
    ) -> MemoryObjectRecord | None:
        if semantic_key is None:
            return None
        return self.session.scalar(
            select(MemoryObjectRecord).where(
                MemoryObjectRecord.memory_space_id == memory_space_id,
                MemoryObjectRecord.semantic_key == semantic_key,
                MemoryObjectRecord.semantic_fingerprint != semantic_fingerprint,
                MemoryObjectRecord.status != "deleted",
            )
        )

    def inferred_identity_conflict(
        self,
        memory_space_id: str,
        namespace: str,
        semantic_fingerprint: str,
    ) -> MemoryObjectRecord | None:
        """Find an inferred identity claim that explicit user evidence may replace."""
        prefix = f"fact|{namespace.casefold()}|self_identity:"
        return self.session.scalar(
            select(MemoryObjectRecord)
            .where(
                MemoryObjectRecord.memory_space_id == memory_space_id,
                MemoryObjectRecord.semantic_key.startswith(prefix),
                MemoryObjectRecord.semantic_fingerprint != semantic_fingerprint,
                MemoryObjectRecord.epistemic_state == "inferred",
                MemoryObjectRecord.status != "deleted",
            )
            .order_by(MemoryObjectRecord.updated_at.desc(), MemoryObjectRecord.id.desc())
        )

    def is_tombstoned(self, memory_space_id: str, semantic_fingerprint: str) -> bool:
        return self.is_any_tombstoned(memory_space_id, (semantic_fingerprint,))

    def is_any_tombstoned(
        self, memory_space_id: str, semantic_fingerprints: Collection[str]
    ) -> bool:
        if not semantic_fingerprints:
            return False
        return (
            self.session.scalar(
                select(TombstoneRecord.id).where(
                    or_(
                        TombstoneRecord.memory_space_id == memory_space_id,
                        TombstoneRecord.memory_space_id.is_(None),
                    ),
                    TombstoneRecord.semantic_fingerprint.in_(semantic_fingerprints)
                )
            )
            is not None
        )
