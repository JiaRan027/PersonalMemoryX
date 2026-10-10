from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from personalmemory.domain.memory.fingerprint import exact_content_fingerprint

_AGENT_WORKFLOW_SECTIONS = frozenset(
    {
        "generaltips",
        "reusableknowledge",
        "failuresandhowtododifferently",
        "whatsinmemory",
    }
)
_WORKFLOW_SYMPTOM_PATTERN = re.compile(r"^\s*symptom\s*:", re.IGNORECASE)
_WORKFLOW_TASK_PATTERN = re.compile(r"\[\s*task\s+\d+\s*\]", re.IGNORECASE)


def _normalized_section(value: object) -> str:
    return re.sub(r"[\s_'’`\-:：]+", "", str(value)).casefold()


def _payload_value(payload: object, key: str) -> object | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get(key)
    if value is not None:
        return value
    # Older imported records may keep source metadata under a provenance map.
    for container_key in ("provenance", "source_metadata", "metadata"):
        container = payload.get(container_key)
        if isinstance(container, dict) and container.get(key) is not None:
            return container[key]
    return None


def dynamic_memory_quality_issues(
    memories: Iterable[Any],
) -> dict[str, list[dict[str, str]]]:
    """Return explainable raw-agent-index warnings without mutating Core.

    A later project-scoped workflow translator can compile useful operations
    into governed candidates. These checks only describe existing active
    records; they are presentation-time warnings, not admission/deletion rules.
    """
    issues: dict[str, list[dict[str, str]]] = defaultdict(list)
    for memory in memories:
        if memory.status != "active":
            continue
        payload = memory.payload or {}
        source_signal = _payload_value(payload, "source_signal")
        authority_verification = _payload_value(payload, "authority_verification")
        evidence_actor = _payload_value(payload, "evidence_actor")
        source_section = _payload_value(payload, "source_section")
        statement = str(memory.statement or "")
        reason = str(memory.reason or "")
        body = f"{statement}\n{reason}"
        is_unverified_agent_memory = (
            source_signal == "agent_memory"
            and authority_verification == "agent_native_unverified"
        )
        trusted_user_evidence = (
            evidence_actor == "user"
            or authority_verification in {"user_explicit", "self_attested", "verified_source"}
        )

        workflow_section = (
            is_unverified_agent_memory
            and _normalized_section(source_section) in _AGENT_WORKFLOW_SECTIONS
        )
        workflow_marker = bool(
            not trusted_user_evidence
            and (
                _WORKFLOW_SYMPTOM_PATTERN.match(statement)
                or _WORKFLOW_TASK_PATTERN.search(body)
            )
        )
        if workflow_section or workflow_marker:
            detail = (
                f"source_section={source_section}"
                if workflow_section and source_section
                else "statement contains agent workflow marker"
            )
            issues[memory.id].append(
                {
                    "code": "AGENT_WORKFLOW_NOTE",
                    "reason": detail,
                }
            )

        if memory.type == "decision" and not reason.strip():
            issues[memory.id].append(
                {
                    "code": "MISSING_DECISION_RATIONALE",
                    "reason": "Decision memory has no rationale.",
                }
            )
    return dict(issues)


def cross_namespace_duplicate_issues(
    memories: Iterable[Any],
) -> dict[str, list[dict[str, str]]]:
    """Describe exact duplicates without changing either memory or its namespace."""
    groups: dict[str, list[Any]] = defaultdict(list)
    for memory in memories:
        if memory.status != "active":
            continue
        fingerprint = memory.exact_content_fingerprint or exact_content_fingerprint(
            memory.type, memory.statement
        )
        groups[fingerprint].append(memory)

    issues: dict[str, list[dict[str, str]]] = defaultdict(list)
    for matches in groups.values():
        namespaces = {memory.namespace for memory in matches}
        if len(matches) < 2 or len(namespaces) < 2:
            continue
        ordered = sorted(matches, key=lambda memory: (memory.created_at, memory.id))
        for memory in ordered:
            for related in ordered:
                if related.id == memory.id or related.namespace == memory.namespace:
                    continue
                issues[memory.id].append(
                    {
                        "code": "CROSS_NAMESPACE_EXACT_DUPLICATE",
                        "related_memory_id": related.id,
                        "related_namespace": related.namespace,
                    }
                )
    return dict(issues)
