from __future__ import annotations

import io
import json

import personalmemory.agent_hook_light as hook


def payload(tmp_path) -> dict[str, object]:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")
    return {
        "session_id": "session-01",
        "prompt_id": "prompt-01",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "I prefer local-first storage.",
    }


def test_light_hook_translates_without_application_dependencies(tmp_path) -> None:
    translated = hook.translate_payload(payload(tmp_path), "connector-01")
    assert translated["fragment_kind"] == "user_message"
    assert translated["user_text"] == "I prefer local-first storage."
    assert translated["assistant_text"] is None


def test_light_hook_synthesizes_prompt_id_for_real_claude_payload(tmp_path) -> None:
    hook_input = payload(tmp_path)
    hook_input.pop("prompt_id")

    first = hook.translate_payload(hook_input, "connector-01")
    second = hook.translate_payload(hook_input, "connector-01")

    assert str(first["prompt_id"]).startswith("prompt:auto:")
    assert first["prompt_id"] == second["prompt_id"]


def test_light_codex_hook_accepts_null_transcript_path(tmp_path) -> None:
    hook_input = payload(tmp_path)
    hook_input.pop("prompt_id")
    hook_input["turn_id"] = "turn-01"
    hook_input["transcript_path"] = None

    translated = hook.translate_payload(hook_input, "connector-01", "codex")

    assert translated["prompt_id"] == "turn-01"
    assert translated["transcript_path"] == "codex://session/session-01"


def test_light_hook_emits_only_nonempty_preflight_context(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(hook, "deliver", lambda *_args: "bounded context")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload(tmp_path))))
    assert hook.run_hook("connector-01") == 0
    assert json.loads(capsys.readouterr().out) == {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "bounded context",
        }
    }


def test_light_hook_is_silent_and_fail_open(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(hook, "deliver", lambda *_args: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload(tmp_path))))
    assert hook.run_hook("connector-01") == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_light_hook_can_report_opt_in_diagnostics(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(hook, "deliver", lambda *_args: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload(tmp_path))))
    monkeypatch.setenv("PERSONALMEMORY_HOOK_DIAGNOSTICS", "1")

    assert hook.run_hook("connector-01") == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "PersonalMemory Hook: OSError: offline\n"
