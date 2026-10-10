from __future__ import annotations

import json
import os
from contextlib import suppress
from pathlib import Path
from typing import Literal, TypedDict

REVIEW_DECISIONS_FILE_NAME = "review-decisions.json"
REVIEW_DECISIONS_SCHEMA_VERSION = "personalmemory.review-decisions.v1"
MAX_REVIEW_DECISIONS_BYTES = 1_000_000
MAX_REVIEW_DECISIONS_ITEMS = 10_000
MAX_REVIEW_NOTE_LENGTH = 1_000

ReviewVerdict = Literal[
    "keep",
    "project_experience",
    "rewrite",
    "source_only",
    "test_artifact",
    "needs_context",
]
REVIEW_VERDICTS: set[str] = {
    "keep",
    "project_experience",
    "rewrite",
    "source_only",
    "test_artifact",
    "needs_context",
}


class ReviewDecision(TypedDict):
    verdict: ReviewVerdict
    memory_revision: int
    reviewed_at: str
    note: str | None
    scope_hint: str | None


class ReviewDecisionStoreError(RuntimeError):
    pass


def _optional_text(value: object, *, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("review decision text must be a string")
    text = value.strip()
    if len(text) > max_length:
        raise ValueError("review decision text is too long")
    return text or None


def _parse_payload(payload: object) -> dict[str, ReviewDecision]:
    if not isinstance(payload, dict):
        raise ValueError("review decisions must be an object")
    if payload.get("schema_version") != REVIEW_DECISIONS_SCHEMA_VERSION:
        raise ValueError("unknown review decisions schema")
    items = payload.get("items")
    if not isinstance(items, dict) or len(items) > MAX_REVIEW_DECISIONS_ITEMS:
        raise ValueError("invalid review decisions items")

    result: dict[str, ReviewDecision] = {}
    for memory_id, raw in items.items():
        if not isinstance(memory_id, str) or not memory_id.startswith("memory_"):
            raise ValueError("invalid review decision memory id")
        if not isinstance(raw, dict):
            raise ValueError("invalid review decision")
        verdict = raw.get("verdict")
        memory_revision = raw.get("memory_revision")
        reviewed_at = raw.get("reviewed_at")
        if verdict not in REVIEW_VERDICTS:
            raise ValueError("invalid review verdict")
        if (
            not isinstance(memory_revision, int)
            or isinstance(memory_revision, bool)
            or memory_revision < 1
        ):
            raise ValueError("invalid review memory revision")
        if not isinstance(reviewed_at, str) or not reviewed_at.strip():
            raise ValueError("invalid review timestamp")
        result[memory_id] = {
            "verdict": verdict,
            "memory_revision": memory_revision,
            "reviewed_at": reviewed_at.strip(),
            "note": _optional_text(raw.get("note"), max_length=MAX_REVIEW_NOTE_LENGTH),
            "scope_hint": _optional_text(raw.get("scope_hint"), max_length=500),
        }
    return result


def load_review_decisions(data_dir: Path) -> dict[str, ReviewDecision]:
    """Load user-authored gold-set labels without changing canonical Memory."""
    path = data_dir / REVIEW_DECISIONS_FILE_NAME
    try:
        if not path.is_file() or path.stat().st_size > MAX_REVIEW_DECISIONS_BYTES:
            return {}
        return _parse_payload(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return {}


def save_review_decision(
    data_dir: Path,
    memory_id: str,
    decision: ReviewDecision,
) -> ReviewDecision:
    """Atomically persist a non-destructive user review label."""
    path = data_dir / REVIEW_DECISIONS_FILE_NAME
    data_dir.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if path.stat().st_size > MAX_REVIEW_DECISIONS_BYTES:
                raise ValueError("review decisions file is too large")
            existing = _parse_payload(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise ReviewDecisionStoreError("existing review decisions are invalid") from error
    else:
        existing = {}

    existing[memory_id] = decision
    payload = {
        "schema_version": REVIEW_DECISIONS_SCHEMA_VERSION,
        "items": existing,
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    if len(encoded) > MAX_REVIEW_DECISIONS_BYTES:
        raise ReviewDecisionStoreError("review decisions file is too large")

    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(encoded)
        temporary.replace(path)
    except OSError as error:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise ReviewDecisionStoreError("review decision could not be saved") from error
    return decision
