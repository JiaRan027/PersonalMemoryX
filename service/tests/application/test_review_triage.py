from __future__ import annotations

from datetime import UTC, datetime, timedelta

from personalmemory.application.review_triage import build_review_triage
from personalmemory.domain.memory.fingerprint import (
    exact_content_fingerprint,
    semantic_fingerprint_value,
)
from personalmemory.infrastructure.database.models import MemoryObjectRecord

from service.tests.api.test_memories_create import auth_headers, make_client


def record(
    memory_id: str,
    statement: str,
    *,
    memory_type: str = "fact",
    namespace: str = "general",
    epistemic_state: str = "inferred",
    reason: str | None = "Source evidence.",
    payload: dict[str, object] | None = None,
    created_at: datetime | None = None,
) -> MemoryObjectRecord:
    return MemoryObjectRecord(
        id=memory_id,
        memory_space_id="space_default",
        type=memory_type,
        statement=statement,
        reason=reason,
        namespace=namespace,
        sensitivity="personal",
        epistemic_state=epistemic_state,
        status="active",
        semantic_fingerprint=semantic_fingerprint_value(memory_type, namespace, statement),
        exact_content_fingerprint=exact_content_fingerprint(memory_type, statement),
        semantic_key=None,
        payload=payload or {},
        revision=1,
        created_at=created_at or datetime(2026, 8, 1, tzinfo=UTC),
    )


def test_triage_automatically_isolates_agent_workflow_notes() -> None:
    memory = record(
        "memory_workflow",
        "Keep the rollout registry up to date.",
        memory_type="insight",
        payload={
            "source_signal": "agent_memory",
            "authority_verification": "agent_native_unverified",
            "source_section": "General Tips",
        },
    )

    triage = build_review_triage([memory])[memory.id]

    assert triage["effective_verdict"] == "source_only"
    assert triage["confidence"] == "high"
    assert triage["requires_user_review"] is False
    assert triage["delivery_eligible"] is False
    assert triage["graph_eligible"] is False
    assert triage["reason_codes"] == ["AGENT_WORKFLOW_NOTE"]


def test_triage_only_escalates_high_impact_unverified_agent_memory() -> None:
    preference = record(
        "memory_preference",
        "The user prefers React.",
        memory_type="preference",
        payload={
            "source_signal": "agent_memory",
            "authority_verification": "agent_native_unverified",
            "source_section": "User Preferences",
        },
    )
    insight = record(
        "memory_insight",
        "A retry boundary belongs at the adapter edge.",
        memory_type="insight",
        payload={
            "source_signal": "agent_memory",
            "authority_verification": "agent_native_unverified",
            "source_section": "User Profile",
        },
    )

    triage = build_review_triage([preference, insight])

    assert triage[preference.id]["effective_verdict"] == "needs_context"
    assert triage[preference.id]["requires_user_review"] is True
    assert triage[insight.id]["effective_verdict"] == "source_only"
    assert triage[insight.id]["requires_user_review"] is False


def test_triage_keeps_trusted_user_evidence_without_manual_review() -> None:
    memory = record(
        "memory_explicit",
        "I prefer local-first software.",
        memory_type="preference",
        payload={
            "source_signal": "agent_hook",
            "authority_verification": "user_explicit",
            "evidence_actor": "user",
            "evidence_excerpt": "I prefer local-first software.",
        },
    )

    triage = build_review_triage([memory])[memory.id]

    assert triage["suggested_verdict"] == "keep"
    assert triage["effective_verdict"] == "keep"
    assert triage["requires_user_review"] is False
    assert triage["delivery_eligible"] is True


def test_triage_isolates_identity_fact_without_recoverable_user_actor() -> None:
    memory = record(
        "memory_identity_example",
        "示例职业：工程师",
        memory_type="fact",
        epistemic_state="asserted",
        payload={
            "source_signal": "deterministic",
            "evidence_excerpt": "我是示例职业工程师",
            "evidence_actor": "unknown",
        },
    )

    triage = build_review_triage([memory])[memory.id]

    assert triage["effective_verdict"] == "source_only"
    assert triage["reason_codes"] == ["UNVERIFIED_DETERMINISTIC_HIGH_IMPACT"]
    assert triage["requires_user_review"] is False
    assert triage["delivery_eligible"] is False


def test_triage_does_not_turn_explicit_information_request_into_identity() -> None:
    canonical = record(
        "memory_identity_request",
        "测试用户想知道示例程序的运行机制，便于评估安全边界",
        memory_type="fact",
        namespace="codex",
        epistemic_state="asserted",
        payload={
            "source_signal": "deterministic",
            "evidence_actor": "user",
            "evidence_excerpt": "用户: 测试用户想知道示例程序的运行机制，便于评估安全边界",
            "semantic_authority": {
                "actor_kind": "user",
                "assertion_kind": "explicit",
                "actor_id": None,
            },
        },
    )
    duplicate = record(
        "memory_identity_request_duplicate",
        canonical.statement,
        memory_type="fact",
        namespace="claude_code",
        epistemic_state="asserted",
        payload={
            "source_signal": "deterministic",
            "evidence_actor": "user",
            "evidence_excerpt": "用户: 测试用户想知道示例程序的运行机制，便于评估安全边界",
            "semantic_authority": {
                "actor_kind": "user",
                "assertion_kind": "explicit",
                "actor_id": None,
            },
        },
        created_at=datetime(2026, 8, 2, tzinfo=UTC),
    )

    triage = build_review_triage([duplicate, canonical])

    assert triage[canonical.id]["effective_verdict"] == "source_only"
    assert triage[canonical.id]["reason_codes"] == ["TRANSIENT_INFORMATION_REQUEST"]
    assert triage[canonical.id]["delivery_eligible"] is False
    assert triage[duplicate.id]["effective_verdict"] == "source_only"
    assert triage[duplicate.id]["reason_codes"] == ["NON_CANONICAL_DUPLICATE"]
    assert triage[duplicate.id]["delivery_eligible"] is False


def test_triage_keeps_one_cross_namespace_duplicate_and_isolates_the_other() -> None:
    earlier = datetime(2026, 8, 1, tzinfo=UTC)
    canonical = record(
        "memory_canonical",
        "The local Core remains authoritative.",
        namespace="general",
        epistemic_state="asserted",
        created_at=earlier,
    )
    duplicate = record(
        "memory_duplicate",
        "The local Core remains authoritative.",
        namespace="codex",
        created_at=earlier + timedelta(minutes=1),
    )

    triage = build_review_triage([duplicate, canonical])

    assert triage[canonical.id]["delivery_eligible"] is True
    assert triage[duplicate.id]["effective_verdict"] == "source_only"
    assert triage[duplicate.id]["reason_codes"] == ["NON_CANONICAL_DUPLICATE"]


def test_current_user_override_governs_delivery_without_mutating_memory() -> None:
    memory = record(
        "memory_override",
        "The user prefers Rust.",
        memory_type="preference",
        payload={
            "source_signal": "agent_memory",
            "authority_verification": "agent_native_unverified",
        },
    )
    decisions = {
        memory.id: {
            "verdict": "keep",
            "memory_revision": 1,
            "reviewed_at": "2026-08-11T00:00:00Z",
            "note": None,
            "scope_hint": None,
        }
    }

    triage = build_review_triage([memory], review_decisions=decisions)[memory.id]

    assert triage["overridden_by_user"] is True
    assert triage["suggested_verdict"] == "needs_context"
    assert triage["effective_verdict"] == "keep"
    assert triage["requires_user_review"] is False
    assert triage["delivery_eligible"] is True


def test_test_source_is_automatically_excluded() -> None:
    memory = record(
        "memory_test",
        "Use this synthetic preference for acceptance.",
        memory_type="preference",
        payload={"evidence_locator": r"C:\repo\tests\fixtures\memory.md"},
    )

    triage = build_review_triage([memory])[memory.id]

    assert triage["effective_verdict"] == "test_artifact"
    assert triage["requires_user_review"] is False
    assert triage["delivery_eligible"] is False


def test_governed_legacy_provisional_memory_remains_deliverable() -> None:
    memory = record(
        "memory_legacy_governed",
        "Use a local continuity core.",
        memory_type="decision",
        payload={"source": "agent_mcp"},
    )

    triage = build_review_triage([memory])[memory.id]

    assert triage["effective_verdict"] == "keep"
    assert triage["confidence"] == "medium"
    assert triage["reason_codes"] == ["GOVERNED_PROVISIONAL"]
    assert triage["requires_user_review"] is False
    assert triage["delivery_eligible"] is True
    assert triage["graph_eligible"] is True


def test_asserted_operational_index_stays_in_source_catalog_only() -> None:
    memory = record(
        "memory_index",
        (
            "ExampleProject two-level comment replies: "
            "CommentSection.vue, parentId, replyToUser, mp-weixin"
        ),
        memory_type="event",
        epistemic_state="asserted",
        payload={"source_section": r"C:\projects\ExampleProject"},
    )

    triage = build_review_triage([memory])[memory.id]

    assert triage["effective_verdict"] == "source_only"
    assert triage["reason_codes"] == ["OPERATIONAL_INDEX"]
    assert triage["requires_user_review"] is False
    assert triage["delivery_eligible"] is False


def test_api_triage_excludes_source_only_from_context_and_graph_until_user_override(
    tmp_path,
) -> None:
    memory = record(
        "memory_workflow_api",
        "Provider Boundary Ledger keeps adapter failures explicit.",
        memory_type="insight",
        payload={
            "source_signal": "agent_memory",
            "authority_verification": "agent_native_unverified",
            "source_section": "General Tips",
        },
    )
    with make_client(tmp_path) as client:
        with client.app.state.database.session_factory.begin() as session:
            session.add(memory)

        listed = client.get("/api/v1/memories", headers=auth_headers()).json()["items"]
        context_before = client.post(
            "/api/v1/memories/context",
            headers=auth_headers(),
            json={"query": "Provider Boundary Ledger"},
        ).json()
        graph_before = client.get(
            "/api/v1/memories/graph", headers=auth_headers()
        ).json()

        reviewed = client.patch(
            f"/api/v1/memories/{memory.id}/review",
            headers=auth_headers(),
            json={"expected_revision": 1, "verdict": "keep"},
        )
        context_after = client.post(
            "/api/v1/memories/context",
            headers=auth_headers(),
            json={"query": "Provider Boundary Ledger"},
        ).json()
        graph_after = client.get(
            "/api/v1/memories/graph", headers=auth_headers()
        ).json()

    assert listed[0]["review_triage"]["effective_verdict"] == "source_only"
    assert listed[0]["review_triage"]["requires_user_review"] is False
    assert context_before["items"] == []
    assert context_before["automatically_excluded_count"] == 1
    assert graph_before["nodes"] == []
    assert reviewed.status_code == 200
    assert [item["id"] for item in context_after["items"]] == [memory.id]
    assert [item["id"] for item in graph_after["nodes"]] == [memory.id]
