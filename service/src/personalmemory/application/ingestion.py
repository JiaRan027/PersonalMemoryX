from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from personalmemory.application.candidate_governance import (
    draft_from_candidate,
    govern_source_draft,
    identity_semantic_slot,
)
from personalmemory.domain.connector.profile import get_builtin_profile
from personalmemory.domain.extraction import ExtractorPort, build_extractor
from personalmemory.domain.memory.fingerprint import (
    exact_content_fingerprint,
    legacy_semantic_fingerprint_value,
    semantic_fingerprint_value,
)
from personalmemory.domain.memory.model import MemoryDraft
from personalmemory.domain.memory.types import AdmissionAction, MemoryType
from personalmemory.domain.policy.admission import AutoAdmissionPolicy
from personalmemory.infrastructure.database.models import (
    ActivityRecord,
    ConnectorInstanceRecord,
    MemoryCandidateRecord,
    MemoryObjectRecord,
    MemoryRevisionRecord,
    OperationEffectRecord,
    SourceRevisionRecord,
    TombstoneRecord,
)
from personalmemory.infrastructure.database.repositories import MemoryRepository
from personalmemory.infrastructure.database.session import Database
from personalmemory.infrastructure.filesystem.adapters import ADAPTER_FORMATS
from personalmemory.infrastructure.filesystem.markdown import MarkdownMemoryParser

_IDENTITY_ROLE_PATTERN = re.compile(
    r"^(?:一名|一个|一位)?(?:程序员|开发者|工程师|学生|本科生|研究生|博士生|"
    r"老师|教师|设计师|产品经理|研究员|自由职业者|创业者|创作者)$"
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def semantic_fingerprint(draft: MemoryDraft) -> str:
    return semantic_fingerprint_value(draft.type, draft.namespace, draft.statement)


def tombstone_fingerprints(draft: MemoryDraft) -> tuple[str, ...]:
    current = semantic_fingerprint(draft)
    legacy = legacy_semantic_fingerprint_value(
        draft.type, draft.namespace, draft.statement
    )
    return (current,) if legacy == current else (current, legacy)


def semantic_key(draft: MemoryDraft) -> str | None:
    payload_slot = draft.payload.get("semantic_slot")
    if (
        draft.type == MemoryType.FACT
        and isinstance(payload_slot, str)
        and payload_slot.startswith("self_identity")
    ):
        return f"{draft.type.value}|{draft.namespace.casefold()}|{payload_slot}"
    if draft.type == MemoryType.FACT and _IDENTITY_ROLE_PATTERN.fullmatch(
        draft.statement.strip()
    ):
        slot = identity_semantic_slot(draft.statement)
        return f"{draft.type.value}|{draft.namespace.casefold()}|{slot}"
    if draft.type not in {
        MemoryType.FACT,
        MemoryType.GOAL,
        MemoryType.SKILL,
        MemoryType.PREFERENCE,
    }:
        return None
    match = re.match(r"^([^:：]{1,80})[:：]", draft.statement)
    if match is None:
        return None
    slot = " ".join(match.group(1).casefold().split())
    return f"{draft.type.value}|{draft.namespace.casefold()}|{slot}"


@dataclass(frozen=True)
class IngestionResult:
    source_revision_id: str
    operation_id: str
    duplicate: bool
    counts: dict[str, int]


class IngestionService:
    def __init__(self, database: Database, settings=None) -> None:
        self.database = database
        self.settings = settings
        self.policy = AutoAdmissionPolicy()

    def ingest(self, connector_id: str, source_path: Path, content: str) -> IngestionResult:
        return self.ingest_content(
            connector_id,
            source_locator=str(source_path.resolve()),
            content=content,
        )

    def ingest_content(
        self,
        connector_id: str,
        *,
        source_locator: str,
        content: str,
        source_digest: str | None = None,
        extractor: ExtractorPort | None = None,
        payload_metadata: dict[str, object] | None = None,
    ) -> IngestionResult:
        """Ingest normalized content from a file or an authorized inbound source."""
        digest = source_digest or content_hash(content)
        operation_id = new_id("operation")

        with self.database.session_factory.begin() as session:
            repository = MemoryRepository(session)
            existing_revision = repository.source_revision_by_hash(connector_id, digest)
            if existing_revision is not None:
                return IngestionResult(
                    source_revision_id=existing_revision.id,
                    operation_id=operation_id,
                    duplicate=True,
                    counts=self._empty_counts(),
                )

            connector = session.scalar(
                select(ConnectorInstanceRecord).where(ConnectorInstanceRecord.id == connector_id)
            )
            if connector is None:
                raise ValueError(f"Unknown connector: {connector_id}")
            if not connector.can_read:
                raise PermissionError(f"Connector cannot read sources: {connector_id}")

            source_revision_id = new_id("source")
            session.add(
                SourceRevisionRecord(
                    id=source_revision_id,
                    connector_id=connector_id,
                    source_path=source_locator,
                    content_hash=digest,
                    byte_size=len(content.encode("utf-8")),
                )
            )

            profile = get_builtin_profile(connector.profile_id)
            source_metadata = {
                **(payload_metadata or {}),
                "source_locator": source_locator,
                "source_digest": digest,
            }
            drafts = self._drafts_from_profile(
                profile,
                content,
                source_revision_id=source_revision_id,
                namespace=connector.namespace,
                extractor=extractor,
                payload_metadata=source_metadata,
            )
            counts = self._empty_counts()
            created_memory_ids: list[str] = []
            memory_changes: list[dict[str, str]] = []

            for draft in drafts:
                fingerprint = semantic_fingerprint(draft)
                exact_fingerprint = exact_content_fingerprint(draft.type, draft.statement)
                key = semantic_key(draft)
                cross_namespace_duplicate = repository.cross_namespace_exact_match(
                    connector.memory_space_id,
                    exact_fingerprint,
                    draft.namespace,
                )
                if cross_namespace_duplicate is not None:
                    payload = dict(draft.payload)
                    quality_issues = list(payload.get("quality_issues", []))
                    quality_issues.append(
                        {
                            "code": "CROSS_NAMESPACE_EXACT_DUPLICATE",
                            "related_memory_id": cross_namespace_duplicate.id,
                            "related_namespace": cross_namespace_duplicate.namespace,
                        }
                    )
                    payload["quality_issues"] = quality_issues
                    draft = draft.model_copy(update={"payload": payload})
                conflict = repository.slot_conflict(
                    connector.memory_space_id,
                    key,
                    fingerprint,
                )
                existing = repository.memory_by_any_fingerprint(
                    connector.memory_space_id, tombstone_fingerprints(draft)
                )
                replacement = self._explicit_user_replacement(
                    draft,
                    existing
                    or conflict
                    or (
                        repository.inferred_identity_conflict(
                            connector.memory_space_id,
                            draft.namespace,
                            fingerprint,
                        )
                        if self._is_explicit_user_identity(draft)
                        else None
                    ),
                )
                tombstoned = repository.is_any_tombstoned(
                    connector.memory_space_id, tombstone_fingerprints(draft)
                )
                decision = self.policy.evaluate(
                    draft,
                    has_conflict=conflict is not None and replacement is None,
                    has_cross_namespace_duplicate=cross_namespace_duplicate is not None,
                )
                if tombstoned:
                    decision = decision.model_copy(
                        update={
                            "action": AdmissionAction.DISCARD,
                            "reason_code": "TOMBSTONED_MEMORY",
                        }
                    )

                counts[self._count_key(decision.action)] += 1
                session.add(
                    MemoryCandidateRecord(
                        id=new_id("candidate"),
                        source_revision_id=source_revision_id,
                        action=decision.action.value,
                        reason_code=decision.reason_code,
                        status=self._candidate_status(decision.action),
                        draft=(
                            None
                            if decision.action == AdmissionAction.DISCARD
                            else draft.model_dump(mode="json")
                        ),
                    )
                )

                if decision.action not in {
                    AdmissionAction.ACCEPT,
                    AdmissionAction.PROVISIONAL,
                    AdmissionAction.RESTRICT,
                }:
                    continue
                if existing is not None and replacement is None:
                    continue

                if replacement is not None:
                    self._apply_replacement(
                        session,
                        replacement,
                        draft,
                        decision,
                        operation_id=operation_id,
                        actor_kind="user_evidence",
                    )
                    memory_changes.append(
                        {
                            "id": replacement.id,
                            "action": "revised",
                            "type": draft.type.value,
                            "statement": draft.statement,
                        }
                    )
                    continue

                memory_id = new_id("memory")
                memory = MemoryObjectRecord(
                    id=memory_id,
                    memory_space_id=connector.memory_space_id,
                    type=draft.type.value,
                    statement=draft.statement,
                    reason=draft.reason,
                    namespace=draft.namespace,
                    sensitivity=decision.sensitivity.value,
                    epistemic_state=decision.epistemic_state.value,
                    status="active",
                    semantic_fingerprint=fingerprint,
                    exact_content_fingerprint=exact_fingerprint,
                    semantic_key=key,
                    payload=draft.payload,
                    revision=1,
                )
                session.add(memory)
                created_memory_ids.append(memory_id)
                memory_changes.append(
                    {
                        "id": memory_id,
                        "action": "created",
                        "type": draft.type.value,
                        "statement": draft.statement,
                    }
                )
                session.add(
                    MemoryRevisionRecord(
                        id=new_id("revision"),
                        memory_id=memory_id,
                        revision=1,
                        snapshot=self._snapshot(memory),
                        actor_kind="auto_admission",
                    )
                )
                session.add(
                    OperationEffectRecord(
                        id=new_id("effect"),
                        operation_id=operation_id,
                        object_id=memory_id,
                        effect_kind="created",
                        semantic_fingerprint=fingerprint,
                    )
                )

            connector.last_source_hash = digest
            session.add(
                ActivityRecord(
                    id=new_id("activity"),
                    operation_id=operation_id,
                    connector_id=connector_id,
                    kind="source_ingested",
                    status="completed",
                    summary=f"自动处理 {sum(counts.values())} 条候选记忆",
                    details={
                        **counts,
                        "memory_ids": created_memory_ids,
                        "memory_changes": memory_changes,
                        "source_name": connector.name,
                        "source_profile": connector.profile_id,
                        "source_locator": source_locator,
                    },
                )
            )

        return IngestionResult(
            source_revision_id=source_revision_id,
            operation_id=operation_id,
            duplicate=False,
            counts=counts,
        )

    def remember(
        self,
        memory_space_id: str,
        draft: MemoryDraft,
        *,
        source_label: str = "agent_mcp",
    ) -> tuple[str, AdmissionAction, bool] | None:
        """Agent 主动写入一条记忆（MCP remember 工具）。

        复用同一套语义指纹 / 冲突 / 墓碑 / 准入策略，但不要求连接器与来源文件。
        返回 (memory_id, action)；被 discard/quarantine/restrict 语义排除时返回 None。
        """
        payload = dict(draft.payload)
        payload.setdefault("governance_path", "unified_admission")
        payload.setdefault(
            "semantic_authority",
            {
                "actor_kind": "agent",
                "assertion_kind": "inferred",
                "actor_id": source_label,
            },
        )
        draft = draft.model_copy(update={"payload": payload})
        fingerprint = semantic_fingerprint(draft)
        exact_fingerprint = exact_content_fingerprint(draft.type, draft.statement)
        key = semantic_key(draft)
        operation_id = new_id("operation")
        with self.database.session_factory.begin() as session:
            repository = MemoryRepository(session)
            existing = repository.memory_by_any_fingerprint(
                memory_space_id, tombstone_fingerprints(draft)
            )
            if existing is not None:
                replacement = self._explicit_user_replacement(draft, existing)
                if replacement is not None:
                    decision = self.policy.evaluate(draft, has_conflict=False)
                    if decision.action not in {
                        AdmissionAction.ACCEPT,
                        AdmissionAction.PROVISIONAL,
                        AdmissionAction.RESTRICT,
                    }:
                        return None
                    self._apply_replacement(
                        session,
                        replacement,
                        draft,
                        decision,
                        operation_id=operation_id,
                        actor_kind="user_evidence",
                    )
                    session.add(
                        ActivityRecord(
                            id=new_id("activity"),
                            operation_id=operation_id,
                            connector_id=None,
                            kind="memory_revised",
                            status="completed",
                            summary="用户明确表达覆盖 1 条推断记忆",
                            details={
                                "source": source_label,
                                "accepted": 1,
                                "memory_id": replacement.id,
                                "memory_changes": [
                                    {
                                        "id": replacement.id,
                                        "action": "revised",
                                        "type": draft.type.value,
                                        "statement": draft.statement,
                                    }
                                ],
                            },
                        )
                    )
                    return replacement.id, decision.action, True
                action = (
                    AdmissionAction.PROVISIONAL
                    if existing.epistemic_state == "inferred"
                    else AdmissionAction.ACCEPT
                )
                return existing.id, action, False
            cross_namespace_duplicate = repository.cross_namespace_exact_match(
                memory_space_id,
                exact_fingerprint,
                draft.namespace,
            )
            if cross_namespace_duplicate is not None:
                payload = dict(draft.payload)
                quality_issues = list(payload.get("quality_issues", []))
                quality_issues.append(
                    {
                        "code": "CROSS_NAMESPACE_EXACT_DUPLICATE",
                        "related_memory_id": cross_namespace_duplicate.id,
                        "related_namespace": cross_namespace_duplicate.namespace,
                    }
                )
                payload["quality_issues"] = quality_issues
                draft = draft.model_copy(update={"payload": payload})
            conflict = repository.slot_conflict(memory_space_id, key, fingerprint)
            replacement = self._explicit_user_replacement(
                draft,
                conflict
                or (
                    repository.inferred_identity_conflict(
                        memory_space_id,
                        draft.namespace,
                        fingerprint,
                    )
                    if self._is_explicit_user_identity(draft)
                    else None
                ),
            )
            decision = self.policy.evaluate(
                draft,
                has_conflict=conflict is not None and replacement is None,
                has_cross_namespace_duplicate=cross_namespace_duplicate is not None,
            )
            if repository.is_any_tombstoned(
                memory_space_id, tombstone_fingerprints(draft)
            ):
                decision = decision.model_copy(
                    update={
                        "action": AdmissionAction.DISCARD,
                        "reason_code": "TOMBSTONED_MEMORY",
                    }
                )
            if decision.action not in {
                AdmissionAction.ACCEPT,
                AdmissionAction.PROVISIONAL,
                AdmissionAction.RESTRICT,
            }:
                return None

            if replacement is not None:
                self._apply_replacement(
                    session,
                    replacement,
                    draft,
                    decision,
                    operation_id=operation_id,
                    actor_kind="user_evidence",
                )
                session.add(
                    ActivityRecord(
                        id=new_id("activity"),
                        operation_id=operation_id,
                        connector_id=None,
                        kind="memory_revised",
                        status="completed",
                        summary="用户明确表达覆盖 1 条推断记忆",
                        details={
                            "source": source_label,
                            "accepted": 1,
                            "memory_id": replacement.id,
                            "memory_changes": [
                                {
                                    "id": replacement.id,
                                    "action": "revised",
                                    "type": draft.type.value,
                                    "statement": draft.statement,
                                }
                            ],
                        },
                    )
                )
                return replacement.id, decision.action, True

            memory_id = new_id("memory")
            memory = MemoryObjectRecord(
                id=memory_id,
                memory_space_id=memory_space_id,
                type=draft.type.value,
                statement=draft.statement,
                reason=draft.reason,
                namespace=draft.namespace,
                sensitivity=decision.sensitivity.value,
                epistemic_state=decision.epistemic_state.value,
                status="active",
                semantic_fingerprint=fingerprint,
                exact_content_fingerprint=exact_fingerprint,
                semantic_key=key,
                payload=draft.payload,
                revision=1,
            )
            session.add(memory)
            session.add(
                MemoryRevisionRecord(
                    id=new_id("revision"),
                    memory_id=memory_id,
                    revision=1,
                    snapshot=self._snapshot(memory),
                        actor_kind=source_label,
                )
            )
            session.add(
                OperationEffectRecord(
                    id=new_id("effect"),
                    operation_id=operation_id,
                    object_id=memory_id,
                    effect_kind="created",
                    semantic_fingerprint=fingerprint,
                )
            )
            session.add(
                ActivityRecord(
                    id=new_id("activity"),
                    operation_id=operation_id,
                    connector_id=None,
                    kind="memory_added",
                    status="completed",
                    summary=f"Agent 写入 {draft.type.value} 记忆",
                    details={
                        "source": source_label,
                        "accepted": 1,
                        "memory_id": memory_id,
                        "memory_changes": [
                            {
                                "id": memory_id,
                                "action": "created",
                                "type": draft.type.value,
                                "statement": draft.statement,
                            }
                        ],
                    },
                )
            )
        return memory_id, decision.action, True

    def _drafts_from_profile(
        self,
        profile,
        content: str,
        *,
        source_revision_id: str,
        namespace: str,
        extractor: ExtractorPort | None = None,
        payload_metadata: dict[str, object] | None = None,
    ) -> list[MemoryDraft]:
        """Route source content to the appropriate draft producer.

        markdown profiles use the v0.1 MarkdownMemoryParser; conversation
        profiles use the v0.2 deterministic extractor (candidates only).
        """
        if (
            profile.format == "conversation"
            or profile.format in ADAPTER_FORMATS
            or profile.capabilities.observe_via_hook
            or profile.capabilities.accept_envelopes
        ):
            return self._drafts_from_extraction(
                profile,
                content,
                source_revision_id=source_revision_id,
                namespace=namespace,
                extractor=extractor,
                payload_metadata=payload_metadata,
            )
        parser = MarkdownMemoryParser(profile)
        drafts = parser.parse(
            content,
            source_revision_id=source_revision_id,
            namespace=namespace,
        )
        return [
            govern_source_draft(
                draft.model_copy(
                    update={
                        "payload": {
                            **draft.payload,
                            **(payload_metadata or {}),
                            "source_revision_id": source_revision_id,
                            "source_format": profile.format,
                            "source_family": profile.agent_family,
                            "source_profile": profile.profile_id,
                        }
                    }
                ),
                profile_id=profile.profile_id,
                source_locator=(
                    str(payload_metadata.get("source_locator"))
                    if payload_metadata and payload_metadata.get("source_locator")
                    else None
                ),
                source_digest=(
                    str(payload_metadata.get("source_digest"))
                    if payload_metadata and payload_metadata.get("source_digest")
                    else None
                ),
            )
            for draft in drafts
        ]

    def _drafts_from_extraction(
        self,
        profile,
        content: str,
        *,
        source_revision_id: str,
        namespace: str,
        extractor: ExtractorPort | None = None,
        payload_metadata: dict[str, object] | None = None,
    ) -> list[MemoryDraft]:
        if len(content.encode(profile.encoding)) > profile.max_bytes:
            raise ValueError("Source exceeds conversation profile byte limit")
        active_extractor = extractor or build_extractor(self.settings)
        result = active_extractor.extract(
            content,
            source_revision_id=source_revision_id,
            namespace=namespace,
        )
        return [
            draft_from_candidate(
                item,
                content,
                source_revision_id=source_revision_id,
                namespace=namespace,
                payload_metadata=payload_metadata,
            )
            for item in result.items
        ]

    @staticmethod
    def _empty_counts() -> dict[str, int]:
        return {
            "accepted": 0,
            "provisional": 0,
            "quarantined": 0,
            "restricted": 0,
            "discarded": 0,
        }

    @staticmethod
    def _is_explicit_user(draft: MemoryDraft) -> bool:
        authority = draft.payload.get("semantic_authority")
        return (
            isinstance(authority, dict)
            and authority.get("actor_kind") == "user"
            and authority.get("assertion_kind") in {"explicit", "confirmed"}
            and draft.payload.get("evidence_actor") in {"user", None}
        )

    @classmethod
    def _is_explicit_user_identity(cls, draft: MemoryDraft) -> bool:
        return draft.type == MemoryType.FACT and cls._is_explicit_user(draft) and (
            isinstance(draft.payload.get("semantic_slot"), str)
            and str(draft.payload["semantic_slot"]).startswith("self_identity")
            or identity_semantic_slot(draft.statement) != "self_identity:general"
        )

    @classmethod
    def _explicit_user_replacement(
        cls,
        draft: MemoryDraft,
        existing: MemoryObjectRecord | None,
    ) -> MemoryObjectRecord | None:
        if existing is None or not cls._is_explicit_user(draft):
            return None
        authority = (existing.payload or {}).get("semantic_authority")
        inferred = existing.epistemic_state == "inferred" or (
            isinstance(authority, dict)
            and authority.get("assertion_kind") in {"inferred", "observed", "unknown"}
        )
        if not inferred:
            return None
        if draft.type == MemoryType.FACT and cls._is_explicit_user_identity(draft):
            return existing if existing.namespace == draft.namespace else None
        return existing

    def _apply_replacement(
        self,
        session,
        memory: MemoryObjectRecord,
        draft: MemoryDraft,
        decision,
        *,
        operation_id: str,
        actor_kind: str,
    ) -> None:
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
        memory.type = draft.type.value
        memory.statement = draft.statement
        memory.reason = draft.reason
        memory.namespace = draft.namespace
        memory.sensitivity = decision.sensitivity.value
        memory.epistemic_state = decision.epistemic_state.value
        memory.status = "active"
        memory.semantic_fingerprint = semantic_fingerprint(draft)
        memory.exact_content_fingerprint = exact_content_fingerprint(
            draft.type, draft.statement
        )
        memory.semantic_key = semantic_key(draft)
        memory.payload = draft.payload
        memory.revision += 1
        session.add(
            MemoryRevisionRecord(
                id=new_id("revision"),
                memory_id=memory.id,
                revision=memory.revision,
                snapshot=self._snapshot(memory),
                actor_kind=actor_kind,
            )
        )
        session.add(
            OperationEffectRecord(
                id=new_id("effect"),
                operation_id=operation_id,
                object_id=memory.id,
                effect_kind="revised",
                semantic_fingerprint=memory.semantic_fingerprint,
            )
        )

    @staticmethod
    def _count_key(action: AdmissionAction) -> str:
        return {
            AdmissionAction.ACCEPT: "accepted",
            AdmissionAction.PROVISIONAL: "provisional",
            AdmissionAction.QUARANTINE: "quarantined",
            AdmissionAction.RESTRICT: "restricted",
            AdmissionAction.DISCARD: "discarded",
        }[action]

    @staticmethod
    def _candidate_status(action: AdmissionAction) -> str:
        if action == AdmissionAction.QUARANTINE:
            return "quarantined"
        if action == AdmissionAction.DISCARD:
            return "discarded"
        return "resolved"

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
