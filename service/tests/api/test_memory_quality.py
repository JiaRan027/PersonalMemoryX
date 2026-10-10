from __future__ import annotations

from personalmemory.domain.memory.fingerprint import (
    exact_content_fingerprint,
    semantic_fingerprint_value,
)
from personalmemory.infrastructure.database.models import MemoryObjectRecord
from sqlalchemy import select

from service.tests.api.test_api_contract import auth_headers, make_client


def _record(
    *,
    memory_id: str,
    statement: str,
    memory_type: str = "fact",
    namespace: str = "codex",
    reason: str | None = "Evidence from the source.",
    payload: dict[str, object] | None = None,
    status: str = "active",
) -> MemoryObjectRecord:
    return MemoryObjectRecord(
        id=memory_id,
        memory_space_id="space_default",
        type=memory_type,
        statement=statement,
        reason=reason,
        namespace=namespace,
        sensitivity="personal",
        epistemic_state="inferred",
        status=status,
        semantic_fingerprint=semantic_fingerprint_value(memory_type, namespace, statement),
        exact_content_fingerprint=exact_content_fingerprint(memory_type, statement),
        semantic_key=None,
        payload=payload or {},
        revision=1,
    )


def test_codex_profile_maps_only_durable_user_sections() -> None:
    """Raw Codex indexes stay out of Core; workflow translation remains separate."""
    from personalmemory.domain.connector.profile import get_builtin_profile

    profile = get_builtin_profile("codex_memory_markdown")

    assert {
        key: value.value if hasattr(value, "value") else value
        for key, value in (profile.section_types or {}).items()
    } == {
        "userprofile": "fact",
        "userpreferences": "preference",
    }
    assert profile.skip_unmapped_sections is True


def test_memory_quality_is_dynamic_explainable_and_preserves_trusted_sources(tmp_path) -> None:
    source_payload = {
        "source_signal": "agent_memory",
        "authority_verification": "agent_native_unverified",
        "source_section": "General Tips",
        "quality_issues": [{"code": "EXISTING_ISSUE"}],
    }
    with make_client(tmp_path) as client:
        with client.app.state.database.session_factory.begin() as session:
            session.add(
                _record(
                    memory_id="memory_workflow_section",
                    statement="Keep verification status precise.",
                    payload=source_payload,
                )
            )
            session.add(
                _record(
                    memory_id="memory_workflow_symptom",
                    statement="Symptom: a source import failed.",
                    payload={
                        "source_signal": "agent_memory",
                        "authority_verification": "agent_native_unverified",
                        "source_section": "User Profile",
                    },
                )
            )
            session.add(
                _record(
                    memory_id="memory_workflow_task",
                    statement="[Task 2] verify the next migration step.",
                    payload={
                        "source_signal": "agent_memory",
                        "authority_verification": "agent_native_unverified",
                        "source_section": "User Preferences",
                    },
                )
            )
            session.add(
                _record(
                    memory_id="memory_workflow_marker_only",
                    statement="Symptom: legacy raw index marker.",
                    payload={},
                    namespace="legacy",
                )
            )
            session.add(
                _record(
                    memory_id="memory_missing_reason",
                    statement="The system uses the local Core.",
                    memory_type="decision",
                    reason=None,
                    namespace="decisions",
                )
            )
            session.add(
                _record(
                    memory_id="memory_trusted_task",
                    statement="[Task 3] user explicitly requested this workflow.",
                    payload={
                        "source_signal": "agent_hook",
                        "authority_verification": "user_explicit",
                        "evidence_actor": "user",
                    },
                    namespace="trusted",
                )
            )
            session.add(
                _record(
                    memory_id="memory_profile",
                    statement="The user works on PersonalMemory.",
                    payload={
                        "source_signal": "agent_memory",
                        "authority_verification": "agent_native_unverified",
                        "source_section": "User Profile",
                    },
                    namespace="profile",
                )
            )
            session.add(
                _record(
                    memory_id="memory_deleted_workflow",
                    statement="Symptom: deleted source note.",
                    payload={
                        "source_signal": "agent_memory",
                        "authority_verification": "agent_native_unverified",
                        "source_section": "General Tips",
                    },
                    status="deleted",
                )
            )

        with client.app.state.database.session_factory() as session:
            before = {
                memory.id: (memory.payload, memory.revision)
                for memory in session.scalars(select(MemoryObjectRecord)).all()
            }
        response = client.get("/api/v1/memories", headers=auth_headers())
        assert response.status_code == 200, response.text
        items = {item["id"]: item for item in response.json()["items"]}

        section_codes = {
            issue["code"]
            for issue in items["memory_workflow_section"]["quality_issues"]
        }
        assert section_codes >= {"EXISTING_ISSUE", "AGENT_WORKFLOW_NOTE"}
        assert any(
            issue.get("reason") == "source_section=General Tips"
            for issue in items["memory_workflow_section"]["quality_issues"]
        )
        assert any(
            issue["code"] == "AGENT_WORKFLOW_NOTE"
            for key in ("memory_workflow_symptom", "memory_workflow_task")
            for issue in items[key]["quality_issues"]
        )
        assert any(
            issue["code"] == "AGENT_WORKFLOW_NOTE"
            for issue in items["memory_workflow_marker_only"]["quality_issues"]
        )
        assert {
            issue["code"] for issue in items["memory_missing_reason"]["quality_issues"]
        } >= {"MISSING_DECISION_RATIONALE"}
        assert items["memory_trusted_task"]["quality_issues"] == []
        assert items["memory_profile"]["quality_issues"] == []
        assert items["memory_workflow_section"]["review_triage"] == {
            "suggested_verdict": "source_only",
            "effective_verdict": "source_only",
            "confidence": "high",
            "reason_codes": ["AGENT_WORKFLOW_NOTE", "QUALITY:EXISTING_ISSUE"],
            "requires_user_review": False,
            "delivery_eligible": False,
            "graph_eligible": False,
            "scope_hint": None,
            "overridden_by_user": False,
            "memory_revision": 1,
        }
        assert items["memory_profile"]["review_triage"]["effective_verdict"] == "needs_context"
        assert items["memory_profile"]["review_triage"]["requires_user_review"] is True
        assert "memory_deleted_workflow" not in items

        with client.app.state.database.session_factory() as session:
            after = {
                memory.id: (memory.payload, memory.revision)
                for memory in session.scalars(select(MemoryObjectRecord)).all()
            }
        assert before == after


def test_memory_quality_merges_cross_namespace_issue(tmp_path) -> None:
    with make_client(tmp_path) as client:
        first = client.post(
            "/api/v1/memories",
            headers=auth_headers(),
            json={
                "type": "fact",
                "statement": "The local Core remains authoritative.",
                "namespace": "profile",
            },
        )
        assert first.status_code == 201, first.text
        second = client.post(
            "/api/v1/memories",
            headers=auth_headers(),
            json={
                "type": "fact",
                "statement": "The local Core remains authoritative.",
                "namespace": "codex",
            },
        )
        assert second.status_code in {200, 201}, second.text
        items = client.get("/api/v1/memories", headers=auth_headers()).json()["items"]

    assert len(items) == 2
    assert all(
        any(issue["code"] == "CROSS_NAMESPACE_EXACT_DUPLICATE" for issue in item["quality_issues"])
        for item in items
    )
