from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from personalmemory.domain.observation import InteractionFragment


class HookInputError(ValueError):
    pass


def _prompt_id(
    payload: dict[str, Any],
    *,
    event_name: str,
    session_id: object,
    transcript_path: str,
    selected_text: object,
) -> object:
    explicit = payload.get("prompt_id") if "prompt_id" in payload else payload.get("promptId")
    if explicit is None:
        explicit = payload.get("turn_id")
    if explicit is not None or event_name != "UserPromptSubmit":
        return explicit
    try:
        stat = Path(transcript_path).stat()
        transcript_state = f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        transcript_state = "unavailable"
    identity = "\0".join(
        (str(session_id or ""), transcript_path, transcript_state, str(selected_text or ""))
    )
    return f"prompt:auto:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"


def _absolute_path(raw: object, field: str, *, jsonl: bool = False) -> str:
    value = str(raw or "").strip()
    if not value:
        raise HookInputError(f"{field} is required")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise HookInputError(f"{field} must be an absolute normalized path")
    if jsonl and path.suffix.casefold() != ".jsonl":
        raise HookInputError("transcript_path must be a JSONL file")
    return str(path.resolve(strict=False))


def _transcript_locator(
    raw: object, *, agent_family: str, session_id: object
) -> str:
    if raw is None and agent_family == "codex":
        # Codex explicitly permits transcript_path=null. Preserve truthful
        # provenance without inventing a clickable filesystem location.
        return f"codex://session/{session_id}"
    return _absolute_path(raw, "transcript_path", jsonl=True)


def parse_agent_hook_event(
    payload: dict[str, Any], *, connector_id: str, agent_family: str
) -> InteractionFragment:
    if not isinstance(payload, dict):
        raise HookInputError("hook input must be an object")
    event_name = str(payload.get("hook_event_name") or "")
    transcript_path = _transcript_locator(
        payload.get("transcript_path"),
        agent_family=agent_family,
        session_id=payload.get("session_id"),
    )
    selected_text = (
        payload.get("prompt")
        if event_name == "UserPromptSubmit"
        else payload.get("last_assistant_message") if event_name == "Stop" else None
    )
    common = {
        "schema_version": "1",
        "connector_id": connector_id,
        "agent_family": agent_family,
        "fragment_kind": (
            "user_message" if event_name == "UserPromptSubmit" else "assistant_message"
        ),
        "session_id": payload.get("session_id"),
        "prompt_id": _prompt_id(
            payload,
            event_name=event_name,
            session_id=payload.get("session_id"),
            transcript_path=transcript_path,
            selected_text=selected_text,
        ),
        "cwd": _absolute_path(payload.get("cwd"), "cwd"),
        "transcript_path": transcript_path,
        "user_text": selected_text if event_name == "UserPromptSubmit" else None,
        "assistant_text": selected_text if event_name == "Stop" else None,
    }
    try:
        return InteractionFragment.model_validate(common)
    except ValidationError as error:
        raise HookInputError(str(error)) from error


def parse_claude_hook_event(payload: dict[str, Any], *, connector_id: str) -> InteractionFragment:
    """Translate the existing Claude lifecycle format into a Core fragment."""
    return parse_agent_hook_event(
        payload,
        connector_id=connector_id,
        agent_family="claude_code",
    )


def _visible_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    pieces: list[str] = []
    for block in content:
        if isinstance(block, str):
            pieces.append(block)
            continue
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "text").casefold()
        if block_type not in {"text", "input_text"}:
            continue
        text = block.get("text") or block.get("input_text")
        if isinstance(text, str) and text.strip():
            pieces.append(text.strip())
    return "\n".join(pieces).strip()


def recover_latest_user_text(transcript_path: Path) -> str | None:
    """Best-effort recovery used only when UserPromptSubmit delivery was missed."""
    latest: str | None = None
    try:
        handle = transcript_path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None
    with handle:
        for raw_line in handle:
            try:
                entry = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict) or entry.get("type") != "user":
                continue
            message = entry.get("message")
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            text = _visible_text(message.get("content"))
            if text:
                latest = text
    return latest
