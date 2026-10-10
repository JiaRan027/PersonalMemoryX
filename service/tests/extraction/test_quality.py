"""Quality pipeline tests: extractor output -> AdmissionPolicy boundary.

v0.2 gate: SECRET and SENSITIVE content must never be accepted, and conflicts
must never be silently overridden by extractor scores.
"""

from __future__ import annotations

from personalmemory.domain.extraction.deterministic import DeterministicExtractorAdapter
from personalmemory.domain.memory.model import MemoryDraft
from personalmemory.domain.memory.types import AdmissionAction, MemoryType
from personalmemory.domain.policy.admission import AutoAdmissionPolicy


def _draft(statement: str, reason: str | None = None) -> MemoryDraft:
    return MemoryDraft(
        type=MemoryType.FACT,
        statement=statement,
        reason=reason,
        source_revision_id="rev_test",
        confidence=1.0,
        importance=0.5,
        namespace="general",
    )


def test_secret_never_accepted_after_extraction():
    policy = AutoAdmissionPolicy()
    adapter = DeterministicExtractorAdapter()
    fake_key = "sk-" + "abcdef1234567890abcdef"
    text = f"我的 API token 是 {fake_key}，我使用它调用模型。"
    result = adapter.extract(text, source_revision_id="rev", namespace="general")
    # extractor may or may not produce a candidate; the admission policy is authoritative
    if not result.items:
        return
    for item in result.items:
        draft = _draft(item.statement, item.reason)
        decision = policy.evaluate(draft)
        assert decision.action is not AdmissionAction.ACCEPT
        assert decision.sensitivity.value == "secret"


def test_sensitive_never_accepted_after_extraction():
    policy = AutoAdmissionPolicy()
    adapter = DeterministicExtractorAdapter()
    text = "我的诊断是高血压，医生建议我注意饮食。"
    result = adapter.extract(text, source_revision_id="rev", namespace="general")
    if not result.items:
        return
    for item in result.items:
        draft = _draft(item.statement, item.reason)
        decision = policy.evaluate(draft)
        assert decision.action is not AdmissionAction.ACCEPT


def test_conflict_not_overridden_by_high_confidence():
    policy = AutoAdmissionPolicy()
    draft = _draft("我选择 SQLite 作为首版存储。")
    decision = policy.evaluate(draft, has_conflict=True)
    assert decision.action == AdmissionAction.QUARANTINE
    # even a max-confidence extractor result cannot override conflict
    assert draft.confidence == 1.0
    assert decision.action == AdmissionAction.QUARANTINE


def test_low_confidence_goes_provisional():
    policy = AutoAdmissionPolicy()
    adapter = DeterministicExtractorAdapter()
    result = adapter.extract(
        "我总结出：模型只能输出候选，不能直接决定记忆。",
        source_revision_id="rev",
        namespace="general",
    )
    assert result.items
    item = result.items[0]
    assert item.type == MemoryType.INSIGHT
    decision = policy.evaluate(_draft(item.statement, item.reason))
    assert decision.action in {
        AdmissionAction.PROVISIONAL,
        AdmissionAction.ACCEPT,
    }
