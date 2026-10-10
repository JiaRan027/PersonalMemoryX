from __future__ import annotations

import io
import json

import httpx
import personalmemory.agent_hook as hook
import personalmemory.agent_mcp as mcp


def hook_payload(tmp_path) -> dict[str, object]:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")
    return {
        "session_id": "session-01",
        "prompt_id": "prompt-01",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "我决定采用 SQLite。",
    }


def test_hook_delivers_translated_event_with_one_second_timeout(tmp_path, monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    class Response:
        status_code = 202

        @staticmethod
        def json():
            return {"preflight": {"context": "relevant context"}}

    def fake_post(url, **kwargs):
        captured.append({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(hook, "resolve_connection", lambda: ("http://127.0.0.1:19191", "token"))
    monkeypatch.setattr(hook.httpx, "post", fake_post)

    delivered = hook.deliver_claude_hook("connector-01", hook_payload(tmp_path))

    assert delivered == "relevant context"
    assert [call["url"] for call in captured] == [
        "http://127.0.0.1:19191/api/v1/observations/agent-turns"
    ]
    assert all(call["timeout"] == 1.0 for call in captured)
    assert captured[0]["headers"] == {"Authorization": "Bearer token"}
    assert captured[0]["json"]["agent_family"] == "claude_code"
    assert captured[0]["json"]["user_text"] == "我决定采用 SQLite。"


def test_build_claude_hook_settings_is_project_reviewable() -> None:
    settings = hook.build_claude_hook_settings(
        r"C:\Program Files\PersonalMemory\personalmemory-mcp.exe", "connector-01"
    )

    prompt_hook = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    stop_hook = settings["hooks"]["Stop"][0]["hooks"][0]
    assert prompt_hook["type"] == "command"
    assert prompt_hook["async"] is False
    assert stop_hook["type"] == "command"
    assert stop_hook["async"] is False
    assert prompt_hook["timeout"] == 5
    assert stop_hook["timeout"] == 5
    assert prompt_hook["command"] == stop_hook["command"]
    assert "'C:/Program Files/PersonalMemory/personalmemory-mcp.exe'" in prompt_hook["command"]
    assert prompt_hook["command"].endswith("hook --connector-id connector-01")


def test_hook_is_silent_and_fail_open_when_service_is_down(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(hook, "resolve_connection", lambda: ("http://127.0.0.1:1", "token"))

    def unavailable(*_args, **_kwargs):
        raise httpx.ConnectError("unavailable")

    monkeypatch.setattr(hook.httpx, "post", unavailable)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_payload(tmp_path))))

    assert hook.run_hook("connector-01") == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_hook_writes_claude_additional_context_only_for_a_nonempty_match(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(hook, "deliver_claude_hook", lambda *_args, **_kwargs: "context")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_payload(tmp_path))))

    assert hook.run_hook("connector-01") == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "context",
        }
    }

    monkeypatch.setattr(hook, "deliver_claude_hook", lambda *_args, **_kwargs: "")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_payload(tmp_path))))
    assert hook.run_hook("connector-01") == 0
    assert capsys.readouterr().out == ""


def test_mcp_command_dispatches_hook_without_starting_stdio_server(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        "sys.argv", ["personalmemory-mcp", "hook", "--connector-id", "connector-01"]
    )
    monkeypatch.setattr(hook, "run_hook", lambda connector_id: called.append(connector_id) or 0)

    try:
        mcp.main()
    except SystemExit as error:
        assert error.code == 0

    assert called == ["connector-01"]


def test_mcp_command_dispatches_codex_hook_with_explicit_agent_family(monkeypatch) -> None:
    called: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "sys.argv",
        [
            "personalmemory-mcp",
            "hook",
            "--connector-id",
            "connector-01",
            "--agent-family",
            "codex",
        ],
    )
    monkeypatch.setattr(
        hook,
        "run_hook",
        lambda connector_id, *, agent_family: called.append((connector_id, agent_family)) or 0,
    )

    try:
        mcp.main()
    except SystemExit as error:
        assert error.code == 0

    assert called == [("connector-01", "codex")]
