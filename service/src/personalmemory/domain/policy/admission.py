from __future__ import annotations

import re

from personalmemory.domain.memory.model import AdmissionDecision, MemoryDraft
from personalmemory.domain.memory.types import (
    AdmissionAction,
    EpistemicState,
    Sensitivity,
)

SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(password|passwd|api[_ -]?token|access[_ -]?token|private[_ -]?key|secret)"
        r"\s*[:=]\s*\S{4,}"
    ),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)


def contains_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in SECRET_PATTERNS)

SENSITIVE_TERMS = (
    "诊断",
    "高血压",
    "病史",
    "病历",
    "银行卡",
    "银行账户",
    "身份证",
    "家庭住址",
    "工资",
    "收入",
    "medical diagnosis",
    "bank account",
    "social security",
    "salary",
)

TRANSIENT_PROCESS_PATTERNS = (
    re.compile(
        r"^(?:now|currently)\s+(?:exploring|delving|focus(?:ed|ing)|working\s+on|"
        r"investigating|testing|reviewing|analyzing|checking|looking\s+into|"
        r"implementing|updating|running)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:接下来|下一步)\s*(?:开始|继续|检查|分析|探索|测试|实现|更新|处理|运行|查看)",
        re.IGNORECASE,
    ),
)

TRANSIENT_INFORMATION_REQUEST_PATTERNS = (
    re.compile(
        r"(?:想|希望|需要)(?:知道|了解|查看|询问|研究)(?:他|她|它|这个|该|其)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:wants?|needs?|would like) to (?:know|understand|inspect|ask|learn about)\b",
        re.IGNORECASE,
    ),
)

MALFORMED_SELF_STATEMENT_PATTERN = re.compile(
    r"^(?:我会|我能|我掌握|我熟悉|我用过|我精通|"
    r"I (?:can|know how to|am good at|have used|master))\s*[:：]?\s*$",
    re.IGNORECASE,
)

QUESTION_SHAPED_CANDIDATE_PATTERN = re.compile(
    r"(?:\?|？|什么|为什么|为何|如何|怎么|哪(?:儿|里|个)?|是否|是不是|"
    r"吗(?:$|[，,。.!！？?]))",
    re.IGNORECASE,
)


def is_transient_process_narration(value: str) -> bool:
    return any(pattern.search(value.strip()) for pattern in TRANSIENT_PROCESS_PATTERNS)


def is_transient_information_request(value: str) -> bool:
    return any(
        pattern.search(value.strip()) for pattern in TRANSIENT_INFORMATION_REQUEST_PATTERNS
    )


def is_malformed_self_statement(value: str) -> bool:
    return MALFORMED_SELF_STATEMENT_PATTERN.fullmatch(value.strip()) is not None


def is_question_shaped_candidate(value: str) -> bool:
    return QUESTION_SHAPED_CANDIDATE_PATTERN.search(value.strip()) is not None


class AutoAdmissionPolicy:
    version = "0.1.3"
    provisional_threshold = 0.6

    def evaluate(
        self,
        draft: MemoryDraft,
        *,
        has_conflict: bool = False,
        has_cross_namespace_duplicate: bool = False,
    ) -> AdmissionDecision:
        if contains_secret("\n".join(value for value in (draft.statement, draft.reason) if value)):
            return self._decision(
                AdmissionAction.DISCARD,
                Sensitivity.SECRET,
                EpistemicState.INFERRED,
                "SECRET_DETECTED",
            )

        if is_transient_process_narration(draft.statement):
            return self._decision(
                AdmissionAction.DISCARD,
                Sensitivity.PERSONAL,
                EpistemicState.INFERRED,
                "TRANSIENT_PROCESS_NARRATION",
            )

        if is_malformed_self_statement(draft.statement):
            return self._decision(
                AdmissionAction.DISCARD,
                Sensitivity.PERSONAL,
                EpistemicState.INFERRED,
                "MALFORMED_SELF_STATEMENT",
            )

        # Questions can be emitted by extractors after a self-reference prefix.
        # Explicit goals remain admissible because a research question may be durable.
        if draft.type != "goal" and is_question_shaped_candidate(draft.statement):
            return self._decision(
                AdmissionAction.DISCARD,
                Sensitivity.PERSONAL,
                EpistemicState.INFERRED,
                "QUESTION_SHAPED_CANDIDATE",
            )

        # The deterministic extractor can mistake a question/request for a fact.
        # Keep explicit goals and plans even when they use the same vocabulary.
        if (
            draft.type == "fact"
            and is_transient_information_request(draft.statement)
        ):
            return self._decision(
                AdmissionAction.DISCARD,
                Sensitivity.PERSONAL,
                EpistemicState.INFERRED,
                "TRANSIENT_INFORMATION_REQUEST",
            )

        if draft.payload.get("subject_kind") == "third_party":
            return self._decision(
                AdmissionAction.RESTRICT,
                Sensitivity.SENSITIVE,
                EpistemicState.ASSERTED,
                "THIRD_PARTY_INFORMATION",
            )

        if self._contains_sensitive_content(draft.statement):
            return self._decision(
                AdmissionAction.RESTRICT,
                Sensitivity.SENSITIVE,
                EpistemicState.ASSERTED,
                "SENSITIVE_CONTENT",
            )

        if has_conflict:
            return self._decision(
                AdmissionAction.QUARANTINE,
                Sensitivity.PERSONAL,
                EpistemicState.DISPUTED,
                "AUTHORITATIVE_CONFLICT",
            )

        if has_cross_namespace_duplicate:
            return self._decision(
                AdmissionAction.PROVISIONAL,
                Sensitivity.PERSONAL,
                EpistemicState.INFERRED,
                "CROSS_NAMESPACE_EXACT_DUPLICATE",
            )

        authority = draft.payload.get("semantic_authority")
        evidence_actor = draft.payload.get("evidence_actor")
        if isinstance(authority, dict):
            actor_kind = authority.get("actor_kind")
            assertion_kind = authority.get("assertion_kind")
            if actor_kind == "user" and evidence_actor not in {"user", None}:
                return self._decision(
                    AdmissionAction.DISCARD,
                    Sensitivity.PERSONAL,
                    EpistemicState.INFERRED,
                    "SEMANTIC_AUTHORITY_MISMATCH",
                )
            if assertion_kind in {"inferred", "observed", "unknown"}:
                return self._decision(
                    AdmissionAction.PROVISIONAL,
                    Sensitivity.PERSONAL,
                    EpistemicState.INFERRED,
                    (
                        "AGENT_INFERENCE"
                        if assertion_kind == "inferred"
                        else "UNCONFIRMED_OBSERVATION"
                    ),
                )

        if draft.confidence < self.provisional_threshold:
            return self._decision(
                AdmissionAction.PROVISIONAL,
                Sensitivity.PERSONAL,
                EpistemicState.INFERRED,
                "LOW_CONFIDENCE_INFERENCE",
            )

        return self._decision(
            AdmissionAction.ACCEPT,
            Sensitivity.PERSONAL,
            EpistemicState.ASSERTED,
            "LOW_RISK_AUTHORIZED_SOURCE",
        )

    @staticmethod
    def _contains_secret(statement: str) -> bool:
        return contains_secret(statement)

    @staticmethod
    def _contains_sensitive_content(statement: str) -> bool:
        normalized = statement.casefold()
        return any(term.casefold() in normalized for term in SENSITIVE_TERMS)

    def _decision(
        self,
        action: AdmissionAction,
        sensitivity: Sensitivity,
        epistemic_state: EpistemicState,
        reason_code: str,
    ) -> AdmissionDecision:
        return AdmissionDecision(
            action=action,
            sensitivity=sensitivity,
            epistemic_state=epistemic_state,
            reason_code=reason_code,
            policy_version=self.version,
        )
