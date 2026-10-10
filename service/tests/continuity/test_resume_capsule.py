from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from personalmemory.application.capsule import ResumeCapsuleService
from personalmemory.domain.capsule import ResumeCapsule
from personalmemory.infrastructure.database.models import MemoryObjectRecord


def make_client(tmp_path) -> TestClient:
    from personalmemory.api.app import create_app
    from personalmemory.settings import Settings

    settings = Settings(
        data_dir=tmp_path / "data",
        api_token="desktop-session-token",
        enable_background_sync=False,
    )
    return TestClient(create_app(settings))


def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer desktop-session-token"}


def create_memory(
    client: TestClient,
    *,
    statement: str = "Atlas uses a governed Core as its only truth source.",
    reason: str = "Delivery artifacts must remain rebuildable.",
) -> dict[str, object]:
    response = client.post(
        "/api/v1/memories",
        headers=auth_headers(),
        json={
            "type": "decision",
            "statement": statement,
            "reason": reason,
            "namespace": "projects/atlas",
        },
    )
    assert response.status_code == 201
    return response.json()["memory"]


def build_capsule(client: TestClient, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "selection": {
            "query": "Atlas governed Core truth source",
            "namespaces": ["projects/atlas"],
            "allowed_types": ["decision", "insight"],
            "max_sensitivity": "personal",
            "limit": 6,
        },
        "checkpoint": {
            "current_task": "Implement bounded Resume Capsule v0.",
            "verified_status": "Core retrieval passed focused tests.",
            "next_action": "Run a controlled recovery acceptance.",
        },
        "ttl_seconds": 900,
        "token_budget": 600,
    }
    payload.update(overrides)
    response = client.post(
        "/api/v1/capsules", headers=auth_headers(), json=payload
    )
    assert response.status_code == 200, response.text
    return response.json()


def validate_capsule(client: TestClient, capsule: dict[str, object]) -> dict[str, object]:
    response = client.post(
        "/api/v1/capsules/validate",
        headers=auth_headers(),
        json={"capsule": capsule},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_builds_bounded_rebuildable_capsule_without_persistence(tmp_path) -> None:
    with make_client(tmp_path) as client:
        memory = create_memory(client)
        result = build_capsule(client)
        capsule = result["capsule"]
        validation = validate_capsule(client, capsule)

    assert capsule["schema_version"] == "resume-capsule/0"
    assert capsule["capsule_id"].startswith("capsule_")
    assert capsule["estimated_tokens"] <= capsule["token_budget"] == 600
    assert capsule["namespace_scope"] == ["projects/atlas"]
    assert capsule["memory_references"][0]["memory_id"] == memory["id"]
    assert capsule["memory_references"][0]["revision"] == 1
    assert "current_task=Implement bounded Resume Capsule v0." in result["delivery_text"]
    assert validation == {
        "valid": True,
        "expired": False,
        "stale": False,
        "checked_at": validation["checked_at"],
        "issues": [],
    }


def test_rejects_secret_checkpoint_and_private_payload_fields(tmp_path) -> None:
    with make_client(tmp_path) as client:
        create_memory(client)
        rejected_reason = client.post(
            "/api/v1/memories",
            headers=auth_headers(),
            json={
                "type": "decision",
                "statement": "Atlas secret reason boundary must be enforced.",
                "reason": "api_token=abcdefghijklmnop",
                "namespace": "projects/atlas",
            },
        )
        hidden_reason_id = "memory_legacy_secret_reason"
        with client.app.state.database.session_factory.begin() as session:
            session.add(
                MemoryObjectRecord(
                    id=hidden_reason_id,
                    memory_space_id="space_default",
                    type="decision",
                    statement="Atlas legacy secret reason boundary must be enforced.",
                    reason="api_token=abcdefghijklmnop",
                    namespace="projects/atlas",
                    sensitivity="personal",
                    epistemic_state="asserted",
                    status="active",
                    semantic_fingerprint=hashlib.sha256(
                        b"legacy-secret-reason"
                    ).hexdigest(),
                    semantic_key=None,
                    payload={"source": "legacy_test_fixture"},
                    revision=1,
                )
            )
        oversized_reason = create_memory(
            client,
            statement="Atlas oversized imported reason boundary must be enforced.",
            reason="evidence detail " * 180,
        )
        secret = client.post(
            "/api/v1/capsules",
            headers=auth_headers(),
            json={
                "selection": {"query": "Atlas", "namespaces": ["projects/atlas"]},
                "checkpoint": {"current_task": "api_token=abcdefghijklmnop"},
            },
        )
        raw_tools = client.post(
            "/api/v1/capsules",
            headers=auth_headers(),
            json={
                "selection": {"query": "Atlas", "namespaces": ["projects/atlas"]},
                "checkpoint": {"raw_tool_payload": {"command": "do not persist"}},
            },
        )
        governed = build_capsule(client)
        bounded = client.post(
            "/api/v1/capsules",
            headers=auth_headers(),
            json={
                "selection": {
                    "query": "Atlas oversized imported reason boundary",
                    "namespaces": ["projects/atlas"],
                    "limit": 1,
                },
                "token_budget": 1600,
            },
        )

    assert secret.status_code == 400
    assert secret.json()["detail"]["code"] == "SECRET_DETECTED"
    assert rejected_reason.status_code == 409
    assert raw_tools.status_code == 422
    assert "api_token=abcdefghijklmnop" not in governed["delivery_text"]
    assert (
        f"memory:{hidden_reason_id}:secret_detected"
        in governed["capsule"]["dropped_fields"]
    )
    assert bounded.status_code == 200
    assert (
        f"memory:{oversized_reason['id']}:text_bounds"
        in bounded.json()["capsule"]["dropped_fields"]
    )


def test_capsule_api_requires_local_session_authorization(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/capsules",
            json={"selection": {"query": "Atlas"}},
        )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


def test_capsule_drops_whole_memories_to_stay_inside_delivery_budget(tmp_path) -> None:
    with make_client(tmp_path) as client:
        for index in range(6):
            create_memory(
                client,
                statement=(
                    f"Atlas governed Core decision {index}: "
                    + "bounded delivery preserves current authoritative state. " * 8
                ),
                reason="Full memory objects must never be cut in the middle.",
            )
        result = build_capsule(client, token_budget=256)

    capsule = result["capsule"]
    assert capsule["estimated_tokens"] <= 256
    assert any(
        item.endswith(":token_budget") for item in capsule["dropped_fields"]
    )
    for reference in capsule["memory_references"]:
        assert reference["statement"] in result["delivery_text"]


def test_expired_capsule_is_invalid_even_when_core_is_unchanged(tmp_path) -> None:
    with make_client(tmp_path) as client:
        create_memory(client)
        built = build_capsule(client)["capsule"]
        capsule = ResumeCapsule.model_validate(built)
        result = ResumeCapsuleService(client.app.state.database).validate(
            capsule, now=capsule.expires_at + timedelta(seconds=1)
        )

    assert result.valid is False
    assert result.expired is True
    assert result.stale is False
    assert [issue.code for issue in result.issues] == ["CAPSULE_EXPIRED"]


def test_revision_invalidates_old_capsule_and_rebuild_uses_current_core(tmp_path) -> None:
    with make_client(tmp_path) as client:
        memory = create_memory(client)
        old = build_capsule(client)["capsule"]
        revised = client.patch(
            f"/api/v1/memories/{memory['id']}",
            headers=auth_headers(),
            json={
                "expected_revision": 1,
                "statement": "Atlas keeps governed Core as truth; capsules are disposable.",
                "reason": "A capsule is delivery state, not durable authority.",
            },
        )
        validation = validate_capsule(client, old)
        rebuilt_response = client.post(
            "/api/v1/capsules/rebuild",
            headers=auth_headers(),
            json={"capsule": old},
        )

    assert revised.status_code == 200
    assert validation["valid"] is False
    assert validation["stale"] is True
    assert "MEMORY_REVISION_STALE" in {item["code"] for item in validation["issues"]}
    assert rebuilt_response.status_code == 200
    rebuilt = rebuilt_response.json()["capsule"]
    assert rebuilt["capsule_id"] != old["capsule_id"]
    assert rebuilt["memory_references"][0]["revision"] == 2
    assert "capsules are disposable" in rebuilt["memory_references"][0]["statement"]
    assert old["memory_references"][0]["statement"] not in rebuilt_response.json()[
        "delivery_text"
    ]


def test_deletion_invalidates_old_capsule_and_does_not_resurrect_on_rebuild(tmp_path) -> None:
    with make_client(tmp_path) as client:
        memory = create_memory(client)
        old = build_capsule(client)["capsule"]
        deleted = client.delete(
            f"/api/v1/memories/{memory['id']}", headers=auth_headers()
        )
        validation = validate_capsule(client, old)
        rebuilt = client.post(
            "/api/v1/capsules/rebuild",
            headers=auth_headers(),
            json={"capsule": old},
        ).json()

    assert deleted.status_code == 204
    assert validation["valid"] is False
    assert {item["code"] for item in validation["issues"]} >= {"MEMORY_DELETED"}
    assert rebuilt["capsule"]["memory_references"] == []
    assert old["memory_references"][0]["statement"] not in rebuilt["delivery_text"]


def test_undo_propagates_to_capsule_validation_and_rebuild(tmp_path) -> None:
    source = tmp_path / "ATLAS.md"
    source.write_text(
        "## Decision\n- Atlas delivery always comes from governed Core.\n",
        encoding="utf-8",
    )
    with make_client(tmp_path) as client:
        connector = client.post(
            "/api/v1/connectors",
            headers=auth_headers(),
            json={
                "name": "Atlas import",
                "profile_id": "generic_markdown_readonly",
                "source_path": str(source),
                "target_path": None,
                "namespace": "projects/atlas",
                "read_enabled": True,
                "write_enabled": False,
            },
        ).json()
        synced = client.post(
            f"/api/v1/connectors/{connector['id']}/sync", headers=auth_headers()
        )
        assert synced.status_code == 200
        old = build_capsule(client)["capsule"]
        activity = next(
            item
            for item in client.get(
                "/api/v1/activities", headers=auth_headers()
            ).json()["items"]
            if item["kind"] == "source_ingested"
        )
        undone = client.post(
            f"/api/v1/activities/{activity['operation_id']}/undo",
            headers=auth_headers(),
        )
        validation = validate_capsule(client, old)
        rebuilt = client.post(
            "/api/v1/capsules/rebuild",
            headers=auth_headers(),
            json={"capsule": old},
        ).json()["capsule"]

    assert undone.status_code == 200
    assert undone.json()["undone"] == 1
    assert validation["stale"] is True
    assert {item["code"] for item in validation["issues"]} >= {"MEMORY_DELETED"}
    assert rebuilt["memory_references"] == []


def test_missing_checkpoint_is_rendered_as_unknown_boundary(tmp_path) -> None:
    with make_client(tmp_path) as client:
        create_memory(client)
        result = build_capsule(client, checkpoint={})

    capsule = result["capsule"]
    assert capsule["checkpoint"] == {
        "current_task": None,
        "verified_status": None,
        "next_action": None,
    }
    assert capsule["dropped_fields"] == [
        "checkpoint:current_task:not_provided",
        "checkpoint:verified_status:not_provided",
        "checkpoint:next_action:not_provided",
    ]
    assert result["delivery_text"].count("unknown (no governed evidence)") == 3


def test_tampering_is_detected_without_trusting_capsule_copy(tmp_path) -> None:
    with make_client(tmp_path) as client:
        create_memory(client)
        capsule = build_capsule(client)["capsule"]
        capsule["checkpoint"]["next_action"] = "Skip governance and publish directly."
        validation = validate_capsule(client, capsule)

    assert validation["valid"] is False
    assert "CAPSULE_INTEGRITY_FAILED" in {
        item["code"] for item in validation["issues"]
    }


def test_capsule_file_export_auto_names_utf8_and_never_overwrites(tmp_path) -> None:
    export_dir = tmp_path / "capsules"
    export_dir.mkdir()
    with make_client(tmp_path) as client:
        create_memory(
            client,
            statement="中文证据：Memory Core 是唯一真相源。",
            reason="交付文件必须保留原始中文。",
        )
        built = build_capsule(
            client,
            checkpoint={
                "current_task": "验证中文 Capsule 文件",
                "verified_status": "API 测试通过",
                "next_action": "交给新的 Agent",
            },
        )
        first = client.post(
            "/api/v1/capsules/export",
            headers=auth_headers(),
            json={"capsule": built["capsule"], "output_path": str(export_dir)},
        )
        second = client.post(
            "/api/v1/capsules/export",
            headers=auth_headers(),
            json={"capsule": built["capsule"], "output_path": str(export_dir)},
        )
        explicit_path = export_dir / "fixed-capsule.md"
        fixed_first = client.post(
            "/api/v1/capsules/export",
            headers=auth_headers(),
            json={"capsule": built["capsule"], "path": str(explicit_path)},
        )
        fixed_second = client.post(
            "/api/v1/capsules/export",
            headers=auth_headers(),
            json={"capsule": built["capsule"], "path": str(explicit_path)},
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_path = first.json()["path"]
    second_path = second.json()["path"]
    assert first_path != second_path
    assert first_path.endswith(".md")
    assert second_path.endswith(".md")
    document = Path(first_path).read_text(encoding="utf-8")
    assert "中文证据：Memory Core 是唯一真相源。" in document
    assert "验证中文 Capsule 文件" in document
    assert '"capsule_id"' in document
    assert '"delivery_text"' in document
    assert built["delivery_text"] in document
    assert fixed_first.status_code == 200, fixed_first.text
    assert fixed_second.status_code == 409
    assert fixed_second.json()["detail"]["code"] == "CAPSULE_FILE_EXISTS"
