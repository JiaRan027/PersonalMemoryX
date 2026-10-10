from __future__ import annotations

import json

from personalmemory.application.review_decisions import (
    REVIEW_DECISIONS_FILE_NAME,
    REVIEW_DECISIONS_SCHEMA_VERSION,
    load_review_decisions,
)

from service.tests.api.test_memories_create import auth_headers, make_client


def test_review_decision_forms_gold_set_without_mutating_memory(tmp_path) -> None:
    with make_client(tmp_path) as client:
        created = client.post(
            "/api/v1/memories",
            headers=auth_headers(),
            json={
                "type": "preference",
                "statement": "Do not guess without evidence.",
            },
        ).json()["memory"]

        response = client.patch(
            f"/api/v1/memories/{created['id']}/review",
            headers=auth_headers(),
            json={
                "expected_revision": 1,
                "verdict": "needs_context",
                "note": "可能来自验收提示词，需要回看原始会话。",
            },
        )
        assert response.status_code == 200
        assert response.json()["verdict"] == "needs_context"

        listed = client.get("/api/v1/memories", headers=auth_headers()).json()["items"][0]
        fetched = client.get(
            f"/api/v1/memories/{created['id']}", headers=auth_headers()
        ).json()

    assert listed["statement"] == "Do not guess without evidence."
    assert listed["revision"] == 1
    assert listed["epistemic_state"] == created["epistemic_state"]
    assert listed["review_decision"]["verdict"] == "needs_context"
    assert fetched["review_decision"]["note"] == "可能来自验收提示词，需要回看原始会话。"


def test_project_experience_uses_provenance_or_namespace_as_scope_hint(tmp_path) -> None:
    with make_client(tmp_path) as client:
        created = client.post(
            "/api/v1/memories",
            headers=auth_headers(),
            json={
                "type": "insight",
                "statement": "Keep a module-specific retry boundary.",
                "namespace": "projects/demo",
            },
        ).json()["memory"]
        response = client.patch(
            f"/api/v1/memories/{created['id']}/review",
            headers=auth_headers(),
            json={"expected_revision": 1, "verdict": "project_experience"},
        )

    assert response.status_code == 200
    assert response.json()["scope_hint"] == "projects/demo"


def test_review_decision_rejects_invalid_verdict_and_stale_revision(tmp_path) -> None:
    with make_client(tmp_path) as client:
        created = client.post(
            "/api/v1/memories",
            headers=auth_headers(),
            json={"type": "fact", "statement": "A test record."},
        ).json()["memory"]
        invalid = client.patch(
            f"/api/v1/memories/{created['id']}/review",
            headers=auth_headers(),
            json={"expected_revision": 1, "verdict": "delete"},
        )
        stale = client.patch(
            f"/api/v1/memories/{created['id']}/review",
            headers=auth_headers(),
            json={"expected_revision": 99, "verdict": "keep"},
        )

    assert invalid.status_code == 422
    assert stale.status_code == 409


def test_review_decision_loader_fails_closed(tmp_path) -> None:
    path = tmp_path / REVIEW_DECISIONS_FILE_NAME
    path.write_text("{broken", encoding="utf-8")
    assert load_review_decisions(tmp_path) == {}

    path.write_text(
        json.dumps(
            {
                "schema_version": REVIEW_DECISIONS_SCHEMA_VERSION,
                "items": {
                    "memory_01": {
                        "verdict": "keep",
                        "memory_revision": 1,
                        "reviewed_at": "2026-08-11T00:00:00+00:00",
                        "note": None,
                        "scope_hint": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    assert load_review_decisions(tmp_path)["memory_01"]["verdict"] == "keep"
