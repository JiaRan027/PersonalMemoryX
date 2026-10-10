from __future__ import annotations

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


def test_recognize_endpoint_lists_sources(tmp_path) -> None:
    home = tmp_path / "home"
    (home / ".codex" / "memories").mkdir(parents=True)
    (home / ".codex" / "memories" / "MEMORY.md").write_text("# memory\n", encoding="utf-8")
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "history.jsonl").write_text("{}\n", encoding="utf-8")

    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/connectors/recognize",
            json={"roots": {"home": str(home)}},
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    payload = response.json()
    codex_notes = [
        item
        for item in payload["items"]
        if item["agent"] == "codex" and item["kind"] == "memory_note"
    ]
    assert codex_notes
    assert codex_notes[0]["format"] == "markdown"
    claude_history = [
        item
        for item in payload["items"]
        if item["agent"] == "claude_code" and item["kind"] == "conversation_log"
    ]
    assert claude_history
    assert claude_history[0]["format"] == "jsonl"
    assert payload["summary"]["agent_count"] >= 2


def test_recognize_requires_session_token(tmp_path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/v1/connectors/recognize", json={})
    assert response.status_code == 401


def test_recognize_rejects_unknown_root(tmp_path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/connectors/recognize",
            json={"roots": {"c:/tmp": "x"}},
            headers={"Authorization": "Bearer test-token"},
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "UNKNOWN_ROOT"
