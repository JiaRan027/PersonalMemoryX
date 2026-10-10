"""Agent memory format adapters (read-only).

Decodes opaque agent memory files (Codex/Claude JSONL rollouts, Gemini/Cursor
SQLite stores) into role-prefixed conversation text that the extraction
pipeline can consume. Adapters never write to the source file.
"""

from __future__ import annotations

import json
import sqlite3
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from personalmemory.domain.continuity import ContinuityEnvelope, SourceCapabilities

ADAPTER_FORMATS = frozenset({"jsonl", "sqlite", "json"})

MAX_JSONL_ENTRIES = 20_000
MAX_SQLITE_ROWS = 20_000

_ROLE_LABELS = {
    "user": "用户",
    "assistant": "助手",
    "model": "助手",
    "agent": "助手",
    "bot": "助手",
    "system": "系统",
}

_USER_ROLES = {"user", "human"}
_ASSISTANT_ROLES = {"assistant", "model", "agent", "bot"}
_VISIBLE_BLOCK_TYPES = {"text", "input_text", "output_text"}


@dataclass(frozen=True)
class TranscriptMessage:
    role: str
    text: str
    locator: str
    message_id: str | None = None
    session_id: str | None = None
    timestamp: str | None = None


@dataclass(frozen=True)
class ContinuityTranscript:
    source_format: str
    source_family: str
    envelopes: tuple[ContinuityEnvelope, ...]

    def normalized_content(self) -> str:
        return "\n".join(
            envelope.normalized_content() for envelope in self.envelopes
        )


def decode_agent_memory(source_path: Path, format_name: str) -> str:
    """Decode an agent memory file into role-prefixed conversation text."""
    if format_name in {"jsonl", "json"}:
        return decode_continuity_transcript(
            source_path, format_name=format_name
        ).normalized_content()
    if format_name == "sqlite":
        return _decode_sqlite(source_path)
    raise ValueError(f"Unsupported adapter format: {format_name}")


def _block_texts(content) -> list[str]:
    """Extract visible conversational text, excluding tools/thinking/code blocks."""
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        texts: list[str] = []
        for block in content:
            if isinstance(block, str):
                texts.append(block)
            elif isinstance(block, dict):
                block_type = str(block.get("type") or "text").casefold()
                if block_type not in _VISIBLE_BLOCK_TYPES:
                    continue
                text = (
                    block.get("text")
                    or block.get("input_text")
                    or block.get("output_text")
                    or block.get("content")
                )
                if isinstance(text, str):
                    texts.append(text)
        return texts
    if isinstance(content, dict):
        block_type = str(content.get("type") or "text").casefold()
        if block_type not in _VISIBLE_BLOCK_TYPES:
            return []
        text = content.get("text") or content.get("content")
        if isinstance(text, str):
            return [text]
    return []


def _role_label(role: object) -> str:
    if role is None:
        return "用户"
    key = str(role).casefold()
    return _ROLE_LABELS.get(key, str(role))


def _emit(role: object, text: str | None) -> str | None:
    if not text or not text.strip():
        return None
    return f"{_role_label(role)}: {text.strip()}"


def _normalized_role(role: object) -> str | None:
    key = str(role or "").casefold()
    if key in _USER_ROLES:
        return "user"
    if key in _ASSISTANT_ROLES:
        return "assistant"
    return None


def _string_field(obj: dict, *keys: str) -> str | None:
    for key in keys:
        value = obj.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _json_entry_message(obj: dict, *, locator: str) -> TranscriptMessage | None:
    """Translate a known JSON message shape; unknown entries are not guessed."""
    entry_type = obj.get("type")
    payload = obj.get("payload")
    role: object = None
    content: object = None
    metadata_source = obj

    # Codex rollout (current): response_item wrapping a message
    if entry_type == "response_item" and isinstance(payload, dict):
        if payload.get("type") == "message":
            role = payload.get("role")
            content = payload.get("content")
            metadata_source = payload
        else:
            return None

    # Codex rollout (legacy): user_message / agent_message
    elif entry_type in ("user_message", "agent_message"):
        role = "user" if entry_type == "user_message" else "assistant"
        content = payload.get("content") if isinstance(payload, dict) else None
        metadata_source = payload if isinstance(payload, dict) else obj

    # Claude Code history: {"type": "user"|"assistant", "message": {...}}
    elif entry_type in ("user", "assistant"):
        message = obj.get("message")
        if isinstance(message, dict):
            role = message.get("role") or entry_type
            content = message.get("content")
            metadata_source = message
        else:
            return None

    # Claude Code command history: user-entered command text only. The
    # pastedContents object can contain arbitrary clipboard material and is
    # deliberately excluded from offline extraction.
    elif isinstance(obj.get("display"), str):
        role = "user"
        content = obj["display"]

    # Standard transcript: explicit role plus content. Ambiguous entries are skipped.
    elif obj.get("role") is not None:
        role = obj.get("role")
        content = obj.get("content") if "content" in obj else obj.get("text")
    else:
        return None

    normalized_role = _normalized_role(role)
    if normalized_role is None:
        return None
    text = " ".join(piece for piece in _block_texts(content) if piece).strip()
    if not text:
        return None
    return TranscriptMessage(
        role=normalized_role,
        text=text,
        locator=locator,
        message_id=_string_field(
            metadata_source, "id", "message_id", "uuid"
        ) or _string_field(obj, "id", "message_id", "uuid"),
        session_id=_string_field(
            obj, "session_id", "sessionId", "conversation_id", "conversationId"
        ),
        timestamp=_string_field(
            metadata_source, "timestamp", "created_at", "createdAt"
        ) or _string_field(obj, "timestamp", "created_at", "createdAt"),
    )


def _transcript_envelopes(
    messages: list[TranscriptMessage],
    *,
    source_path: Path,
    source_format: str,
    source_family: str,
) -> tuple[ContinuityEnvelope, ...]:
    capabilities = SourceCapabilities(
        has_actor_roles=True,
        has_session=any(message.session_id for message in messages),
        has_timestamps=any(message.timestamp for message in messages),
        is_realtime=False,
    )
    grouped: list[tuple[TranscriptMessage | None, TranscriptMessage | None]] = []
    pending_user: TranscriptMessage | None = None
    for message in messages:
        if message.role == "user":
            if pending_user is not None:
                grouped.append((pending_user, None))
            pending_user = message
        elif pending_user is not None:
            grouped.append((pending_user, message))
            pending_user = None
        else:
            grouped.append((None, message))
    if pending_user is not None:
        grouped.append((pending_user, None))

    envelopes: list[ContinuityEnvelope] = []
    for index, (user, assistant) in enumerate(grouped):
        first = user or assistant
        last = assistant or user
        if first is None or last is None:
            continue
        message_ids = tuple(
            message.message_id
            for message in (user, assistant)
            if message is not None and message.message_id
        )
        interaction_id = (
            (user.message_id if user else None)
            or (assistant.message_id if assistant else None)
            or f"interaction-{index + 1}"
        )
        locator = str(source_path.resolve()) + f"#{first.locator}"
        if last.locator != first.locator:
            locator += f"..{last.locator}"
        envelopes.append(
            ContinuityEnvelope.from_interaction(
                source_kind="transcript",
                source_family=source_family,
                capture_mode="offline_transcript",
                capabilities=capabilities,
                locator=locator,
                interaction_id=interaction_id,
                user_text=user.text if user else None,
                assistant_text=assistant.text if assistant else None,
                session_id=(user.session_id if user else None)
                or (assistant.session_id if assistant else None),
                message_ids=message_ids,
            )
        )
    return tuple(envelopes)


def decode_continuity_transcript(
    source_path: Path,
    *,
    format_name: str,
    source_family: str = "standard_transcript",
) -> ContinuityTranscript:
    messages: list[TranscriptMessage] = []
    if format_name == "jsonl":
        with source_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                if len(messages) >= MAX_JSONL_ENTRIES:
                    break
                try:
                    obj = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                message = _json_entry_message(obj, locator=f"jsonl:L{line_number}")
                if message is not None:
                    messages.append(message)
    elif format_name == "json":
        try:
            data = json.loads(source_path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid JSON source: {error}") from error
        entries = (
            data
            if isinstance(data, list)
            else data.get("messages")
            if isinstance(data, dict)
            else None
        )
        if not isinstance(entries, list):
            raise ValueError("JSON source has no recognizable message list")
        for index, obj in enumerate(entries[:MAX_JSONL_ENTRIES]):
            if not isinstance(obj, dict):
                continue
            message = _json_entry_message(obj, locator=f"json:/messages/{index}")
            if message is not None:
                messages.append(message)
    else:
        raise ValueError(f"Unsupported transcript format: {format_name}")
    return ContinuityTranscript(
        source_format=format_name,
        source_family=source_family,
        envelopes=_transcript_envelopes(
            messages,
            source_path=source_path,
            source_format=format_name,
            source_family=source_family,
        ),
    )


def _decode_jsonl(source_path: Path) -> str:
    """Decode jsonl keeping the most recent entries (rollouts append chronologically)."""
    lines: deque[str] = deque(maxlen=MAX_JSONL_ENTRIES)
    with source_path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            message = _json_entry_message(obj, locator="jsonl")
            if message:
                emitted = _emit(message.role, message.text)
                if emitted:
                    lines.append(emitted)
    return "\n".join(lines)


def _decode_json(source_path: Path) -> str:
    """Single JSON file: a conversation object or an array of messages."""
    try:
        data = json.loads(source_path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON source: {error}") from error
    entries = (
        data
        if isinstance(data, list)
        else data.get("messages")
        if isinstance(data, dict)
        else None
    )
    if not isinstance(entries, list):
        raise ValueError("JSON source has no recognizable message list")
    lines: list[str] = []
    for entry in entries[:MAX_JSONL_ENTRIES]:
        if not isinstance(entry, dict):
            continue
        message = _json_entry_message(entry, locator="json")
        if message:
            emitted = _emit(message.role, message.text)
            if emitted:
                lines.append(emitted)
    return "\n".join(lines)


_TEXT_COLUMN_HINTS = ("content", "text", "message", "payload", "body")
_ROLE_COLUMN_HINTS = ("role", "author", "speaker", "sender", "user")


def _pick_sqlite_table(conn: sqlite3.Connection) -> tuple[str | None, list[str], list[str]]:
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    best: tuple[int, str, list[str], list[str]] | None = None
    for table in tables:
        columns = [
            row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')
        ]
        text_columns = [
            column
            for column in columns
            if any(hint in column.casefold() for hint in _TEXT_COLUMN_HINTS)
        ]
        role_columns = [
            column
            for column in columns
            if any(hint in column.casefold() for hint in _ROLE_COLUMN_HINTS)
        ]
        if not text_columns:
            continue
        score = len(text_columns) * 10 + (len(role_columns) > 0) * 5 + len(columns)
        if best is None or score > best[0]:
            best = (score, table, text_columns, role_columns)
    if best is None:
        return None, [], []
    return best[1], best[2], best[3]


def _sqlite_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("utf-8", errors="replace")
        except Exception:
            return None
    if not isinstance(value, str):
        value = str(value)
    candidate = value.strip()
    if candidate.startswith("{") or candidate.startswith("["):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            pieces = _block_texts(parsed)
            if pieces:
                return " ".join(piece for piece in pieces if piece).strip()
    return candidate or None


def _decode_sqlite(source_path: Path) -> str:
    uris = (f"file:{source_path.as_posix()}?mode=ro", f"file:{source_path.as_posix()}?immutable=1")
    last_error: Exception | None = None
    conn: sqlite3.Connection | None = None
    for uri in uris:
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            break
        except sqlite3.Error as error:
            last_error = error
            conn = None
    if conn is None:
        raise ValueError(f"sqlite open failed: {last_error}")
    try:
        table, text_columns, role_columns = _pick_sqlite_table(conn)
        if table is None or not text_columns:
            return ""
        role_column = role_columns[0] if role_columns else None
        text_column = text_columns[0]
        lines: deque[str] = deque(maxlen=MAX_SQLITE_ROWS)
        for row in conn.execute(
            f'SELECT {role_column + ", " if role_column else ""}"{text_column}" FROM "{table}"'
        ):
            if role_column:
                role, raw = row
            else:
                role, raw = None, row[0]
            text = _sqlite_text(raw)
            emitted = _emit(role, text)
            if emitted:
                lines.append(emitted)
        return "\n".join(lines)
    except sqlite3.Error as error:
        raise ValueError(f"sqlite read failed: {error}") from error
    finally:
        conn.close()
