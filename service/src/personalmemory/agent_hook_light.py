"""Small stdlib-only Claude Hook sidecar.

This process is launched synchronously for every prompt, so it intentionally
does not import the MCP server, httpx, Pydantic, or application modules.
The authenticated Service remains the validation and policy authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib import request

DEFAULT_ENDPOINT = "http://127.0.0.1:19191"
HOOK_DELIVERY_TIMEOUT_SECONDS = 0.5
MAX_HOOK_TEXT_BYTES = 32 * 1024
DIAGNOSTICS_ENV = "PERSONALMEMORY_HOOK_DIAGNOSTICS"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,180}$")


def _configure_utf8_streams() -> None:
    """Keep the JSON hook contract UTF-8 on Windows console code pages."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="strict")


def _truncate_utf8(value: str) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= MAX_HOOK_TEXT_BYTES:
        return value
    return encoded[:MAX_HOOK_TEXT_BYTES].decode("utf-8", errors="ignore")


def _default_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "PersonalMemoryData"
    return Path.home() / ".personalmemory"


def resolve_connection() -> tuple[str, str]:
    endpoint = os.environ.get("PERSONALMEMORY_ENDPOINT", "").strip()
    token = os.environ.get("PERSONALMEMORY_TOKEN", "").strip()
    if endpoint and token:
        return endpoint, token
    configured = os.environ.get("PERSONALMEMORY_DATA_DIR", "").strip()
    path = (Path(configured) if configured else _default_data_dir()) / "runtime" / "connection.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        endpoint = endpoint or str(payload.get("endpoint") or "").strip()
        token = token or str(payload.get("token") or "").strip()
    return endpoint or DEFAULT_ENDPOINT, token


def _absolute_path(raw: object, field: str, *, jsonl: bool = False) -> str:
    value = str(raw or "").strip()
    path = Path(value)
    if not value or not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"invalid {field}")
    if jsonl and path.suffix.casefold() != ".jsonl":
        raise ValueError("invalid transcript_path")
    return str(path.resolve(strict=False))


def _transcript_locator(
    raw: object, *, agent_family: str, session_id: str
) -> str:
    if raw is None and agent_family == "codex":
        return f"codex://session/{session_id}"
    return _absolute_path(raw, "transcript_path", jsonl=True)


def _prompt_id(
    payload: dict[str, object],
    *,
    event_name: str,
    session_id: str,
    transcript_path: str,
    selected_text: str,
) -> str | None:
    raw = payload.get("prompt_id") if "prompt_id" in payload else payload.get("promptId")
    if raw is None:
        raw = payload.get("turn_id")
    if raw is not None:
        value = str(raw)
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("invalid prompt_id")
        return value
    if event_name != "UserPromptSubmit":
        return None
    try:
        stat = Path(transcript_path).stat()
        transcript_state = f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        transcript_state = "unavailable"
    identity = "\0".join((session_id, transcript_path, transcript_state, selected_text))
    return f"prompt:auto:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"


def translate_payload(
    payload: dict[str, object], connector_id: str, agent_family: str = "claude_code"
) -> dict[str, object]:
    if not _IDENTIFIER.fullmatch(connector_id):
        raise ValueError("invalid connector_id")
    event_name = str(payload.get("hook_event_name") or "")
    if event_name not in {"UserPromptSubmit", "Stop"}:
        raise ValueError("unsupported hook event")
    session_id = str(payload.get("session_id") or "")
    if not _IDENTIFIER.fullmatch(session_id):
        raise ValueError("invalid session_id")
    user_text = payload.get("prompt") if event_name == "UserPromptSubmit" else None
    assistant_text = payload.get("last_assistant_message") if event_name == "Stop" else None
    selected_text = user_text if user_text is not None else assistant_text
    if not isinstance(selected_text, str) or not selected_text.strip():
        raise ValueError("hook text is required")
    selected_text = _truncate_utf8(selected_text.strip())
    transcript_path = _transcript_locator(
        payload.get("transcript_path"),
        agent_family=agent_family,
        session_id=session_id,
    )
    prompt_id = _prompt_id(
        payload,
        event_name=event_name,
        session_id=session_id,
        transcript_path=transcript_path,
        selected_text=selected_text,
    )
    return {
        "schema_version": "1",
        "connector_id": connector_id,
        "agent_family": agent_family,
        "fragment_kind": (
            "user_message" if event_name == "UserPromptSubmit" else "assistant_message"
        ),
        "session_id": session_id,
        "prompt_id": prompt_id,
        "cwd": _absolute_path(payload.get("cwd"), "cwd"),
        "transcript_path": transcript_path,
        "user_text": selected_text if event_name == "UserPromptSubmit" else None,
        "assistant_text": selected_text if event_name == "Stop" else None,
    }


def deliver(
    connector_id: str,
    payload: dict[str, object],
    *,
    agent_family: str = "claude_code",
) -> str | None:
    event = translate_payload(payload, connector_id, agent_family)
    endpoint, token = resolve_connection()
    if not token:
        if os.environ.get(DIAGNOSTICS_ENV) == "1":
            print("PersonalMemory Hook: runtime connection unavailable", file=sys.stderr)
        return None
    body = json.dumps(event, ensure_ascii=False).encode("utf-8")
    http_request = request.Request(
        endpoint.rstrip("/") + "/api/v1/observations/agent-turns",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with request.urlopen(http_request, timeout=HOOK_DELIVERY_TIMEOUT_SECONDS) as response:
        if response.status not in {200, 201, 202}:
            return None
        result = json.loads(response.read().decode("utf-8"))
    if event["fragment_kind"] != "user_message":
        return ""
    context = (result.get("preflight") or {}).get("context")
    return context if isinstance(context, str) else ""


def run_hook(connector_id: str, *, agent_family: str = "claude_code") -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        # Avoid changing the long-standing Claude call boundary. This also
        # keeps older packaged wrappers compatible while Codex opts in.
        context = (
            deliver(connector_id, payload)
            if agent_family == "claude_code"
            else deliver(connector_id, payload, agent_family=agent_family)
        )
        if context:
            json.dump(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": context,
                    }
                },
                sys.stdout,
                ensure_ascii=False,
            )
    except Exception as error:
        if os.environ.get(DIAGNOSTICS_ENV) == "1":
            print(f"PersonalMemory Hook: {type(error).__name__}: {error}", file=sys.stderr)
    return 0


def main() -> None:
    _configure_utf8_streams()
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("command", nargs="?", default="hook")
    parser.add_argument("--connector-id", required=True)
    parser.add_argument(
        "--agent-family",
        choices=("claude_code", "codex"),
        default="claude_code",
    )
    args = parser.parse_args()
    if args.command != "hook":
        raise SystemExit(0)
    raise SystemExit(run_hook(args.connector_id, agent_family=args.agent_family))


if __name__ == "__main__":
    main()
