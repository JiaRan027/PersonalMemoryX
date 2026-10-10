from fastapi.testclient import TestClient


def make_client(tmp_path):
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


def create_memory(client: TestClient, namespace: str, statement: str) -> str:
    response = client.post(
        "/api/v1/memories",
        headers=auth_headers(),
        json={"type": "fact", "statement": statement, "namespace": namespace},
    )
    assert response.status_code == 201
    return response.json()["memory"]["id"]


def test_lists_active_namespaces_with_memory_counts(tmp_path) -> None:
    with make_client(tmp_path) as client:
        create_memory(client, "projects/personalmemory", "The project uses a local core.")
        create_memory(client, "projects/personalmemory", "Capsules have a token budget.")
        deleted_id = create_memory(client, "archive", "This item will be deleted.")
        create_memory(client, "codex", "Codex can consume a continuity manifest.")
        assert client.delete(
            f"/api/v1/memories/{deleted_id}", headers=auth_headers()
        ).status_code == 204

        response = client.get("/api/v1/memories/namespaces", headers=auth_headers())

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {"namespace": "projects/personalmemory", "memory_count": 2},
            {"namespace": "codex", "memory_count": 1},
        ],
        "total": 3,
    }


def test_namespace_summary_requires_session_token(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/api/v1/memories/namespaces")

    assert response.status_code == 401
