from __future__ import annotations

import sqlite3

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


def write(tmp_path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def memory_count(tmp_path) -> int:
    db = tmp_path / "data" / "data" / "memory.db"
    with sqlite3.connect(db) as connection:
        return connection.execute("SELECT COUNT(*) FROM memory_objects").fetchone()[0]


def auth_headers():
    return {"Authorization": "Bearer test-token"}


def test_extract_preview_returns_candidates_without_writing_db(tmp_path) -> None:
    source = write(
        tmp_path,
        "conversation.txt",
        "我的目标是进入人工智能行业。\n"
        "我最终决定采用结构化查询优先，因为大量个人记忆属于明确事实。\n",
    )
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sources/extract",
            json={"profile_id": "conversation_text", "source_path": source},
            headers=auth_headers(),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["format"] == "conversation"
    types = {item["type"] for item in payload["items"]}
    assert "goal" in types
    assert "decision" in types
    goal = next(item for item in payload["items"] if item["type"] == "goal")
    assert "人工智能行业" in goal["statement"]
    assert goal["evidence"]["start"] < goal["evidence"]["end"]
    assert goal["source_signal"] == "deterministic"
    assert memory_count(tmp_path) == 0


def test_extract_preview_accepts_markdown_profile(tmp_path) -> None:
    source = write(tmp_path, "notes.md", "## 能力\n- 我掌握 Python，用于数据分析。\n")
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sources/extract",
            json={"profile_id": "generic_markdown_readonly", "source_path": source},
            headers=auth_headers(),
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "markdown"
    skill = next(item for item in payload["items"] if item["type"] == "skill")
    assert skill["source_signal"] == "agent_memory"
    assert skill["evidence"] == {"start": 2, "end": 2}


def test_extract_preview_merges_markdown_directory_with_traceable_warnings(tmp_path) -> None:
    source = tmp_path / "memory"
    source.mkdir()
    useful = source / "useful.md"
    oversized = source / "oversized.md"
    useful.write_text(
        "---\ntype: feedback\n---\nPrefer local-first storage.\n",
        encoding="utf-8",
    )
    oversized.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    (source / "ignored.txt").write_text("I decided to ignore this.\n", encoding="utf-8")
    app = make_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sources/extract",
            json={"profile_id": "claude_memory_markdown", "source_path": str(source)},
            headers=auth_headers(),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["byte_size"] == useful.stat().st_size + oversized.stat().st_size
    assert any(item["type"] == "preference" for item in payload["items"])
    assert payload["items"][0]["extra"]["source_path"] == "useful.md"
    assert "oversized.md: source exceeds profile size limit" in payload["warnings"]
    assert memory_count(tmp_path) == 0


def test_extract_preview_reclassifies_technical_feedback_before_import(tmp_path) -> None:
    source = write(
        tmp_path,
        "feedback_arkts_builder_this.md",
        """---
type: feedback
---
In ArkTS, a `@Builder` method passed to `.tabBar()` must use the `this.` prefix.

**Why:** The compiler treats the bare method as a missing identifier.
""",
    )
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sources/extract",
            json={"profile_id": "claude_memory_markdown", "source_path": source},
            headers=auth_headers(),
        )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["type"] == "insight"
    assert item["admission_action"] == "provisional"
    assert item["admission_reason_code"] == "UNCONFIRMED_OBSERVATION"
    assert item["extra"]["classification_original_type"] == "preference"
    assert (
        item["extra"]["classification_reason_code"]
        == "TECHNICAL_RULE_NOT_USER_PREFERENCE"
    )
    assert memory_count(tmp_path) == 0


def test_extract_preview_discards_context_poor_information_request(tmp_path) -> None:
    source = write(
        tmp_path,
        "conversation.txt",
        "我主要想了解示例应用的运行机制。\n",
    )
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sources/extract",
            json={"profile_id": "conversation_text", "source_path": source},
            headers=auth_headers(),
        )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["admission_action"] == "discard"
    assert item["admission_reason_code"] == "TRANSIENT_INFORMATION_REQUEST"
    assert memory_count(tmp_path) == 0


def test_extract_preview_supports_codex_jsonl(tmp_path) -> None:
    import json as jsonlib

    source = tmp_path / "rollout.jsonl"
    lines = [
        {"type": "response_item", "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "我完成了 Agent 项目。"}],
        }},
        {"type": "response_item", "payload": {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "很好。"}],
        }},
    ]
    source.write_text(
        "\n".join(jsonlib.dumps(line) for line in lines), encoding="utf-8"
    )
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sources/extract",
            json={"profile_id": "codex_jsonl", "source_path": str(source)},
            headers=auth_headers(),
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "jsonl"
    assert any(item["type"] == "event" for item in payload["items"])
    assert memory_count(tmp_path) == 0


def test_extract_rejects_missing_file(tmp_path) -> None:
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sources/extract",
            json={
                "profile_id": "conversation_text",
                "source_path": str(tmp_path / "missing.txt"),
            },
            headers=auth_headers(),
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "SOURCE_FILE_NOT_FOUND"


def test_extract_rejects_too_large_source(tmp_path) -> None:
    path = tmp_path / "big.txt"
    path.write_bytes(b"x" * (262_144 + 1))
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sources/extract",
            json={"profile_id": "conversation_text", "source_path": str(path)},
            headers=auth_headers(),
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "SOURCE_TOO_LARGE"


def test_extract_rejects_unknown_profile(tmp_path) -> None:
    source = write(tmp_path, "c.txt", "我的目标是测试。\n")
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sources/extract",
            json={"profile_id": "nope", "source_path": source},
            headers=auth_headers(),
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "UNKNOWN_PROFILE"


def test_extract_rejects_write_only_profile(tmp_path) -> None:
    source = write(tmp_path, "c.txt", "我的目标是测试。\n")
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sources/extract",
            json={"profile_id": "personal_memory_projection_file", "source_path": source},
            headers=auth_headers(),
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "PROFILE_CANNOT_READ"


def test_extract_requires_session_token(tmp_path) -> None:
    source = write(tmp_path, "c.txt", "我的目标是测试。\n")
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sources/extract",
            json={"profile_id": "conversation_text", "source_path": source},
        )
    assert response.status_code == 401
