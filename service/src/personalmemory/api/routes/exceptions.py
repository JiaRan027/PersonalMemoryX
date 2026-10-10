from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from personalmemory.api.dependencies import require_session_token
from personalmemory.application.memory_quality import cross_namespace_duplicate_issues
from personalmemory.infrastructure.database.models import (
    ConnectorInstanceRecord,
    ConnectorIssueRecord,
    MemoryCandidateRecord,
    MemoryObjectRecord,
)

router = APIRouter(
    prefix="/exceptions",
    tags=["exceptions"],
    dependencies=[Depends(require_session_token)],
)


@router.get("")
def list_exceptions(request: Request) -> dict[str, object]:
    with request.app.state.database.session_factory() as session:
        candidates = session.scalars(
            select(MemoryCandidateRecord)
            .where(MemoryCandidateRecord.status == "quarantined")
            .order_by(MemoryCandidateRecord.created_at.desc())
        ).all()
        issues = session.scalars(
            select(ConnectorIssueRecord)
            .where(ConnectorIssueRecord.status == "open")
            .order_by(ConnectorIssueRecord.created_at.desc())
        ).all()
        connector_names = {
            connector.id: connector.name
            for connector in session.scalars(select(ConnectorInstanceRecord)).all()
        }
        memories = session.scalars(
            select(MemoryObjectRecord).where(MemoryObjectRecord.status == "active")
        ).all()
        duplicate_issues = cross_namespace_duplicate_issues(memories)
    items = [
        {
            "id": candidate.id,
            "kind": "quarantine",
            "reason_code": candidate.reason_code,
            "statement": (candidate.draft or {}).get("statement"),
            "memory_type": (candidate.draft or {}).get("type"),
            "connector_id": None,
            "created_at": candidate.created_at.isoformat(),
        }
        for candidate in candidates
    ]
    items.extend(
        {
            "id": issue.id,
            "kind": "connector",
            "reason_code": issue.reason_code,
            "statement": connector_names.get(issue.connector_id, "连接需要处理"),
            "memory_type": None,
            "connector_id": issue.connector_id,
            "created_at": issue.created_at.isoformat(),
        }
        for issue in issues
    )
    memories_by_id = {memory.id: memory for memory in memories}
    seen_duplicate_groups: set[tuple[str, ...]] = set()
    for memory_id, issues in duplicate_issues.items():
        related_ids = sorted(
            {
                str(issue["related_memory_id"])
                for issue in issues
                if issue.get("related_memory_id")
            }
        )
        group = tuple(sorted([memory_id, *related_ids]))
        if group in seen_duplicate_groups:
            continue
        seen_duplicate_groups.add(group)
        memory = memories_by_id[memory_id]
        related_namespaces = sorted(
            {memory.namespace}
            | {
                memories_by_id[related_id].namespace
                for related_id in related_ids
                if related_id in memories_by_id
            }
        )
        items.append(
            {
                "id": f"duplicate:{memory.exact_content_fingerprint or memory.id}",
                "kind": "memory_quality",
                "reason_code": "CROSS_NAMESPACE_EXACT_DUPLICATE",
                "statement": memory.statement,
                "memory_type": memory.type,
                "memory_id": memory.id,
                "related_memory_ids": related_ids,
                "related_namespaces": related_namespaces,
                "connector_id": None,
                "created_at": memory.updated_at.isoformat(),
            }
        )
    items.sort(key=lambda item: str(item["created_at"]), reverse=True)
    return {"items": items, "total": len(items)}
