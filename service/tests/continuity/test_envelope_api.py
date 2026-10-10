from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient
from personalmemory.infrastructure.database.models import (
    MemoryCandidateRecord,
    MemoryObjectRecord,
    SourceRevisionRecord,
)
from sqlalchemy import select

HEADERS = {"Authorization": "Bearer test-token"}


def make_client(tmp_path) -> TestClient:
    from personalmemory.api.app import create_app
    from personalmemory.settings import Settings

    return TestClient(
        create_app(
            Settings(
                data_dir=tmp_path / "data",
                api_token="test-token",
                enable_background_sync=False,
                llm_enabled=False,
            )
        )
    )


def create_envelope_connector(client: TestClient, namespace="projects/atlas") -> str:
    response = client.post(
        "/api/v1/connectors",
        headers=HEADERS,
        json={
            "name": "Generic Continuity Envelope",
            "profile_id": "continuity_envelope_api",
            "source_path": None,
            "target_path": None,
            "namespace": namespace,
            "read_enabled": True,
            "write_enabled": False,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def envelope_payload(
    connector_id: str,
    *,
    text: str = "I decided to use SQLite because local deployment is simpler.",
    idempotency_key: str = "turn-001",
    namespace: str = "projects/atlas",
    authority: dict[str, object] | None = None,
) -> dict[str, object]:
    evidence_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "connector_id": connector_id,
        "envelope": {
            "schema_version": "1",
            "source_kind": "interaction",
            "source_family": "generic_agent",
            "capture_mode": "structured_api",
            "capabilities": {
                "has_actor_roles": True,
                "has_session": True,
                "has_timestamps": True,
                "has_tool_calls": False,
                "has_attachments": False,
                "is_realtime": True,
            },
            "evidence": {
                "locator": "generic://session-1/turn-1",
                "digest": evidence_digest,
                "interaction_id": "turn-1",
                "message_ids": ["message-1"],
                "evidence_kind": "message",
                "actor_kind": "user",
            },
            "evidence_references": [],
            "authority": authority
            or {
                "actor_kind": "user",
                "assertion_kind": "explicit",
                "actor_id": "local-user",
            },
            "namespace": namespace,
            "idempotency_key": idempotency_key,
            "user_text": text,
            "assistant_text": "Understood.",
            "session_id": "session-1",
        },
    }


def test_exact_replay_is_idempotent_and_changed_reuse_conflicts(tmp_path) -> None:
    with make_client(tmp_path) as client:
        connector_id = create_envelope_connector(client)
        payload = envelope_payload(connector_id)
        created = client.post("/api/v1/envelopes", headers=HEADERS, json=payload)
        replay = client.post("/api/v1/envelopes", headers=HEADERS, json=payload)
        changed = client.post(
            "/api/v1/envelopes",
            headers=HEADERS,
            json=envelope_payload(
                connector_id,
                text="I decided to use Postgres because remote collaboration is required.",
            ),
        )

        with client.app.state.database.session_factory() as session:
            revisions = session.scalars(select(SourceRevisionRecord)).all()
            memories = session.scalars(select(MemoryObjectRecord)).all()

    assert created.status_code == 201
    assert created.json()["duplicate"] is False
    assert replay.status_code == 200
    assert replay.json()["duplicate"] is True
    assert replay.json()["envelope_id"] == created.json()["envelope_id"]
    assert replay.json()["operation_id"] == created.json()["operation_id"]
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "IDEMPOTENCY_CONTENT_CONFLICT"
    assert len(revisions) == len(memories) == 1


def test_different_keys_for_same_content_remain_bound_after_deduplication(tmp_path) -> None:
    with make_client(tmp_path) as client:
        connector_id = create_envelope_connector(client)
        first = client.post(
            "/api/v1/envelopes",
            headers=HEADERS,
            json=envelope_payload(connector_id, idempotency_key="alias-a"),
        )
        alias = client.post(
            "/api/v1/envelopes",
            headers=HEADERS,
            json=envelope_payload(connector_id, idempotency_key="alias-b"),
        )
        changed = client.post(
            "/api/v1/envelopes",
            headers=HEADERS,
            json=envelope_payload(
                connector_id,
                idempotency_key="alias-b",
                text="I decided to use Postgres for a hosted deployment.",
            ),
        )

    assert first.status_code == 201
    assert alias.status_code == 200
    assert alias.json()["duplicate"] is True
    assert alias.json()["operation_id"] == first.json()["operation_id"]
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "IDEMPOTENCY_CONTENT_CONFLICT"


def test_external_authority_is_self_attested_and_agent_inference_is_provisional(tmp_path) -> None:
    with make_client(tmp_path) as client:
        connector_id = create_envelope_connector(client)
        explicit = client.post(
            "/api/v1/envelopes",
            headers=HEADERS,
            json=envelope_payload(connector_id, idempotency_key="explicit-1"),
        )
        inferred = client.post(
            "/api/v1/envelopes",
            headers=HEADERS,
            json=envelope_payload(
                connector_id,
                text="I prefer TypeScript for application services.",
                idempotency_key="inferred-1",
                authority={
                    "actor_kind": "agent",
                    "assertion_kind": "inferred",
                    "actor_id": "generic_agent",
                },
            ),
        )
        with client.app.state.database.session_factory() as session:
            memories = session.scalars(
                select(MemoryObjectRecord).order_by(MemoryObjectRecord.created_at)
            ).all()
            candidates = session.scalars(
                select(MemoryCandidateRecord).order_by(MemoryCandidateRecord.created_at)
            ).all()

    assert explicit.status_code == inferred.status_code == 201
    assert explicit.json()["counts"]["provisional"] == 1
    assert inferred.json()["counts"]["provisional"] == 1
    assert [memory.epistemic_state for memory in memories] == ["inferred", "inferred"]
    assert candidates[0].draft["payload"]["authority_verification"] == "self_attested"
    assert candidates[0].draft["payload"]["semantic_authority"] == {
        "actor_kind": "user",
        "assertion_kind": "observed",
        "actor_id": "local-user",
    }
    assert candidates[1].reason_code == "AGENT_INFERENCE"
    assert candidates[1].draft["payload"]["semantic_authority"] == {
        "actor_kind": "agent",
        "assertion_kind": "inferred",
        "actor_id": "generic_agent",
    }


def test_secret_in_evidence_metadata_is_rejected(tmp_path) -> None:
    with make_client(tmp_path) as client:
        connector_id = create_envelope_connector(client)
        payload = envelope_payload(connector_id, idempotency_key="metadata-secret-1")
        payload["envelope"]["evidence"]["locator"] = (
            "https://example.test/source?api_token=TEST_ONLY_REDACTED_VALUE"
        )
        response = client.post("/api/v1/envelopes", headers=HEADERS, json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "SECRET_DETECTED"


def test_assistant_claim_cannot_become_a_user_decision(tmp_path) -> None:
    with make_client(tmp_path) as client:
        connector_id = create_envelope_connector(client)
        payload = envelope_payload(
            connector_id,
            text="Please compare SQLite and Postgres.",
            idempotency_key="assistant-claim-1",
        )
        payload["envelope"]["assistant_text"] = (
            "I decided to use Postgres because it sounds more scalable."
        )
        response = client.post("/api/v1/envelopes", headers=HEADERS, json=payload)
        with client.app.state.database.session_factory() as session:
            memories = session.scalars(select(MemoryObjectRecord)).all()

    assert response.status_code == 201
    assert response.json()["counts"] == {
        "accepted": 0,
        "provisional": 0,
        "quarantined": 0,
        "restricted": 0,
        "discarded": 0,
    }
    assert memories == []


def test_namespace_secret_and_profile_boundaries_are_enforced(tmp_path) -> None:
    with make_client(tmp_path) as client:
        connector_id = create_envelope_connector(client)
        denied = client.post(
            "/api/v1/envelopes",
            headers=HEADERS,
            json=envelope_payload(connector_id, namespace="projects/other"),
        )
        secret = client.post(
            "/api/v1/envelopes",
            headers=HEADERS,
            json=envelope_payload(
                connector_id,
                text="I decided to use api_token=TEST_ONLY_REDACTED_VALUE for testing.",
                idempotency_key="secret-1",
            ),
        )

        markdown = tmp_path / "memory.md"
        markdown.write_text("# Memory\n", encoding="utf-8")
        connector_response = client.post(
            "/api/v1/connectors",
            headers=HEADERS,
            json={
                "name": "Markdown",
                "profile_id": "generic_markdown_readonly",
                "source_path": str(markdown),
                "target_path": None,
                "namespace": "projects/atlas",
                "read_enabled": True,
                "write_enabled": False,
            },
        )
        mismatch = client.post(
            "/api/v1/envelopes",
            headers=HEADERS,
            json=envelope_payload(connector_response.json()["id"]),
        )

    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "NAMESPACE_SCOPE_DENIED"
    assert secret.status_code == 422
    assert secret.json()["detail"]["code"] == "SECRET_DETECTED"
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "CONNECTOR_PROFILE_MISMATCH"


def test_missing_evidence_private_fields_size_and_auth_are_rejected(tmp_path) -> None:
    with make_client(tmp_path) as client:
        connector_id = create_envelope_connector(client)
        missing = envelope_payload(connector_id)
        del missing["envelope"]["evidence"]
        private = envelope_payload(connector_id, idempotency_key="private-1")
        private["envelope"]["thinking"] = "hidden chain of thought"
        oversized = envelope_payload(
            connector_id,
            text="I prefer " + ("x" * (32 * 1024)),
            idempotency_key="large-1",
        )

        missing_response = client.post(
            "/api/v1/envelopes", headers=HEADERS, json=missing
        )
        private_response = client.post(
            "/api/v1/envelopes", headers=HEADERS, json=private
        )
        oversized_response = client.post(
            "/api/v1/envelopes", headers=HEADERS, json=oversized
        )
        unauthorized = client.post(
            "/api/v1/envelopes", json=envelope_payload(connector_id)
        )

    assert missing_response.status_code == 422
    assert private_response.status_code == 422
    assert oversized_response.status_code == 422
    assert unauthorized.status_code == 401


def test_context_reports_actual_lexical_dynamic_delivery_capability(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/memories/context",
            headers=HEADERS,
            json={"query": "architecture decision"},
        )

    assert response.status_code == 200
    assert response.json()["delivery_receipt"] == {
        "requested_mode": "lexical",
        "effective_mode": "lexical",
        "status": "ok",
        "cache_class": "dynamic",
        "degradation_reason": None,
    }


@pytest.mark.parametrize("requested_mode", ["embedding", "hybrid"])
def test_context_fails_open_when_embedding_capability_is_unavailable(
    tmp_path, requested_mode: str
) -> None:
    with make_client(tmp_path) as client:
        created = client.post(
            "/api/v1/memories",
            headers=HEADERS,
            json={
                "type": "decision",
                "statement": "The architecture keeps the Continuity Core platform neutral.",
                "reason": "Agents are replaceable while continuity is not.",
            },
        )
        lexical = client.post(
            "/api/v1/memories/context",
            headers=HEADERS,
            json={"query": "architecture decision", "requested_mode": "lexical"},
        )
        response = client.post(
            "/api/v1/memories/context",
            headers=HEADERS,
            json={"query": "architecture decision", "requested_mode": requested_mode},
        )

    assert created.status_code == 201
    assert lexical.status_code == 200
    assert response.status_code == 200
    assert response.json()["items"] == lexical.json()["items"]
    assert response.json()["delivery_receipt"] == {
        "requested_mode": requested_mode,
        "effective_mode": "lexical",
        "status": "degraded",
        "cache_class": "dynamic",
        "degradation_reason": "embedding_unavailable",
    }


def test_context_rejects_unknown_retrieval_mode(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/memories/context",
            headers=HEADERS,
            json={"query": "architecture decision", "requested_mode": "vector"},
        )

    assert response.status_code == 422
