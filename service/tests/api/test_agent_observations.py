from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from personalmemory.infrastructure.database.models import (
    AgentTurnObservationRecord,
    MemoryCandidateRecord,
    MemoryObjectRecord,
    SourceRevisionRecord,
)
from sqlalchemy import func, select


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


def create_hook_connector(client: TestClient, project_root, namespace="projects/demo") -> str:
    response = client.post(
        "/api/v1/connectors",
        headers=auth_headers(),
        json={
            "name": "Claude Code 自动采集",
            "profile_id": "claude_code_hook",
            "source_path": str(project_root),
            "target_path": None,
            "namespace": namespace,
            "read_enabled": True,
            "write_enabled": False,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_global_context_connector(
    client: TestClient,
    project_root,
    namespace="projects/global",
    *,
    profile_id="claude_code_global_context",
) -> str:
    response = client.post(
        "/api/v1/connectors",
        headers=auth_headers(),
        json={
            "name": "Claude Code global context",
            "profile_id": profile_id,
            "source_path": str(project_root),
            "target_path": None,
            "namespace": namespace,
            "read_enabled": True,
            "write_enabled": False,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def event_payload(
    connector_id: str,
    project_root,
    transcript,
    *,
    event_name: str,
    user_text: str | None = None,
    assistant_text: str | None = None,
    agent_family: str = "claude_code",
) -> dict[str, object]:
    return {
        "schema_version": "1",
        "connector_id": connector_id,
        "agent_family": agent_family,
        "fragment_kind": (
            "user_message" if event_name == "UserPromptSubmit" else "assistant_message"
        ),
        "session_id": "session-01",
        "prompt_id": "prompt-01",
        "cwd": str(project_root),
        "transcript_path": str(transcript),
        "user_text": user_text,
        "assistant_text": assistant_text,
    }


def post_turn_pair(client, connector_id, project_root, transcript, user_text):
    opened = client.post(
        "/api/v1/observations/agent-turns",
        headers=auth_headers(),
        json=event_payload(
            connector_id,
            project_root,
            transcript,
            event_name="UserPromptSubmit",
            user_text=user_text,
        ),
    )
    closed = client.post(
        "/api/v1/observations/agent-turns",
        headers=auth_headers(),
        json=event_payload(
            connector_id,
            project_root,
            transcript,
            event_name="Stop",
            assistant_text="已完成本轮工作。",
        ),
    )
    return opened, closed


def test_turn_events_create_candidate_and_memory_then_clear_raw_text(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector_id = create_hook_connector(client, project_root)
        opened, closed = post_turn_pair(
            client,
            connector_id,
            project_root,
            transcript,
            "我决定采用 SQLite，因为本地自部署更简单。",
        )

        database = client.app.state.database
        with database.session_factory() as session:
            observation = session.scalar(select(AgentTurnObservationRecord))
            source_count = session.scalar(select(func.count()).select_from(SourceRevisionRecord))
            candidate = session.scalar(select(MemoryCandidateRecord))
            memory = session.scalar(select(MemoryObjectRecord))

    assert opened.status_code == 202
    assert opened.json()["status"] == "pending"
    assert closed.status_code == 201
    assert closed.json()["status"] == "processed"
    assert closed.json()["counts"]["accepted"] == 1
    assert observation.user_text is None
    assert observation.assistant_text is None
    assert observation.status == "processed"
    assert source_count == 1
    assert candidate.draft["payload"]["observation_id"] == observation.id
    assert candidate.draft["payload"]["evidence_excerpt"].startswith("用户:")
    assert candidate.draft["payload"]["evidence_actor"] == "user"
    assert candidate.draft["payload"]["capture_mode"] == "realtime_hook"
    assert memory.type == "decision"
    assert memory.namespace == "projects/demo"
    assert memory.payload["source_revision_id"] == observation.source_revision_id
    assert memory.payload["cwd"] == str(project_root)
    assert memory.payload["transcript_path"] == str(transcript)


def test_explicit_miniprogram_intent_becomes_a_goal_through_the_hook_pipeline(tmp_path) -> None:
    project_root = tmp_path / "miniprogram"
    project_root.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector_id = create_hook_connector(client, project_root)
        opened, closed = post_turn_pair(
            client,
            connector_id,
            project_root,
            transcript,
            "我计划开发一个示例应用。",
        )
        database = client.app.state.database
        with database.session_factory() as session:
            memory = session.scalar(select(MemoryObjectRecord))

    assert opened.status_code == 202
    assert closed.status_code == 201
    assert closed.json()["counts"]["accepted"] == 1
    assert memory.type == "goal"
    assert memory.statement == "开发一个示例应用"
    assert memory.namespace == "projects/demo"


def test_stop_before_prompt_is_joined_without_duplicates(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector_id = create_hook_connector(client, project_root)
        stop = event_payload(
            connector_id,
            project_root,
            transcript,
            event_name="Stop",
            assistant_text="好的。",
        )
        prompt = event_payload(
            connector_id,
            project_root,
            transcript,
            event_name="UserPromptSubmit",
            user_text="我喜欢详细的工程分析。",
        )
        first = client.post("/api/v1/observations/agent-turns", headers=auth_headers(), json=stop)
        second = client.post(
            "/api/v1/observations/agent-turns", headers=auth_headers(), json=prompt
        )
        duplicate_open = client.post(
            "/api/v1/observations/agent-turns", headers=auth_headers(), json=prompt
        )
        duplicate_close = client.post(
            "/api/v1/observations/agent-turns", headers=auth_headers(), json=stop
        )

        database = client.app.state.database
        with database.session_factory() as session:
            observations = session.scalar(
                select(func.count()).select_from(AgentTurnObservationRecord)
            )
            sources = session.scalar(select(func.count()).select_from(SourceRevisionRecord))
            memories = session.scalar(select(func.count()).select_from(MemoryObjectRecord))

    assert first.status_code == 202
    assert second.status_code == 201
    assert duplicate_open.status_code == 200
    assert duplicate_open.json()["duplicate"] is True
    assert duplicate_close.status_code == 200
    assert observations == sources == memories == 1


def test_assistant_only_suggestion_does_not_become_user_memory(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector_id = create_hook_connector(client, project_root)
        opened = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="UserPromptSubmit",
                user_text="请比较 SQLite 和向量数据库。",
            ),
        )
        closed = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="Stop",
                assistant_text="我决定采用向量数据库，因为看起来更智能。",
            ),
        )
        memories = client.get("/api/v1/memories", headers=auth_headers()).json()

    assert opened.status_code == 202
    assert closed.status_code == 201
    assert closed.json()["counts"] == {
        "accepted": 0,
        "provisional": 0,
        "quarantined": 0,
        "restricted": 0,
        "discarded": 0,
    }
    assert memories["total"] == 0


def test_unauthorized_project_and_disabled_connector_are_rejected(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector_id = create_hook_connector(client, project_root)
        unauthorized = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                outside,
                transcript,
                event_name="UserPromptSubmit",
                user_text="我喜欢 Rust。",
            ),
        )
        client.patch(
            f"/api/v1/connectors/{connector_id}",
            headers=auth_headers(),
            json={"enabled": False},
        )
        disabled = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="UserPromptSubmit",
                user_text="我喜欢 Rust。",
            ),
        )

    assert unauthorized.status_code == 403
    assert unauthorized.json()["detail"]["code"] == "PROJECT_SCOPE_DENIED"
    assert disabled.status_code == 409
    assert disabled.json()["detail"]["code"] == "CONNECTOR_DISABLED"


def test_secret_is_discarded_and_conflicting_prompt_is_not_overwritten(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector_id = create_hook_connector(client, project_root)
        _, closed = post_turn_pair(
            client,
            connector_id,
            project_root,
            transcript,
            "我决定使用 api_token=TEST_ONLY_REDACTED_VALUE，因为测试需要。",
        )
        conflicting = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="UserPromptSubmit",
                user_text="我决定公开这个 token。",
            ),
        )
        memories = client.get("/api/v1/memories", headers=auth_headers()).json()

    assert closed.status_code == 201
    assert closed.json()["counts"]["discarded"] == 1
    assert memories["total"] == 0
    assert conflicting.status_code == 409
    assert conflicting.json()["detail"]["code"] == "OBSERVATION_CONTENT_CONFLICT"


@pytest.mark.skipif(os.name != "nt", reason="Windows paths are case-insensitive")
def test_case_only_path_differences_merge_the_same_observation(tmp_path) -> None:
    project_root = tmp_path / "ProjectCase"
    project_root.mkdir()
    transcript = project_root / "SessionCase.jsonl"
    transcript.write_text("", encoding="utf-8")
    lower_root = str(project_root).replace("ProjectCase", "projectcase")
    lower_transcript = str(transcript).replace("SessionCase.jsonl", "sessioncase.jsonl")

    with make_client(tmp_path) as client:
        connector_id = create_hook_connector(client, project_root)
        user = event_payload(
            connector_id,
            project_root,
            transcript,
            event_name="UserPromptSubmit",
            user_text="我决定保持路径治理稳定，因为 Windows 路径大小写不应改变身份。",
        )
        user["cwd"] = lower_root
        user["transcript_path"] = lower_transcript
        opened = client.post(
            "/api/v1/observations/agent-turns", headers=auth_headers(), json=user
        )
        stop = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="Stop",
                assistant_text="已确认。",
            ),
        )

    assert opened.status_code == 202
    assert stop.status_code == 201
    assert stop.json()["status"] == "processed"


def test_observation_api_requires_token(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector_id = create_hook_connector(client, project_root)
        response = client.post(
            "/api/v1/observations/agent-turns",
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="UserPromptSubmit",
                user_text="我喜欢 Python。",
            ),
        )

    assert response.status_code == 401


def test_concurrent_fragments_merge_into_one_observation(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector_id = create_hook_connector(client, project_root)
        payloads = [
            event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="UserPromptSubmit",
                user_text="我决定保持 Continuity Core 平台无关。",
            ),
            event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="Stop",
                assistant_text="已完成。",
            ),
        ]

        def post(payload):
            return client.post(
                "/api/v1/observations/agent-turns",
                headers=auth_headers(),
                json=payload,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(post, payloads))

        database = client.app.state.database
        with database.session_factory() as session:
            observations = session.scalar(
                select(func.count()).select_from(AgentTurnObservationRecord)
            )
            sources = session.scalar(select(func.count()).select_from(SourceRevisionRecord))
            memories = session.scalar(select(func.count()).select_from(MemoryObjectRecord))

    assert sorted(response.status_code for response in responses) == [201, 202]
    assert observations == sources == memories == 1


def test_assistant_fragment_without_prompt_id_uses_unique_pending_turn(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector_id = create_hook_connector(client, project_root)
        opened = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="UserPromptSubmit",
                user_text="我决定保持平台无关。",
            ),
        )
        close_payload = event_payload(
            connector_id,
            project_root,
            transcript,
            event_name="Stop",
            assistant_text="已确认。",
        )
        close_payload["prompt_id"] = None
        closed = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=close_payload,
        )

    assert opened.status_code == 202
    assert closed.status_code == 201
    assert closed.json()["counts"]["accepted"] == 1


def test_missing_id_assistant_can_arrive_before_user_fragment(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector_id = create_hook_connector(client, project_root)
        stop_payload = event_payload(
            connector_id,
            project_root,
            transcript,
            event_name="Stop",
            assistant_text="已确认。",
        )
        stop_payload["prompt_id"] = None
        stopped = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=stop_payload,
        )
        opened = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="UserPromptSubmit",
                user_text="我决定保持 Continuity Core 平台无关。",
            ),
        )

        database = client.app.state.database
        with database.session_factory() as session:
            observations = session.scalars(select(AgentTurnObservationRecord)).all()

    assert stopped.status_code == 202
    assert opened.status_code == 201
    assert opened.json()["counts"]["accepted"] == 1
    assert len(observations) == 1
    assert observations[0].status == "processed"


def test_global_context_prompt_delivers_preflight_without_capture(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector_id = create_global_context_connector(
            client, project_root, namespace="general"
        )
        for namespace, statement in (
            ("projects/global", "global context continuity item"),
            ("general", "general context continuity item"),
        ):
            created = client.post(
                "/api/v1/memories",
                headers=auth_headers(),
                json={"type": "fact", "statement": statement, "namespace": namespace},
            )
            assert created.status_code == 201, created.text

        database = client.app.state.database
        with database.session_factory() as session:
            before = tuple(
                session.scalar(select(func.count()).select_from(model))
                for model in (
                    AgentTurnObservationRecord,
                    SourceRevisionRecord,
                    MemoryCandidateRecord,
                    MemoryObjectRecord,
                )
            )

        prompt = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="UserPromptSubmit",
                user_text="Please provide continuity context",
            ),
        )
        stopped = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="Stop",
                assistant_text="The response is complete",
            ),
        )

        with database.session_factory() as session:
            after = tuple(
                session.scalar(select(func.count()).select_from(model))
                for model in (
                    AgentTurnObservationRecord,
                    SourceRevisionRecord,
                    MemoryCandidateRecord,
                    MemoryObjectRecord,
                )
            )

    assert prompt.status_code == 200
    prompt_body = prompt.json()
    assert prompt_body["status"] == "context_only"
    assert prompt_body["capture_agent_turns"] is False
    assert prompt_body["preflight"]["namespaces"] == ["general"]
    assert "global context continuity item" not in prompt_body["preflight"]["context"]
    assert "general context continuity item" in prompt_body["preflight"]["context"]
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "ignored"
    assert stopped.json()["mode"] == "context_only"
    assert stopped.json()["reason"] == "capture_agent_turns_disabled"
    assert after == before


@pytest.mark.parametrize(
    ("user_text", "expected_type", "expected_statement"),
    [
        ("\u6211\u662f\u5b66\u751f", "fact", "\u5b66\u751f"),
        (
            "\u6211\u8981\u5f00\u53d1\u4e00\u4e2a\u5fae\u4fe1\u5c0f\u7a0b\u5e8f",
            "goal",
            "\u5f00\u53d1\u4e00\u4e2a\u5fae\u4fe1\u5c0f\u7a0b\u5e8f",
        ),
        ("\u6211\u51b3\u5b9a\u4f7f\u7528 Rust", "decision", "Rust"),
        (
            "\u6211\u504f\u597d\u4f7f\u7528\u4e2d\u6587\u56de\u7b54",
            "preference",
            "\u4f7f\u7528\u4e2d\u6587\u56de\u7b54",
        ),
    ],
)
def test_global_context_selectively_captures_explicit_durable_statements(
    tmp_path, user_text: str, expected_type: str, expected_statement: str
) -> None:
    project_root = tmp_path / "global"
    project_root.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector_id = create_global_context_connector(
            client, project_root, namespace="general"
        )
        prompt = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="UserPromptSubmit",
                user_text=user_text,
            ),
        )
        stopped = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="Stop",
                assistant_text="\u597d\u7684\uff0c\u6211\u8bb0\u4e0b\u4e86\u3002",
            ),
        )
        memories = client.get("/api/v1/memories", headers=auth_headers())

    assert prompt.status_code == 202, prompt.text
    assert prompt.json()["mode"] == "selective_capture"
    assert prompt.json()["selective_capture"] is True
    assert "preflight" in prompt.json()
    assert stopped.status_code == 201, stopped.text
    assert stopped.json()["status"] == "processed"
    assert memories.status_code == 200, memories.text
    items = memories.json()["items"]
    assert len(items) == 1
    assert items[0]["type"] == expected_type
    assert items[0]["statement"] == expected_statement
    assert items[0]["namespace"] == "general"


def test_global_explicit_fact_is_deliverable_to_codex_profile_recall(tmp_path) -> None:
    project_root = tmp_path / "global"
    project_root.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector_id = create_global_context_connector(client, project_root, namespace="general")
        prompt, stopped = post_turn_pair(
            client,
            connector_id,
            project_root,
            transcript,
            "我是一名示例大学生。",
        )
        memories = client.get(
            "/api/v1/memories",
            headers=auth_headers(),
            params={"type": "fact"},
        )

    assert prompt.status_code == 202, prompt.text
    assert stopped.status_code == 201, stopped.text
    assert stopped.json()["counts"]["accepted"] == 1
    items = memories.json()["items"]
    assert len(items) == 1
    assert items[0]["statement"] == "一名示例大学生"
    assert items[0]["payload"]["evidence_actor"] == "user"
    assert items[0]["review_triage"]["delivery_eligible"] is True


def test_codex_global_hook_uses_the_same_selective_governance_pipeline(tmp_path) -> None:
    project_root = tmp_path / "codex"
    project_root.mkdir()
    transcript = tmp_path / "codex-session.jsonl"
    transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector_id = create_global_context_connector(
            client,
            project_root,
            namespace="general",
            profile_id="codex_global_context",
        )
        prompt = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="UserPromptSubmit",
                user_text="我计划开发一个示例应用。",
                agent_family="codex",
            ),
        )
        stopped = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="Stop",
                assistant_text="好的。",
                agent_family="codex",
            ),
        )
        memories = client.get("/api/v1/memories", headers=auth_headers())

    assert prompt.status_code == 202, prompt.text
    assert prompt.json()["mode"] == "selective_capture"
    assert stopped.status_code == 201, stopped.text
    assert stopped.json()["counts"]["accepted"] == 1
    assert memories.json()["items"][0]["statement"] == "开发一个示例应用"


@pytest.mark.parametrize("family,profile", [
    ("claude_code", "claude_code_global_context"),
    ("codex", "codex_global_context"),
])
def test_global_context_keeps_transient_prompt_context_only_and_ignores_stop(
    tmp_path, family, profile
) -> None:
    project_root = tmp_path / "global"
    project_root.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector_id = create_global_context_connector(client, project_root, profile_id=profile)
        prompt = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="UserPromptSubmit",
                user_text="\u8bf7\u5e2e\u6211\u6253\u5f00\u8fd9\u4e2a\u6587\u4ef6",
                agent_family=family,
            ),
        )
        stopped = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="Stop",
                assistant_text="\u5df2\u6253\u5f00\u3002",
                agent_family=family,
            ),
        )
        connectors = client.get("/api/v1/connectors", headers=auth_headers()).json()["items"]
        receipt = next(item for item in connectors if item["id"] == connector_id)["hook_receipt"]
        assert set(receipt) == {"user_message", "assistant_message"}
        stored = (tmp_path / "data/runtime/hook-receipts.json").read_text(encoding="utf-8")
        assert "请帮我打开这个文件" not in stored
        assert "已打开" not in stored
        database = client.app.state.database
        with database.session_factory() as session:
            counts = tuple(
                session.scalar(select(func.count()).select_from(model))
                for model in (
                    AgentTurnObservationRecord,
                    SourceRevisionRecord,
                    MemoryCandidateRecord,
                    MemoryObjectRecord,
                )
            )

    assert prompt.status_code == 200
    assert prompt.json()["status"] == "context_only"
    assert prompt.json()["mode"] == "context_only"
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "ignored"
    assert stopped.json()["reason"] == "capture_agent_turns_disabled"
    assert counts == (0, 0, 0, 0)


def test_global_context_stop_without_selective_prompt_does_not_create_observation(tmp_path) -> None:
    project_root = tmp_path / "global"
    project_root.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector_id = create_global_context_connector(client, project_root)
        stopped = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="Stop",
                assistant_text="\u666e\u901a\u56de\u7b54\u3002",
            ),
        )

    assert stopped.status_code == 200
    assert stopped.json()["status"] == "ignored"
    assert stopped.json()["reason"] == "capture_agent_turns_disabled"


def test_global_context_yields_stop_when_project_hook_covers_cwd(tmp_path) -> None:
    global_root = tmp_path / "global"
    project_root = global_root / "project"
    global_root.mkdir()
    project_root.mkdir()
    global_transcript = tmp_path / "global-session.jsonl"
    project_transcript = tmp_path / "project-session.jsonl"
    global_transcript.write_text("", encoding="utf-8")
    project_transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        global_connector_id = create_global_context_connector(client, global_root)
        project_connector_id = create_hook_connector(client, project_root)
        opened = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                global_connector_id,
                project_root,
                global_transcript,
                event_name="UserPromptSubmit",
                user_text="\u6211\u8981\u5f00\u53d1\u4e00\u4e2a\u5fae\u4fe1\u5c0f\u7a0b\u5e8f",
            ),
        )
        stopped = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                global_connector_id,
                project_root,
                global_transcript,
                event_name="Stop",
                assistant_text="\u5b8c\u6210\u3002",
            ),
        )
        project_opened = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                project_connector_id,
                project_root,
                project_transcript,
                event_name="UserPromptSubmit",
                user_text="\u6211\u8981\u5f00\u53d1\u4e00\u4e2a\u5fae\u4fe1\u5c0f\u7a0b\u5e8f",
            ),
        )

    assert opened.status_code == 200
    assert opened.json()["status"] == "ignored"
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "ignored"
    assert stopped.json()["reason"] == "project_connector_context_delivery"
    assert project_opened.status_code == 202


def test_global_context_skips_delivery_when_project_hook_covers_cwd(tmp_path) -> None:
    global_root = tmp_path / "global"
    project_root = global_root / "project"
    global_root.mkdir()
    project_root.mkdir()
    global_transcript = tmp_path / "global-session.jsonl"
    project_transcript = tmp_path / "project-session.jsonl"
    global_transcript.write_text("", encoding="utf-8")
    project_transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        global_connector_id = create_global_context_connector(client, global_root)
        project_connector_id = create_hook_connector(
            client, project_root, namespace="projects/project"
        )
        for namespace, statement in (
            ("projects/project", "project context continuity item"),
            ("general", "general context continuity item"),
        ):
            created = client.post(
                "/api/v1/memories",
                headers=auth_headers(),
                json={"type": "fact", "statement": statement, "namespace": namespace},
            )
            assert created.status_code == 201, created.text

        global_prompt = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                global_connector_id,
                project_root,
                global_transcript,
                event_name="UserPromptSubmit",
                user_text="Please provide continuity context",
            ),
        )
        project_prompt = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                project_connector_id,
                project_root,
                project_transcript,
                event_name="UserPromptSubmit",
                user_text="Please provide continuity context",
            ),
        )

    assert global_prompt.status_code == 200
    global_body = global_prompt.json()
    assert global_body["status"] == "ignored"
    assert global_body["mode"] == "context_only"
    assert global_body["reason"] == "project_connector_context_delivery"
    assert "preflight" not in global_body
    assert project_prompt.status_code == 202
    project_body = project_prompt.json()
    assert project_body["preflight"]["namespaces"] == ["projects/project", "general"]
    assert "project context continuity item" in project_body["preflight"]["context"]
    assert "general context continuity item" in project_body["preflight"]["context"]


def test_global_context_reuses_connector_scope_and_enabled_checks(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector_id = create_global_context_connector(client, project_root)
        outside_payload = event_payload(
            connector_id,
            outside,
            transcript,
            event_name="UserPromptSubmit",
            user_text="outside project scope",
        )
        outside_response = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=outside_payload,
        )
        disabled_update = client.patch(
            f"/api/v1/connectors/{connector_id}",
            headers=auth_headers(),
            json={"enabled": False},
        )
        assert disabled_update.status_code == 200, disabled_update.text
        disabled_response = client.post(
            "/api/v1/observations/agent-turns",
            headers=auth_headers(),
            json=event_payload(
                connector_id,
                project_root,
                transcript,
                event_name="UserPromptSubmit",
                user_text="disabled connector",
            ),
        )

    assert outside_response.status_code == 403
    assert outside_response.json()["detail"]["code"] == "PROJECT_SCOPE_DENIED"
    assert disabled_response.status_code == 409
    assert disabled_response.json()["detail"]["code"] == "CONNECTOR_DISABLED"
