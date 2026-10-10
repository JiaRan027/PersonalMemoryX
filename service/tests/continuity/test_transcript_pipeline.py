from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from personalmemory.domain.continuity import ContinuityEnvelope, SourceCapabilities
from personalmemory.infrastructure.database.models import MemoryCandidateRecord
from personalmemory.infrastructure.filesystem.adapters import (
    MAX_JSONL_ENTRIES,
    decode_continuity_transcript,
)
from sqlalchemy import select


def make_client(data_dir) -> TestClient:
    from personalmemory.api.app import create_app
    from personalmemory.settings import Settings

    return TestClient(
        create_app(
            Settings(
                data_dir=data_dir,
                api_token="test-token",
                enable_background_sync=False,
                llm_enabled=False,
            )
        )
    )


HEADERS = {"Authorization": "Bearer test-token"}


def create_connector(client, profile_id: str, source, namespace="projects/shared") -> str:
    response = client.post(
        "/api/v1/connectors",
        headers=HEADERS,
        json={
            "name": profile_id,
            "profile_id": profile_id,
            "source_path": str(source),
            "target_path": None,
            "namespace": namespace,
            "read_enabled": True,
            "write_enabled": False,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def memories(client) -> list[dict[str, object]]:
    response = client.get("/api/v1/memories", headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def sync(client, connector_id: str) -> dict[str, int]:
    response = client.post(f"/api/v1/connectors/{connector_id}/sync", headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("format_name", ["json", "jsonl"])
def test_standard_transcript_profiles_ingest_with_bounded_evidence(tmp_path, format_name) -> None:
    messages = [
        {
            "role": "user",
            "id": "prompt-01",
            "session_id": "session-01",
            "content": "I decided to use SQLite because local deployment is simpler.",
        },
        {
            "role": "assistant",
            "id": "answer-01",
            "session_id": "session-01",
            "content": "Done.",
        },
    ]
    source = tmp_path / f"conversation.{format_name}"
    if format_name == "json":
        source.write_text(json.dumps({"messages": messages}), encoding="utf-8")
    else:
        source.write_text("\n".join(json.dumps(item) for item in messages), encoding="utf-8")

    with make_client(tmp_path / "data") as client:
        connector_id = create_connector(client, f"transcript_{format_name}", source)
        first = sync(client, connector_id)
        second = sync(client, connector_id)
        items = memories(client)
        with client.app.state.database.session_factory() as session:
            candidate = session.scalar(select(MemoryCandidateRecord))

    assert first["accepted"] == 1
    assert second["accepted"] == 0
    assert [(item["type"], item["statement"]) for item in items] == [
        ("decision", "to use SQLite")
    ]
    payload = candidate.draft["payload"]
    assert payload["source_kind"] == "transcript"
    assert payload["source_family"] == "standard_transcript"
    assert payload["source_format"] == format_name
    assert payload["source_capabilities"]["is_realtime"] is False
    assert payload["evidence_locator"].startswith(str(source.resolve()))
    assert len(payload["evidence_digest"]) == 64


def test_hook_and_transcript_envelopes_have_equivalent_identity_and_core_result(tmp_path) -> None:
    user_text = "I decided to use SQLite because local deployment is simpler."
    assistant_text = "Done."
    transcript_path = tmp_path / "conversation.json"
    transcript_path.write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "id": "prompt-01",
                        "session_id": "session-01",
                        "content": user_text,
                    },
                    {
                        "role": "assistant",
                        "id": "answer-01",
                        "session_id": "session-01",
                        "content": assistant_text,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    transcript = decode_continuity_transcript(
        transcript_path,
        format_name="json",
        source_family="standard_transcript",
    )
    hook_envelope = ContinuityEnvelope.from_interaction(
        source_kind="interaction",
        source_family="claude_code",
        capture_mode="realtime_hook",
        capabilities=SourceCapabilities(
            has_actor_roles=True,
            has_session=True,
            has_timestamps=False,
            is_realtime=True,
        ),
        locator="claude_code://session-01/prompt-01",
        interaction_id="prompt-01",
        user_text=user_text,
        assistant_text=assistant_text,
        session_id="session-01",
        message_ids=("prompt-01", "answer-01"),
    )

    assert len(transcript.envelopes) == 1
    offline_envelope = transcript.envelopes[0]
    assert offline_envelope.normalized_content() == hook_envelope.normalized_content()
    assert offline_envelope.evidence.digest == hook_envelope.evidence.digest
    assert offline_envelope.capabilities.is_realtime is False
    assert hook_envelope.capabilities.is_realtime is True

    statements: list[tuple[str, str]] = []
    for name, profile_id, source in (
        ("offline", "transcript_json", transcript_path),
        ("realtime", "claude_code_hook", tmp_path / "project"),
    ):
        source.mkdir(exist_ok=True) if profile_id == "claude_code_hook" else None
        with make_client(tmp_path / f"data-{name}") as client:
            connector_id = create_connector(client, profile_id, source)
            if profile_id == "transcript_json":
                sync(client, connector_id)
            else:
                payload = {
                    "schema_version": "1",
                    "connector_id": connector_id,
                    "agent_family": "claude_code",
                    "session_id": "session-01",
                    "prompt_id": "prompt-01",
                    "cwd": str(source),
                    "transcript_path": str(tmp_path / "session.jsonl"),
                }
                for fragment_kind, text_field, text in (
                    ("user_message", "user_text", user_text),
                    ("assistant_message", "assistant_text", assistant_text),
                ):
                    response = client.post(
                        "/api/v1/observations/agent-turns",
                        headers=HEADERS,
                        json={**payload, "fragment_kind": fragment_kind, text_field: text},
                    )
                    assert response.status_code in {201, 202}, response.text
            item = memories(client)[0]
            statements.append((item["type"], item["statement"]))
    assert statements == [("decision", "to use SQLite")] * 2


def test_transcript_excludes_non_user_evidence_and_secrets(tmp_path) -> None:
    source = tmp_path / "privacy.jsonl"
    entries = [
        {"role": "system", "content": "I decided to leak system state."},
        {"role": "assistant", "content": "I decided to use a vector cluster."},
        {
            "role": "user",
            "content": [{"type": "tool_result", "content": "I prefer leaked tools."}],
        },
        {
            "role": "user",
            "content": [{"type": "thinking", "text": "I prefer hidden reasoning."}],
        },
        {"type": "unknown_future_entry", "payload": {"content": "I prefer guesses."}},
        {"role": "user", "content": "I decided to use api_token=TEST_ONLY_REDACTED_VALUE."},
        {"role": "user", "content": "I prefer deterministic local extraction."},
    ]
    source.write_text("\n".join(json.dumps(item) for item in entries), encoding="utf-8")

    with make_client(tmp_path / "data") as client:
        connector_id = create_connector(client, "transcript_jsonl", source)
        sync(client, connector_id)
        items = memories(client)

    assert [(item["type"], item["statement"]) for item in items] == [
        ("preference", "deterministic local extraction")
    ]


def test_deleted_memory_is_not_resurrected_by_changed_transcript(tmp_path) -> None:
    source = tmp_path / "conversation.json"
    first = {
        "messages": [
            {"role": "user", "id": "p1", "content": "I prefer local-first storage."}
        ]
    }
    source.write_text(json.dumps(first), encoding="utf-8")
    with make_client(tmp_path / "data") as client:
        connector_id = create_connector(client, "transcript_json", source)
        sync(client, connector_id)
        memory_id = memories(client)[0]["id"]
        assert client.delete(f"/api/v1/memories/{memory_id}", headers=HEADERS).status_code == 204

        first["messages"].append(
            {"role": "assistant", "id": "a1", "content": "Understood."}
        )
        source.write_text(json.dumps(first), encoding="utf-8")
        sync(client, connector_id)
        assert memories(client) == []


def test_transcript_decoder_caps_entry_count_and_skips_unknown_roles(tmp_path) -> None:
    source = tmp_path / "bounded.json"
    entries = [
        {"role": "system", "content": "ignored"},
        *(
            {"role": "user", "id": f"p-{index}", "content": f"message {index}"}
            for index in range(MAX_JSONL_ENTRIES + 10)
        ),
    ]
    source.write_text(json.dumps({"messages": entries}), encoding="utf-8")
    transcript = decode_continuity_transcript(
        source, format_name="json", source_family="bounded-test"
    )
    assert len(transcript.envelopes) == MAX_JSONL_ENTRIES - 1
