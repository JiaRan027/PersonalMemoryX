import pytest
from fastapi.testclient import TestClient
from personalmemory.api.app import create_app
from personalmemory.settings import Settings


def make_app(tmp_path):
    return create_app(
        Settings(
            data_dir=tmp_path / "data",
            api_token="test-token",
            enable_background_sync=False,
        )
    )


def test_shutdown_requires_session_token(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/v1/system/shutdown")

    assert response.status_code == 401


def test_shutdown_requests_server_exit(tmp_path):
    app = make_app(tmp_path)
    requested = []
    app.state.request_shutdown = lambda: requested.append(True)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/system/shutdown",
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 202
    assert requested == [True]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/system/runtime"),
        ("POST", "/api/v1/system/sync"),
        ("PATCH", "/api/v1/system/runtime"),
    ],
)
def test_runtime_controls_require_session_token(tmp_path, method, path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.request(method, path, json={})

    assert response.status_code == 401


def test_runtime_control_api_updates_state_and_runs_sync(tmp_path):
    app = make_app(tmp_path)
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(app) as client:
        initial = client.get("/api/v1/system/runtime", headers=headers)
        updated = client.patch(
            "/api/v1/system/runtime",
            headers=headers,
            json={"sync_paused": True, "privacy_mode": True},
        )
        synced = client.post("/api/v1/system/sync", headers=headers)

    assert initial.json() == {"sync_paused": False, "privacy_mode": False}
    assert updated.json() == {"sync_paused": True, "privacy_mode": True}
    assert synced.json() == {"ingested": 0, "projected": 0, "failed": 0}
