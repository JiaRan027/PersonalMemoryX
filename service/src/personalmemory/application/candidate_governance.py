from __future__ import annotations

import re
from typing import Any

from personalmemory.domain.extraction.extractor import CandidateItem
from personalmemory.domain.memory.model import MemoryDraft
from personalmemory.domain.memory.types import MemoryType

AGENT_NATIVE_MARKDOWN_PROFILES = {
    "claude_memory_markdown",
    "codex_memory_markdown",
}

_IDENTITY_EDUCATION_PATTERN = re.compile(
    r"^(?:一名|一个|一位)?(?:学生|大学生|本科生|研究生|博士生)$",
    re.IGNORECASE,
)
_IDENTITY_OCCUPATION_PATTERN = re.compile(
    r"^(?:一名|一个|一位)?(?:程序员|开发者|工程师|老师|教师|设计师|产品经理|"
    r"研究员|自由职业者|创业者|创作者)$",
    re.IGNORECASE,
)

_TECHNICAL_RULE_SIGNAL = re.compile(
    r"(?:`[^`]+`|@[A-Za-z][A-Za-z0-9_]*|\.[A-Za-z_][A-Za-z0-9_]*\s*\(|"
    r"\b(?:ArkTS|TypeScript|JavaScript|Python|Rust|Vue|React|compiler|compile|"
    r"method|function|class|component|attribute|runtime|exception|error)\b)",
    re.IGNORECASE,
)
_USER_PREFERENCE_SIGNAL = re.compile(
    r"(?:用户|我(?:喜欢|偏好|希望|要求|不喜欢|讨厌)|请(?:始终|不要|使用)|"
    r"回答用户|与用户|user (?:asked|requires|prefers|likes|dislikes)|"
    r"I (?:prefer|like|dislike|want))",
    re.IGNORECASE,
)


def identity_semantic_slot(statement: str) -> str:
    """Keep independent identity dimensions from creating false conflicts."""
    normalized = " ".join(statement.strip().split())
    if _IDENTITY_EDUCATION_PATTERN.fullmatch(normalized):
        return "self_identity:education"
    if _IDENTITY_OCCUPATION_PATTERN.fullmatch(normalized):
        return "self_identity:occupation"
    return "self_identity:general"


def govern_source_draft(
    draft: MemoryDraft,
    *,
    profile_id: str,
    source_locator: str | None = None,
    source_digest: str | None = None,
) -> MemoryDraft:
    """Attach source governance and correct narrow agent-native misclassifications."""
    if profile_id not in AGENT_NATIVE_MARKDOWN_PROFILES:
        return draft

    payload = dict(draft.payload)
    payload.update(
        {
            "source_format": "markdown",
            "source_signal": "agent_memory",
            "evidence_actor": "agent_memory",
            "semantic_authority": {
                "actor_kind": "agent",
                "assertion_kind": "observed",
                "actor_id": profile_id,
            },
            "authority_verification": "agent_native_unverified",
        }
    )
    if source_locator:
        payload["source_locator"] = source_locator
    if source_digest:
        payload["source_digest"] = source_digest

    memory_type = draft.type
    confidence = draft.confidence
    importance = draft.importance
    if (
        profile_id == "claude_memory_markdown"
        and memory_type == MemoryType.PREFERENCE
        and _TECHNICAL_RULE_SIGNAL.search(f"{draft.statement} {draft.reason or ''}")
        and not _USER_PREFERENCE_SIGNAL.search(f"{draft.statement} {draft.reason or ''}")
    ):
        memory_type = MemoryType.INSIGHT
        confidence = min(confidence, 0.65)
        importance = max(importance, 0.6)
        payload.update(
            {
                "classification_original_type": MemoryType.PREFERENCE.value,
                "classification_reason_code": "TECHNICAL_RULE_NOT_USER_PREFERENCE",
            }
        )

    return draft.model_copy(
        update={
            "type": memory_type,
            "confidence": confidence,
            "importance": importance,
            "payload": payload,
        }
    )


def draft_from_candidate(
    item: CandidateItem,
    content: str,
    *,
    source_revision_id: str,
    namespace: str,
    payload_metadata: dict[str, object] | None = None,
) -> MemoryDraft:
    start, end = item.evidence_span
    evidence_excerpt = content[start:end]
    while len(evidence_excerpt.encode("utf-8")) > 1024:
        evidence_excerpt = evidence_excerpt[:-1]
    normalized_excerpt = evidence_excerpt.lstrip().casefold()
    evidence_actor = (
        "user"
        if normalized_excerpt.startswith(("用户:", "用户：", "user:"))
        else "unknown"
    )
    submitted_authority = (
        payload_metadata.get("authority")
        if payload_metadata and isinstance(payload_metadata.get("authority"), dict)
        else None
    )
    if evidence_actor == "user":
        semantic_authority: dict[str, Any] = submitted_authority or {
            "actor_kind": "user",
            "assertion_kind": "explicit",
            "actor_id": None,
        }
    else:
        semantic_authority = {
            "actor_kind": "agent",
            "assertion_kind": "inferred",
            "actor_id": payload_metadata.get("source_family") if payload_metadata else None,
        }
    provenance = {
        "source_format": (
            payload_metadata.get("source_format", "conversation")
            if payload_metadata
            else "conversation"
        ),
        "source_revision_id": source_revision_id,
        "source_signal": item.source_signal,
        "evidence_start": start,
        "evidence_end": end,
        "evidence_excerpt": evidence_excerpt,
        "evidence_actor": evidence_actor,
        "semantic_authority": semantic_authority,
        "extractor_extra": item.extra,
    }
    matched_prefix = str(item.extra.get("matched_prefix", ""))
    if item.type == MemoryType.FACT and (
        matched_prefix in {"我是", "我是一名", "我是一个"}
        or identity_semantic_slot(item.statement) != "self_identity:general"
    ):
        # Identity claims conflict only within the same dimension. A person can
        # be both a student and a developer; a later occupation claim still
        # enters conflict governance instead of silently replacing the first.
        provenance["semantic_slot"] = identity_semantic_slot(item.statement)
    return MemoryDraft(
        type=item.type,
        statement=item.statement,
        reason=item.reason,
        source_revision_id=source_revision_id,
        confidence=item.confidence,
        importance=item.importance,
        namespace=namespace,
        payload={**(payload_metadata or {}), **provenance},
    )
