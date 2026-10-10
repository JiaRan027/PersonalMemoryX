from __future__ import annotations

import json

import pytest
from personalmemory.infrastructure.claude_hook import (
    HookInputError,
    parse_agent_hook_event,
    parse_claude_hook_event,
    recover_latest_user_text,
)


def test_codex_null_transcript_uses_non_file_evidence_locator(tmp_path) -> None:
    event = parse_agent_hook_event(
        {
            "session_id": "session-01",
            "turn_id": "turn-01",
            "transcript_path": None,
            "cwd": str(tmp_path),
            "hook_event_name": "UserPromptSubmit",
            "prompt": "我计划开发一个示例应用。",
        },
        connector_id="connector-01",
        agent_family="codex",
    )

    assert event.prompt_id == "turn-01"
    assert event.transcript_path == "codex://session/session-01"


def test_user_prompt_submit_uses_direct_prompt(tmp_path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    event = parse_claude_hook_event(
        {
            "session_id": "session-01",
            "prompt_id": "prompt-01",
            "transcript_path": str(transcript),
            "cwd": str(tmp_path),
            "hook_event_name": "UserPromptSubmit",
            "prompt": "我决定采用 SQLite，因为它适合本地部署。",
        },
        connector_id="connector-01",
    )

    assert event.fragment_kind == "user_message"
    assert event.user_text == "我决定采用 SQLite，因为它适合本地部署。"
    assert event.assistant_text is None


def test_user_prompt_submit_synthesizes_missing_claude_prompt_id(tmp_path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")
    payload = {
        "session_id": "session-01",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "Continue the current project.",
    }

    first = parse_claude_hook_event(payload, connector_id="connector-01")
    second = parse_claude_hook_event(payload, connector_id="connector-01")

    assert first.prompt_id is not None
    assert first.prompt_id.startswith("prompt:auto:")
    assert first.prompt_id == second.prompt_id


def test_stop_uses_last_assistant_message_even_when_transcript_lags(tmp_path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": "我喜欢工程化分析。"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    event = parse_claude_hook_event(
        {
            "session_id": "session-01",
            "prompt_id": "prompt-01",
            "transcript_path": str(transcript),
            "cwd": str(tmp_path),
            "hook_event_name": "Stop",
            "stop_hook_active": False,
            "last_assistant_message": "已经记录这个偏好。",
        },
        connector_id="connector-01",
    )

    assert event.user_text is None
    assert event.assistant_text == "已经记录这个偏好。"


def test_stop_allows_missing_prompt_id_for_service_side_correlation(tmp_path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    event = parse_claude_hook_event(
        {
            "session_id": "session-01",
            "transcript_path": str(transcript),
            "cwd": str(tmp_path),
            "hook_event_name": "Stop",
            "last_assistant_message": "已完成。",
        },
        connector_id="connector-01",
    )

    assert event.fragment_kind == "assistant_message"
    assert event.prompt_id is None


def test_recovery_reads_only_latest_user_visible_message(tmp_path) -> None:
    transcript = tmp_path / "session.jsonl"
    lines = [
        {"type": "system", "message": {"role": "system", "content": "hidden"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "hidden reasoning"},
                    {"type": "tool_use", "input": {"secret": "hidden"}},
                ],
            },
        },
        {"type": "user", "message": {"role": "user", "content": "较早消息"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_result", "content": "tool output"}],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "我最终选择自部署。"}],
            },
        },
    ]
    transcript.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in lines),
        encoding="utf-8",
    )

    assert recover_latest_user_text(transcript) == "我最终选择自部署。"


@pytest.mark.parametrize(
    "patch",
    [
        {"hook_event_name": "Unknown"},
        {"session_id": "../session"},
        {"prompt_id": ""},
        {"cwd": "relative/path"},
        {"transcript_path": "relative.jsonl"},
    ],
)
def test_invalid_hook_input_is_rejected(tmp_path, patch) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")
    payload = {
        "session_id": "session-01",
        "prompt_id": "prompt-01",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "我喜欢 Python。",
    }
    payload.update(patch)

    with pytest.raises(HookInputError):
        parse_claude_hook_event(payload, connector_id="connector-01")


def test_hook_text_is_truncated_on_utf8_boundary(tmp_path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    event = parse_claude_hook_event(
        {
            "session_id": "session-01",
            "prompt_id": "prompt-01",
            "transcript_path": str(transcript),
            "cwd": str(tmp_path),
            "hook_event_name": "UserPromptSubmit",
            "prompt": "记" * 40_000,
        },
        connector_id="connector-01",
    )

    assert len(event.user_text.encode("utf-8")) <= 32 * 1024
    assert event.user_text.endswith("记")
