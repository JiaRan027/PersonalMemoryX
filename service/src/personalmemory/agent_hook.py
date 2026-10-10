"""Fail-open delivery boundary for agent lifecycle hooks."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from typing import Any

import httpx

from personalmemory.agent_mcp import resolve_connection
from personalmemory.infrastructure.claude_hook import parse_agent_hook_event

HOOK_DELIVERY_TIMEOUT_SECONDS = 1.0


def build_claude_hook_settings(executable: str, connector_id: str) -> dict[str, object]:
    executable_for_shell = executable.replace("\\", "/")
    command = f"{shlex.quote(executable_for_shell)} hook --connector-id {shlex.quote(connector_id)}"

    def command_hook(*, asynchronous: bool) -> dict[str, object]:
        return {
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "async": asynchronous,
                    "timeout": 5,
                }
            ]
        }

    return {
        "hooks": {
            # Additional context is only consumed from a synchronous hook response.
            "UserPromptSubmit": [command_hook(asynchronous=False)],
            # Claude can exit before an asynchronous Stop process delivers the final turn.
            "Stop": [command_hook(asynchronous=False)],
        }
    }


def deliver_agent_hook(
    connector_id: str,
    payload: dict[str, Any],
    *,
    agent_family: str = "claude_code",
    timeout: float = HOOK_DELIVERY_TIMEOUT_SECONDS,
) -> str | None:
    """Translate and deliver one event; expected boundary failures are silent."""
    try:
        event = parse_agent_hook_event(
            payload,
            connector_id=connector_id,
            agent_family=agent_family,
        )
        endpoint, token = resolve_connection()
        if not token:
            return None
        response = httpx.post(
            endpoint.rstrip("/") + "/api/v1/observations/agent-turns",
            headers={"Authorization": f"Bearer {token}"},
            json=event.model_dump(mode="json"),
            timeout=timeout,
        )
        if response.status_code not in {200, 201, 202}:
            return None
        if event.fragment_kind != "user_message" or not event.user_text:
            return ""
        context = (response.json().get("preflight") or {}).get("context")
        return context if isinstance(context, str) else ""
    except Exception:  # Hook delivery must never block the host Agent.
        return None


def deliver_claude_hook(
    connector_id: str,
    payload: dict[str, Any],
    *,
    timeout: float = HOOK_DELIVERY_TIMEOUT_SECONDS,
) -> str | None:
    """Compatibility wrapper for the original Claude-only entry point."""
    return deliver_agent_hook(connector_id, payload, timeout=timeout)


def run_hook(connector_id: str, *, agent_family: str = "claude_code") -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        # Keep the original Claude seam stable for packaged and test callers;
        # Codex uses the shared translator with its explicit agent identity.
        context = (
            deliver_claude_hook(connector_id, payload)
            if agent_family == "claude_code"
            else deliver_agent_hook(connector_id, payload, agent_family=agent_family)
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
    except Exception:  # stdin and translation failures are intentionally silent.
        pass
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("command", nargs="?", default="hook")
    parser.add_argument("--connector-id", required=True)
    parser.add_argument(
        "--agent-family",
        choices=("claude_code", "codex"),
        default="claude_code",
    )
    args = parser.parse_args()
    if args.command == "hook":
        raise SystemExit(run_hook(args.connector_id, agent_family=args.agent_family))


if __name__ == "__main__":
    main()
