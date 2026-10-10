from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select

from personalmemory.api.dependencies import require_session_token
from personalmemory.application.context import ContextCompiler, PreflightContextError
from personalmemory.application.ingestion import IngestionService
from personalmemory.application.memory_quality import (
    cross_namespace_duplicate_issues,
    dynamic_memory_quality_issues,
)
from personalmemory.application.review_assistance import (
    ReviewAssistance,
    load_review_assistance,
)
from personalmemory.application.review_decisions import (
    ReviewDecision,
    ReviewDecisionStoreError,
    ReviewVerdict,
    load_review_decisions,
    save_review_decision,
)
from personalmemory.application.review_triage import ReviewTriage, build_review_triage
from personalmemory.application.undo import MemoryOperations, RevisionConflictError
from personalmemory.domain.memory.model import MemoryDraft
from personalmemory.domain.memory.transfer import TransferProfile
from personalmemory.domain.memory.types import MemoryType, Sensitivity
from personalmemory.infrastructure.database.models import (
    MemoryObjectRecord,
    MemoryRelationRecord,
    SourceRevisionRecord,
)
from personalmemory.infrastructure.database.search import MemorySearchIndex

router = APIRouter(
    prefix="/memories",
    tags=["memories"],
    dependencies=[Depends(require_session_token)],
)


class MemoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    statement: str
    reason: str | None = None
    namespace: str = "general"
    transfer: TransferProfile | None = None

    @model_validator(mode="after")
    def validate_transfer_type(self) -> MemoryCreateRequest:
        if self.transfer is not None and self.type not in {
            MemoryType.DECISION.value,
            MemoryType.INSIGHT.value,
            MemoryType.EVENT.value,
        }:
            raise ValueError("transfer metadata is only valid for decision, insight, or event")
        return self


class MemoryReviseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int
    statement: str
    reason: str | None = None


class MemoryReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    verdict: ReviewVerdict
    note: str | None = Field(default=None, max_length=1_000)
    scope_hint: str | None = Field(default=None, max_length=500)

    @field_validator("note", "scope_hint")
    @classmethod
    def strip_optional_review_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class MemoryContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    requested_mode: Literal["lexical", "embedding", "hybrid"] = "lexical"
    namespaces: list[str] | None = Field(default=None, min_length=1, max_length=20)
    allowed_types: list[MemoryType] = Field(
        default_factory=lambda: list(MemoryType), min_length=1, max_length=len(MemoryType)
    )
    max_sensitivity: Sensitivity = Sensitivity.PERSONAL
    token_budget: int = Field(default=1600, ge=128, le=4096)
    limit: int = Field(default=10, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped

    @field_validator("namespaces")
    @classmethod
    def normalize_namespaces(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = list(dict.fromkeys(namespace.strip() for namespace in value))
        if not normalized or any(not namespace for namespace in normalized):
            raise ValueError("namespaces must not contain blanks")
        return normalized


class MemoryPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector_id: str = Field(min_length=1, max_length=180)
    cwd: str = Field(min_length=1, max_length=2000)
    query: str = Field(min_length=1, max_length=500)

    @field_validator("connector_id", "cwd", "query")
    @classmethod
    def strip_preflight_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped


def memory_dict(
    memory: MemoryObjectRecord,
    *,
    source_path: str | None = None,
    quality_issues: list[dict[str, str]] | None = None,
    review_assistance: ReviewAssistance | None = None,
    review_decision: ReviewDecision | None = None,
    review_triage: ReviewTriage | None = None,
) -> dict[str, object]:
    payload_issues = (memory.payload or {}).get("quality_issues", [])
    merged_issues: list[dict[str, str]] = []
    for issue in [
        *(payload_issues if isinstance(payload_issues, list) else []),
        *dynamic_memory_quality_issues([memory]).get(memory.id, []),
        *(quality_issues or []),
    ]:
        if isinstance(issue, dict) and issue not in merged_issues:
            merged_issues.append(issue)
    result: dict[str, object] = {
        "id": memory.id,
        "type": memory.type,
        "statement": memory.statement,
        "reason": memory.reason,
        "namespace": memory.namespace,
        "sensitivity": memory.sensitivity,
        "epistemic_state": memory.epistemic_state,
        "status": memory.status,
        "revision": memory.revision,
        "payload": memory.payload,
        "provenance": memory_provenance(memory, source_path=source_path),
        "quality_issues": merged_issues,
        "created_at": memory.created_at.isoformat(),
        "updated_at": memory.updated_at.isoformat(),
    }
    if review_assistance is not None:
        result["review_assistance"] = review_assistance
    if review_decision is not None:
        result["review_decision"] = review_decision
    if review_triage is not None:
        result["review_triage"] = review_triage
    return result


def memory_provenance(
    memory: MemoryObjectRecord, *, source_path: str | None = None
) -> dict[str, object | None]:
    """Build a stable management view without copying source indexes into Memory text."""
    payload = memory.payload or {}
    authority = payload.get("semantic_authority")
    authority = authority if isinstance(authority, dict) else {}

    def text_value(*keys: str) -> str | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def int_value(*keys: str) -> int | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        return None

    actor_kind = authority.get("actor_kind")
    actor_id = authority.get("actor_id")
    return {
        "source_revision_id": text_value("source_revision_id"),
        "source_kind": text_value("source_kind"),
        "source_family": text_value("source_family"),
        "source_format": text_value("source_format"),
        "source_signal": text_value("source_signal", "source"),
        "source_profile": text_value("source_profile"),
        "capture_mode": text_value("capture_mode"),
        "locator": text_value(
            "evidence_locator",
            "transcript_path",
            "source_locator",
            "source_path",
            "path",
            "locator",
        ) or source_path,
        "project": text_value("cwd", "project_root", "project"),
        "actor_kind": actor_kind if isinstance(actor_kind, str) else None,
        "actor_id": actor_id if isinstance(actor_id, str) else None,
        "evidence_actor": text_value("evidence_actor"),
        "evidence_excerpt": text_value("evidence_excerpt"),
        "evidence_start": int_value("evidence_start", "start"),
        "evidence_end": int_value("evidence_end", "end"),
        "line_start": int_value("source_line_start", "line_start"),
        "line_end": int_value("source_line_end", "line_end"),
        "source_section": text_value("source_section"),
    }


@router.get("")
def list_memories(
    request: Request,
    search: str | None = Query(default=None, max_length=200),
    memory_type: str | None = Query(default=None, alias="type"),
    epistemic_state: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=1000),
) -> dict[str, object]:
    query = select(MemoryObjectRecord).where(MemoryObjectRecord.status == "active")
    if search:
        matching_ids = MemorySearchIndex(request.app.state.database).search_ids(search)
        query = query.where(MemoryObjectRecord.id.in_(matching_ids))
    if memory_type:
        query = query.where(MemoryObjectRecord.type == memory_type)
    if epistemic_state:
        query = query.where(MemoryObjectRecord.epistemic_state == epistemic_state)
    count_query = select(func.count()).select_from(query.subquery())
    query = query.order_by(MemoryObjectRecord.updated_at.desc())
    if limit is not None:
        query = query.limit(limit)
    with request.app.state.database.session_factory() as session:
        total = session.scalar(count_query) or 0
        items = session.scalars(query).all()
        all_active = session.scalars(
            select(MemoryObjectRecord).where(MemoryObjectRecord.status == "active")
        ).all()
        source_revision_ids = {
            (item.payload or {}).get("source_revision_id")
            for item in all_active
            if isinstance((item.payload or {}).get("source_revision_id"), str)
        }
        source_paths = {
            revision.id: revision.source_path
            for revision in session.scalars(
                select(SourceRevisionRecord).where(
                    SourceRevisionRecord.id.in_(source_revision_ids)
                )
            ).all()
        }
        quality_by_memory = cross_namespace_duplicate_issues(all_active)
    review_assistance = load_review_assistance(request.app.state.settings.data_dir)
    review_decisions = load_review_decisions(request.app.state.settings.data_dir)
    triage_by_memory = build_review_triage(
        all_active,
        quality_by_memory=quality_by_memory,
        review_decisions=review_decisions,
    )
    return {
        "items": [
            memory_dict(
                item,
                source_path=source_paths.get(
                    (item.payload or {}).get("source_revision_id")
                ),
                quality_issues=quality_by_memory.get(item.id),
                review_assistance=review_assistance.get(item.id),
                review_decision=review_decisions.get(item.id),
                review_triage=triage_by_memory.get(item.id),
            )
            for item in items
        ],
        "total": total,
    }


@router.get("/namespaces")
def list_memory_namespaces(request: Request) -> dict[str, object]:
    query = (
        select(
            MemoryObjectRecord.namespace,
            func.count(MemoryObjectRecord.id).label("memory_count"),
        )
        .where(MemoryObjectRecord.status == "active")
        .group_by(MemoryObjectRecord.namespace)
        .order_by(func.count(MemoryObjectRecord.id).desc(), MemoryObjectRecord.namespace.asc())
    )
    with request.app.state.database.session_factory() as session:
        rows = session.execute(query).all()
    return {
        "items": [
            {"namespace": namespace, "memory_count": memory_count}
            for namespace, memory_count in rows
        ],
        "total": sum(memory_count for _, memory_count in rows),
    }



GRAPH_MAX_NODES = 1000
GRAPH_MAX_DERIVED_EDGES = 300
GRAPH_ROLE_SHARED_TOPICS = 2
GRAPH_RELATED_SHARED_TOPICS = 2
GRAPH_WEAK_SHARED_TOPICS = 3

_EN_STOPWORDS = {
    "the", "and", "that", "this", "with", "for", "from", "have", "has", "was",
    "were", "you", "your", "our", "will", "can", "not", "but", "are", "its",
    "all", "about", "into", "their", "them", "they", "what", "when", "where",
    "who", "why", "how", "one", "two", "use", "using", "want", "need", "make",
    "made", "work", "used", "also", "only",
}
_ZH_STOPWORDS = {
    "我的", "我们", "这个", "一个", "一种", "进行", "因为", "所以", "但是",
    "以及", "可以", "需要", "用于", "对于", "通过", "完成", "开始", "应该",
    "能够", "不能", "不是", "没有", "就是", "还是", "已经", "正在", "还有",
    "相关", "主要", "比较", "非常", "很多", "一些", "这些", "那些", "自己",
    "成为", "认为", "知道", "了解", "看到", "使用", "提供", "支持",
}

# Story-chain role edges: source is the foundation, target is what derives from it.
# 目标 <- 能力/经历/事实 <- 洞察 <- 决策 的叙事链在推导时按此表赋予语义。
_ROLE_EDGES: dict[tuple[str, str], str] = {
    ("event", "insight"): "derived_from",
    ("event", "decision"): "derived_from",
    ("insight", "decision"): "supports",
    ("insight", "goal"): "supports",
    ("skill", "goal"): "supports",
    ("event", "skill"): "supports",
    ("fact", "goal"): "supports",
    ("fact", "insight"): "supports",
    ("fact", "decision"): "supports",
}

_TYPE_IMPORTANCE = {
    "decision": 0.85,
    "insight": 0.75,
    "goal": 0.8,
    "skill": 0.6,
    "preference": 0.6,
    "event": 0.5,
    "fact": 0.5,
}


def _node_importance(memory: MemoryObjectRecord) -> float:
    """Node importance drives visual weight; prefers stored metadata."""
    raw = memory.payload.get("importance")
    try:
        value = float(raw)
        if 0.0 <= value <= 1.0:
            return round(value, 3)
    except (TypeError, ValueError):
        pass
    return _TYPE_IMPORTANCE.get(memory.type, 0.5)


def _memory_topics(memory: MemoryObjectRecord) -> set[str]:
    """Deterministic topic extraction for graph edges (read-only heuristic)."""
    text = f"{memory.statement} {memory.reason or ''}"
    topics: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{1,}", text.casefold()):
        if token not in _EN_STOPWORDS and token not in topics:
            topics.add(token)
    for segment in re.findall(r"[\u4e00-\u9fff]{2,8}", text):
        if segment not in _ZH_STOPWORDS:
            topics.add(segment)
        for size in (2, 3):
            for index in range(len(segment) - size + 1):
                gram = segment[index : index + size]
                if gram not in _ZH_STOPWORDS:
                    topics.add(gram)
    return topics


def _derived_edge_kind(
    source_type: str, target_type: str, shared: int
) -> tuple[str, str, str] | None:
    """Decide a semantic derived edge for an unordered memory pair.

    Returns (foundation_type, derived_type, relation) when a meaningful edge
    exists; the caller maps types back to concrete memory ids.
    """
    role = _ROLE_EDGES.get((source_type, target_type))
    if role is not None and shared >= GRAPH_ROLE_SHARED_TOPICS:
        return source_type, target_type, role
    role = _ROLE_EDGES.get((target_type, source_type))
    if role is not None and shared >= GRAPH_ROLE_SHARED_TOPICS:
        return target_type, source_type, role
    if source_type == target_type and shared >= GRAPH_RELATED_SHARED_TOPICS:
        return source_type, target_type, "related_to"
    if shared >= GRAPH_WEAK_SHARED_TOPICS:
        return source_type, target_type, "related_to"
    return None


def _find_cluster_roots(node_ids: list[str]) -> tuple[object, object]:
    parent = {node_id: node_id for node_id in node_ids}

    def find(node_id: str) -> str:
        while parent[node_id] != node_id:
            parent[node_id] = parent[parent[node_id]]
            node_id = parent[node_id]
        return node_id

    def union(a: str, b: str) -> None:
        root_a = find(a)
        root_b = find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    return find, union


def _cluster_label(
    member_nodes: list[MemoryObjectRecord], topics: dict[str, set[str]]
) -> str:
    """Pick the most distinctive shared topic as the cluster label."""
    counts: dict[str, int] = {}
    for memory in member_nodes:
        for topic in topics[memory.id]:
            counts[topic] = counts.get(topic, 0) + 1
    if not counts:
        namespaces = [memory.namespace for memory in member_nodes]
        return max(set(namespaces), key=namespaces.count) if namespaces else "未命名群组"
    meaningful = [
        topic
        for topic in counts
        if len(topic) >= 4 or (topic.isascii() and len(topic) >= 3)
    ]
    candidates = meaningful or [topic for topic in counts if len(topic) >= 2]
    if not candidates:
        return "未命名群组"
    best = max(candidates, key=lambda topic: (counts[topic], len(topic)))
    return best


@router.post("", status_code=status.HTTP_201_CREATED)
def create_memory(
    payload: MemoryCreateRequest, request: Request, response: Response
) -> dict[str, object]:
    """Agent / 用户主动写入一条记忆（走准入/指纹/墓碑语义，幂等）。"""
    try:
        memory_type = MemoryType(payload.type)
    except ValueError as error:
        raise HTTPException(400, detail={"code": "UNKNOWN_MEMORY_TYPE"}) from error
    statement = payload.statement.strip()
    if not statement:
        raise HTTPException(400, detail={"code": "EMPTY_STATEMENT"})
    if len(statement) > 2000:
        raise HTTPException(400, detail={"code": "STATEMENT_TOO_LONG"})

    draft = MemoryDraft(
        type=memory_type,
        statement=statement,
        reason=(payload.reason or "").strip() or None,
        source_revision_id="manual:agent_mcp",
        confidence=0.9,
        importance=0.5,
        namespace=(payload.namespace or "general").strip() or "general",
        payload={
            "source": "agent_mcp",
            **(
                {"transfer": payload.transfer.model_dump(mode="json")}
                if payload.transfer is not None
                else {}
            ),
        },
    )
    result = IngestionService(request.app.state.database).remember(
        "space_default", draft, source_label="agent_mcp"
    )
    if result is None:
        raise HTTPException(
            409, detail={"code": "MEMORY_NOT_ADMITTED", "reason_code": "POLICY_REJECTED"}
        )
    memory_id, action, created = result
    with request.app.state.database.session_factory() as session:
        memory = session.get(MemoryObjectRecord, memory_id)
        if memory is None:
            raise HTTPException(404, detail={"code": "MEMORY_NOT_FOUND"})
        body = {"memory": memory_dict(memory), "action": action.value}
    if not created:
        response.status_code = status.HTTP_200_OK
        body["duplicate"] = True
    else:
        body["duplicate"] = False
    return body


@router.post("/context")
def compile_memory_context(
    payload: MemoryContextRequest, request: Request
) -> dict[str, object]:
    """Compile bounded task context without changing authoritative memories."""
    return ContextCompiler(
        request.app.state.database, data_dir=request.app.state.settings.data_dir
    ).compile(
        payload.query,
        requested_mode=payload.requested_mode,
        namespaces=payload.namespaces,
        allowed_types=[memory_type.value for memory_type in payload.allowed_types],
        max_sensitivity=payload.max_sensitivity.value,
        token_budget=payload.token_budget,
        limit=payload.limit,
    )


@router.post("/preflight")
def compile_preflight_context(
    payload: MemoryPreflightRequest, request: Request
) -> dict[str, object]:
    """Compile automatic, connector-scoped context for a natural user prompt."""
    try:
        return ContextCompiler(
            request.app.state.database, data_dir=request.app.state.settings.data_dir
        ).compile_preflight(
            payload.query,
            connector_id=payload.connector_id,
            cwd=payload.cwd,
        )
    except PreflightContextError as error:
        status_code = 404 if error.code == "CONNECTOR_NOT_FOUND" else 403
        raise HTTPException(status_code, detail={"code": error.code}) from error


@router.get("/graph")
def memory_graph(request: Request) -> dict[str, object]:
    """Active memories as nodes, semantic edges, and topic clusters.

    Edges: explicit relations from memory_relations (real semantics) plus
    derived edges that follow the story chain (event -> insight/decision ->
    goal, skill -> goal, ...). Derived edges only fire on topic overlap, so
    the graph never draws random keyword connections.
    """
    with request.app.state.database.session_factory() as session:
        memories = session.scalars(
            select(MemoryObjectRecord)
            .where(MemoryObjectRecord.status == "active")
            .order_by(MemoryObjectRecord.updated_at.desc())
            .limit(GRAPH_MAX_NODES)
        ).all()
        source_revision_ids = {
            (item.payload or {}).get("source_revision_id")
            for item in memories
            if isinstance((item.payload or {}).get("source_revision_id"), str)
        }
        source_paths = {
            revision.id: revision.source_path
            for revision in session.scalars(
                select(SourceRevisionRecord).where(
                    SourceRevisionRecord.id.in_(source_revision_ids)
                )
            ).all()
        }
        quality_by_memory = cross_namespace_duplicate_issues(memories)
        review_decisions = load_review_decisions(request.app.state.settings.data_dir)
        triage_by_memory = build_review_triage(
            memories,
            quality_by_memory=quality_by_memory,
            review_decisions=review_decisions,
        )
        memories = [
            memory
            for memory in memories
            if triage_by_memory[memory.id]["graph_eligible"]
        ]
        node_ids = [memory.id for memory in memories]
        nodes_by_id = {memory.id: memory for memory in memories}
        nodes = [
            {
                **memory_dict(
                    memory,
                    source_path=source_paths.get(
                        (memory.payload or {}).get("source_revision_id")
                    ),
                    quality_issues=quality_by_memory.get(memory.id),
                    review_triage=triage_by_memory.get(memory.id),
                ),
                "importance": _node_importance(memory),
            }
            for memory in memories
        ]
        edges: list[dict[str, object]] = []

        if node_ids:
            relations = session.scalars(
                select(MemoryRelationRecord).where(
                    MemoryRelationRecord.validity_state == "active",
                    MemoryRelationRecord.source_memory_id.in_(node_ids),
                    MemoryRelationRecord.target_memory_id.in_(node_ids),
                )
            ).all()
            for relation in relations:
                edges.append(
                    {
                        "source": relation.source_memory_id,
                        "target": relation.target_memory_id,
                        "type": relation.relation_type,
                        "origin": relation.origin,
                        "derived": False,
                    }
                )

        explicit_pairs = {
            (edge["source"], edge["target"])
            for edge in edges
            if not edge["derived"]
        }
        topics = {memory.id: _memory_topics(memory) for memory in memories}
        topic_to_ids: dict[str, list[str]] = {}
        for memory_id, words in topics.items():
            for word in words:
                topic_to_ids.setdefault(word, []).append(memory_id)

        pair_shared: dict[tuple[str, str], int] = {}
        for ids in topic_to_ids.values():
            unique_ids = list(dict.fromkeys(ids))
            for index, source_id in enumerate(unique_ids):
                for target_id in unique_ids[index + 1 :]:
                    pair = tuple(sorted((source_id, target_id)))
                    pair_shared[pair] = pair_shared.get(pair, 0) + 1

        derived_candidates: list[dict[str, object]] = []
        for (source_id, target_id), shared in pair_shared.items():
            source_memory = nodes_by_id[source_id]
            target_memory = nodes_by_id[target_id]
            decision = _derived_edge_kind(
                source_memory.type, target_memory.type, shared
            )
            if decision is None:
                continue
            foundation_type, derived_type, relation = decision
            if source_memory.type == foundation_type:
                edge_source, edge_target = source_id, target_id
            else:
                edge_source, edge_target = target_id, source_id
            if (edge_source, edge_target) in explicit_pairs:
                continue
            derived_candidates.append(
                {
                    "source": edge_source,
                    "target": edge_target,
                    "type": relation,
                    "origin": "derived_role" if relation != "related_to" else "derived_topic",
                    "derived": True,
                    "score": shared,
                }
            )

        derived_candidates.sort(
            key=lambda edge: (
                0 if edge["type"] != "related_to" else 1,
                -int(edge["score"] or 0),
            )
        )
        for derived_index, edge in enumerate(derived_candidates):
            edges.append(edge)
            if derived_index + 1 >= GRAPH_MAX_DERIVED_EDGES:
                break

        # Topic clusters: connected components of the combined edge set.
        clusters: list[dict[str, object]] = []
        cluster_ids: dict[str, str] = {}
        if node_ids:
            find_fn, union_fn = _find_cluster_roots(node_ids)
            for edge in edges:
                union_fn(str(edge["source"]), str(edge["target"]))
            groups: dict[str, list[str]] = {}
            for node_id in node_ids:
                groups.setdefault(find_fn(node_id), []).append(node_id)
            for cluster_index, group in enumerate(
                sorted(groups.values(), key=len, reverse=True)
            ):
                cluster_id = f"c{cluster_index}"
                member_nodes = [nodes_by_id[node_id] for node_id in group]
                clusters.append(
                    {
                        "id": cluster_id,
                        "label": _cluster_label(member_nodes, topics),
                        "size": len(group),
                        "node_ids": group,
                    }
                )
                for node_id in group:
                    cluster_ids[node_id] = cluster_id

        for node in nodes:
            node["cluster_id"] = cluster_ids.get(node["id"], "")

        return {
            "nodes": nodes,
            "edges": edges,
            "clusters": clusters,
            "counts": {
                "nodes": len(nodes),
                "edges": len(edges),
                "clusters": len(clusters),
                "explicit_edges": sum(1 for edge in edges if not edge["derived"]),
                "derived_edges": sum(1 for edge in edges if edge["derived"]),
            },
        }


@router.get("/{memory_id}")
def get_memory(memory_id: str, request: Request) -> dict[str, object]:
    with request.app.state.database.session_factory() as session:
        memory = session.get(MemoryObjectRecord, memory_id)
        if memory is None:
            raise HTTPException(404, detail={"code": "MEMORY_NOT_FOUND"})
        all_active = session.scalars(
            select(MemoryObjectRecord).where(MemoryObjectRecord.status == "active")
        ).all()
        source_revision_id = (memory.payload or {}).get("source_revision_id")
        source_revision = (
            session.get(SourceRevisionRecord, source_revision_id)
            if isinstance(source_revision_id, str)
            else None
        )
    decisions = load_review_decisions(request.app.state.settings.data_dir)
    quality_by_memory = cross_namespace_duplicate_issues(all_active)
    triage_by_memory = build_review_triage(
        all_active,
        quality_by_memory=quality_by_memory,
        review_decisions=decisions,
    )
    return memory_dict(
        memory,
        source_path=source_revision.source_path if source_revision else None,
        quality_issues=quality_by_memory.get(memory.id),
        review_assistance=load_review_assistance(
            request.app.state.settings.data_dir
        ).get(memory.id),
        review_decision=decisions.get(memory.id),
        review_triage=triage_by_memory.get(memory.id),
    )


@router.patch("/{memory_id}/review")
def review_memory(
    memory_id: str, payload: MemoryReviewRequest, request: Request
) -> dict[str, object]:
    """Record a user gold-set verdict without mutating or deleting Memory."""
    with request.app.state.database.session_factory() as session:
        memory = session.get(MemoryObjectRecord, memory_id)
        if memory is None or memory.status != "active":
            raise HTTPException(404, detail={"code": "MEMORY_NOT_FOUND"})
        if memory.revision != payload.expected_revision:
            raise HTTPException(409, detail={"code": "REVISION_CONFLICT"})
        scope_hint = payload.scope_hint
        if payload.verdict == "project_experience" and scope_hint is None:
            provenance = memory_provenance(memory)
            scope_hint = str(provenance.get("project") or memory.namespace)
        decision: ReviewDecision = {
            "verdict": payload.verdict,
            "memory_revision": memory.revision,
            "reviewed_at": datetime.now(UTC).isoformat(),
            "note": payload.note,
            "scope_hint": scope_hint,
        }
    try:
        return save_review_decision(
            request.app.state.settings.data_dir, memory_id, decision
        )
    except ReviewDecisionStoreError as error:
        raise HTTPException(
            500, detail={"code": "REVIEW_DECISION_NOT_SAVED"}
        ) from error


@router.patch("/{memory_id}")
def revise_memory(
    memory_id: str, payload: MemoryReviseRequest, request: Request
) -> dict[str, object]:
    try:
        memory = MemoryOperations(request.app.state.database).revise(
            memory_id,
            expected_revision=payload.expected_revision,
            statement=payload.statement,
            reason=payload.reason,
        )
    except KeyError as error:
        raise HTTPException(404, detail={"code": "MEMORY_NOT_FOUND"}) from error
    except RevisionConflictError as error:
        raise HTTPException(409, detail={"code": "REVISION_CONFLICT"}) from error
    return memory_dict(memory)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(memory_id: str, request: Request) -> Response:
    try:
        MemoryOperations(request.app.state.database).delete(memory_id)
    except KeyError as error:
        raise HTTPException(404, detail={"code": "MEMORY_NOT_FOUND"}) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
