from fastapi.testclient import TestClient


def test_health_reports_ready(tmp_path):
    from personalmemory import __build_revision__, __version__
    from personalmemory.api.app import create_app
    from personalmemory.settings import Settings

    settings = Settings(data_dir=tmp_path, api_token="test-token")

    with TestClient(create_app(settings)) as client:
        unauthorized = client.get("/api/v1/health")
        response = client.get(
            "/api/v1/health",
            headers={"Authorization": "Bearer test-token"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "ready",
        "version": __version__,
        "build_revision": __build_revision__,
    }


def test_health_preflight_allows_only_desktop_origins(tmp_path):
    from personalmemory.api.app import create_app
    from personalmemory.settings import Settings

    settings = Settings(data_dir=tmp_path, api_token="test-token")
    preflight_headers = {
        "Origin": "http://127.0.0.1:1420",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization,content-type",
    }

    with TestClient(create_app(settings)) as client:
        allowed = client.options("/api/v1/health", headers=preflight_headers)
        denied = client.options(
            "/api/v1/health",
            headers={**preflight_headers, "Origin": "https://example.com"},
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:1420"
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers
