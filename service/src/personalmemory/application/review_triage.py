from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any, Literal, TypedDict

from personalmemory.application.memory_quality import (
    cross_namespace_duplicate_issues,
    dynamic_memory_quality_issues,
)
from personalmemory.application.review_decisions import ReviewDecision, ReviewVerdict
from personalmemory.domain.memory.fingerprint import exact_content_fingerprint
from personalmemory.domain.policy.admission import is_transient_information_request

TriageConfidence = Literal["high", "medium", "low"]


class ReviewTriage(TypedDict):
    suggested_verdict: ReviewVerdict
    effective_verdict: ReviewVerdict
    confidence: TriageConfidence
    reason_codes: list[str]
    requires_user_review: bool
    delivery_eligible: bool
    graph_eligible: bool
    scope_hint: str | None
    overridden_by_user: bool
    memory_revision: int


_TRANSIENT_PATTERNS = (
    re.compile(
        r"^(?:now|currently)\s+(?:exploring|delving|focus(?:ed|ing)|working\s+on|"
        r"investigating|testing|reviewing|analyzing|checking|looking\s+into|"
        r"implementing|updating|running)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:接下来|下一步)\s*(?:开始|继续|检查|分析|探索|测试|实现|更新|处理|运行|查看)"),
)
_TEST_PATH_PATTERN = re.compile(
    r"(?:^|[\\/])(?:tests?|fixtures?|acceptance)(?:[\\/]|$)|"
    r"(?:^|[\\/])test_[^\\/]+\.py$",
    re.IGNORECASE,
)
_HIGH_IMPACT_TYPES = frozenset({"fact", "goal", "preference"})
_DELIVERABLE_VERDICTS = frozenset({"keep", "project_experience"})


def _payload_value(payload: object, key: str) -> object | None:
    if not isinstance(payload, dict):
        return None
    if payload.get(key) is not None:
        return payload[key]
    for container_key in ("provenance", "source_metadata", "metadata"):
        container = payload.get(container_key)
        if isinstance(container, dict) and container.get(key) is not None:
            return container[key]
    return None


def _text_payload_value(payload: object, *keys: str) -> str | None:
    for key in keys:
        value = _payload_value(payload, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _quality_issues(
    memory: Any,
    dynamic: Mapping[str, list[dict[str, str]]],
    external: Mapping[str, list[dict[str, str]]],
) -> list[dict[str, str]]:
    payload_issues = (memory.payload or {}).get("quality_issues", [])
    result: list[dict[str, str]] = []
    for issue in [
        *(payload_issues if isinstance(payload_issues, list) else []),
        *dynamic.get(memory.id, []),
        *external.get(memory.id, []),
    ]:
        if isinstance(issue, dict) and issue not in result:
            result.append(issue)
    return result


def _authority_rank(memory: Any) -> tuple[int, int, int, Any, str]:
    payload = memory.payload or {}
    verification = _text_payload_value(payload, "authority_verification")
    evidence_actor = _text_payload_value(payload, "evidence_actor")
    evidence_excerpt = _text_payload_value(payload, "evidence_excerpt")
    trusted_user = evidence_actor == "user" or verification in {
        "user_explicit",
        "verified_source",
    }
    return (
        0 if trusted_user else 1,
        0 if memory.epistemic_state == "asserted" else 1,
        0 if evidence_excerpt else 1,
        memory.created_at,
        memory.id,
    )


def _duplicate_noncanonical_ids(memories: list[Any]) -> set[str]:
    groups: dict[str, list[Any]] = defaultdict(list)
    for memory in memories:
        if memory.status != "active":
            continue
        fingerprint = memory.exact_content_fingerprint or exact_content_fingerprint(
            memory.type, memory.statement
        )
        groups[fingerprint].append(memory)

    noncanonical: set[str] = set()
    for matches in groups.values():
        if len(matches) < 2 or len({item.namespace for item in matches}) < 2:
            continue
        canonical = min(matches, key=_authority_rank)
        noncanonical.update(item.id for item in matches if item.id != canonical.id)
    return noncanonical


def _is_test_artifact(memory: Any) -> bool:
    payload = memory.payload or {}
    source_signal = _text_payload_value(payload, "source_signal")
    capture_mode = _text_payload_value(payload, "capture_mode")
    locator = _text_payload_value(
        payload, "evidence_locator", "source_path", "locator", "transcript_path"
    )
    return source_signal in {"test", "fixture", "acceptance"} or capture_mode in {
        "test",
        "fixture",
    } or bool(locator and _TEST_PATH_PATTERN.search(locator))


def _is_transient(memory: Any) -> bool:
    statement = str(memory.statement or "").strip()
    return any(pattern.search(statement) for pattern in _TRANSIENT_PATTERNS)


def _is_information_request_fact(memory: Any) -> bool:
    """Reject identity-like fragments whose real purpose was asking a question."""
    if memory.type != "fact":
        return False
    payload = memory.payload or {}
    values = (
        str(memory.statement or ""),
        _text_payload_value(payload, "evidence_excerpt") or "",
    )
    return any(is_transient_information_request(value) for value in values)


def _is_operational_index(memory: Any) -> bool:
    if memory.type != "event":
        return False
    heading, separator, raw_terms = str(memory.statement or "").partition(":")
    terms = [term.strip() for term in raw_terms.split(",") if term.strip()]
    scope = _text_payload_value(memory.payload or {}, "project", "cwd", "source_section") or ""
    return (
        bool(separator)
        and 8 <= len(heading.strip()) <= 120
        and len(terms) >= 3
        and all(len(term) <= 80 for term in terms)
        and ("\\" in scope or "/" in scope)
    )


def _scope_hint(memory: Any) -> str | None:
    payload = memory.payload or {}
    return _text_payload_value(payload, "project", "cwd") or str(memory.namespace)


def _triage(
    memory: Any,
    *,
    issues: list[dict[str, str]],
    noncanonical_duplicate: bool,
    decision: ReviewDecision | None,
) -> ReviewTriage:
    current_decision = (
        decision if decision and decision["memory_revision"] == memory.revision else None
    )
    if current_decision is not None:
        baseline = _triage(
            memory,
            issues=issues,
            noncanonical_duplicate=noncanonical_duplicate,
            decision=None,
        )
        verdict = current_decision["verdict"]
        eligible = verdict in _DELIVERABLE_VERDICTS
        return {
            "suggested_verdict": baseline["suggested_verdict"],
            "effective_verdict": verdict,
            "confidence": "high",
            "reason_codes": ["USER_OVERRIDE"],
            "requires_user_review": False,
            "delivery_eligible": eligible,
            "graph_eligible": eligible,
            "scope_hint": current_decision["scope_hint"],
            "overridden_by_user": True,
            "memory_revision": memory.revision,
        }

    issue_codes = {str(issue.get("code")) for issue in issues}
    payload = memory.payload or {}
    verification = _text_payload_value(payload, "authority_verification")
    evidence_actor = _text_payload_value(payload, "evidence_actor")
    evidence_excerpt = _text_payload_value(payload, "evidence_excerpt")
    source_signal = _text_payload_value(payload, "source_signal")
    trusted_user = evidence_actor == "user" or verification in {
        "user_explicit",
        "verified_source",
    }
    unverified_agent = (
        source_signal == "agent_memory" and verification == "agent_native_unverified"
    )
    unverified_deterministic_high_impact = (
        source_signal == "deterministic"
        and memory.type in _HIGH_IMPACT_TYPES
        and evidence_actor != "user"
        and bool(evidence_excerpt)
    )

    verdict: ReviewVerdict
    confidence: TriageConfidence
    reasons: list[str]
    requires_review = False
    delivery_eligible = True
    graph_eligible = True

    if noncanonical_duplicate:
        verdict, confidence = "source_only", "high"
        reasons = ["NON_CANONICAL_DUPLICATE"]
        delivery_eligible = graph_eligible = False
    elif "AGENT_WORKFLOW_NOTE" in issue_codes:
        verdict, confidence = "source_only", "high"
        reasons = ["AGENT_WORKFLOW_NOTE"]
        delivery_eligible = graph_eligible = False
    elif _is_test_artifact(memory):
        verdict, confidence = "test_artifact", "high"
        reasons = ["TEST_SOURCE"]
        delivery_eligible = graph_eligible = False
    elif _is_information_request_fact(memory):
        # Older deterministic imports could parse "I am X and want to know Y"
        # as a durable identity fact. Preserve the source, but do not let a
        # one-turn information request become the user's long-term profile.
        verdict, confidence = "source_only", "high"
        reasons = ["TRANSIENT_INFORMATION_REQUEST"]
        delivery_eligible = graph_eligible = False
    elif _is_transient(memory):
        verdict, confidence = "source_only", "high"
        reasons = ["TRANSIENT_PROCESS"]
        delivery_eligible = graph_eligible = False
    elif _is_operational_index(memory):
        verdict, confidence = "source_only", "high"
        reasons = ["OPERATIONAL_INDEX"]
        delivery_eligible = graph_eligible = False
    elif memory.sensitivity in {"sensitive", "restricted"} and memory.epistemic_state != "asserted":
        verdict, confidence = "needs_context", "medium"
        reasons = ["SENSITIVE_INFERENCE"]
        requires_review = True
        delivery_eligible = graph_eligible = False
    elif unverified_deterministic_high_impact:
        # Older transcript imports lost the role prefix after sentence
        # splitting. Do not deliver identity/preference claims as facts until
        # their actor is recoverable; this is automatic quarantine, not a
        # request to review every ordinary log entry.
        verdict, confidence = "source_only", "high"
        reasons = ["UNVERIFIED_DETERMINISTIC_HIGH_IMPACT"]
        delivery_eligible = graph_eligible = False
    elif trusted_user:
        verdict, confidence = "keep", "high"
        reasons = ["TRUSTED_USER_EVIDENCE"]
    elif memory.epistemic_state == "asserted":
        verdict, confidence = "keep", "high"
        reasons = ["ASSERTED_CORE"]
    elif unverified_agent and memory.type in _HIGH_IMPACT_TYPES:
        verdict, confidence = "needs_context", "medium"
        reasons = ["UNVERIFIED_AGENT_HIGH_IMPACT"]
        requires_review = True
        delivery_eligible = graph_eligible = False
    elif unverified_agent:
        verdict, confidence = "source_only", "high"
        reasons = ["UNVERIFIED_AGENT_SOURCE"]
        delivery_eligible = graph_eligible = False
    elif evidence_excerpt:
        verdict, confidence = "project_experience", "medium"
        reasons = ["PROVISIONAL_WITH_EVIDENCE"]
    else:
        # Existing governed records predate structured Evidence fields. Preserve
        # their delivery unless a known unsafe source or quality rule matched
        # above; transport metadata alone is not semantic authority.
        verdict, confidence = "keep", "medium"
        reasons = ["GOVERNED_PROVISIONAL"]

    if "MISSING_DECISION_RATIONALE" in issue_codes:
        if verdict == "keep":
            verdict = "rewrite"
            confidence = "medium"
        reasons.append("MISSING_DECISION_RATIONALE")

    known_codes = {
        "AGENT_WORKFLOW_NOTE",
        "CROSS_NAMESPACE_EXACT_DUPLICATE",
        "MISSING_DECISION_RATIONALE",
        "OPERATIONAL_INDEX",
        "UNVERIFIED_DETERMINISTIC_HIGH_IMPACT",
    }
    unknown_codes = sorted(code for code in issue_codes if code not in known_codes)
    if unknown_codes:
        reasons.extend(f"QUALITY:{code}" for code in unknown_codes)
        if delivery_eligible:
            requires_review = True
            delivery_eligible = graph_eligible = False

    return {
        "suggested_verdict": verdict,
        "effective_verdict": verdict,
        "confidence": confidence,
        "reason_codes": reasons,
        "requires_user_review": requires_review,
        "delivery_eligible": delivery_eligible,
        "graph_eligible": graph_eligible,
        "scope_hint": _scope_hint(memory) if verdict == "project_experience" else None,
        "overridden_by_user": False,
        "memory_revision": memory.revision,
    }


def build_review_triage(
    memories: Iterable[Any],
    *,
    quality_by_memory: Mapping[str, list[dict[str, str]]] | None = None,
    review_decisions: Mapping[str, ReviewDecision] | None = None,
) -> dict[str, ReviewTriage]:
    """Build an explainable, reversible system triage view for active Memory."""
    items = [memory for memory in memories if memory.status == "active"]
    dynamic = dynamic_memory_quality_issues(items)
    external = quality_by_memory or cross_namespace_duplicate_issues(items)
    decisions = review_decisions or {}
    noncanonical = _duplicate_noncanonical_ids(items)
    return {
        memory.id: _triage(
            memory,
            issues=_quality_issues(memory, dynamic, external),
            noncanonical_duplicate=memory.id in noncanonical,
            decision=decisions.get(memory.id),
        )
        for memory in items
    }
