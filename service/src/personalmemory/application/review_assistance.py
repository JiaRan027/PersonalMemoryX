from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

REVIEW_ASSISTANCE_FILE_NAME = "review-assistance.zh-CN.json"
REVIEW_ASSISTANCE_SCHEMA_VERSION = "personalmemory.review-assistance.v1"
MAX_REVIEW_ASSISTANCE_BYTES = 1_000_000
MAX_REVIEW_ASSISTANCE_ITEMS = 1_000
MAX_REVIEW_ASSISTANCE_TEXT = 2_000
RECOMMENDATIONS = {"keep", "remove", "rewrite"}


class ReviewAssistance(TypedDict):
    locale: str
    summary: str
    explanation: str | None
    recommendation: str
    recommendation_reason: str


def _bounded_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_REVIEW_ASSISTANCE_TEXT:
        return None
    return text


def load_review_assistance(data_dir: Path) -> dict[str, ReviewAssistance]:
    """Load optional display-only review aids without changing authoritative Memory."""
    path = data_dir / REVIEW_ASSISTANCE_FILE_NAME
    try:
        if not path.is_file() or path.stat().st_size > MAX_REVIEW_ASSISTANCE_BYTES:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("schema_version") != REVIEW_ASSISTANCE_SCHEMA_VERSION:
        return {}
    locale = _bounded_text(payload.get("locale"))
    items = payload.get("items")
    if locale is None or not isinstance(items, dict) or len(items) > MAX_REVIEW_ASSISTANCE_ITEMS:
        return {}

    result: dict[str, ReviewAssistance] = {}
    for memory_id, raw in items.items():
        if not isinstance(memory_id, str) or not memory_id.startswith("memory_"):
            continue
        if not isinstance(raw, dict):
            continue
        summary = _bounded_text(raw.get("summary"))
        recommendation = raw.get("recommendation")
        recommendation_reason = _bounded_text(raw.get("recommendation_reason"))
        if (
            summary is None
            or recommendation not in RECOMMENDATIONS
            or recommendation_reason is None
        ):
            continue
        result[memory_id] = {
            "locale": locale,
            "summary": summary,
            "explanation": _bounded_text(raw.get("explanation")),
            "recommendation": recommendation,
            "recommendation_reason": recommendation_reason,
        }
    return result
