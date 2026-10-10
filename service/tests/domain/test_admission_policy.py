from hypothesis import given
from hypothesis import strategies as st


def make_draft(statement: str, **overrides):
    from personalmemory.domain.memory.model import MemoryDraft

    values = {
        "type": "fact",
        "statement": statement,
        "reason": None,
        "source_revision_id": "source_01",
        "confidence": 0.9,
        "importance": 0.5,
        "namespace": "general",
        "payload": {},
    }
    values.update(overrides)
    return MemoryDraft(**values)


def test_policy_accepts_low_risk_content():
    from personalmemory.domain.policy.admission import AutoAdmissionPolicy

    decision = AutoAdmissionPolicy().evaluate(make_draft("用户喜欢直接的技术方案。"))

    assert decision.action.value == "accept"
    assert decision.epistemic_state.value == "asserted"
    assert decision.sensitivity.value == "personal"
    assert decision.reason_code == "LOW_RISK_AUTHORIZED_SOURCE"


def test_policy_marks_low_confidence_inference_provisional():
    from personalmemory.domain.policy.admission import AutoAdmissionPolicy

    decision = AutoAdmissionPolicy().evaluate(
        make_draft("用户可能熟悉 Rust。", type="skill", confidence=0.45)
    )

    assert decision.action.value == "provisional"
    assert decision.epistemic_state.value == "inferred"
    assert decision.reason_code == "LOW_CONFIDENCE_INFERENCE"


def test_policy_discards_context_poor_information_request():
    from personalmemory.domain.policy.admission import AutoAdmissionPolicy

    decision = AutoAdmissionPolicy().evaluate(
        make_draft("测试用户想知道示例程序的运行机制，便于评估安全边界")
    )

    assert decision.action.value == "discard"
    assert decision.epistemic_state.value == "inferred"
    assert decision.reason_code == "TRANSIENT_INFORMATION_REQUEST"


def test_policy_keeps_explicit_research_goal_with_information_request_words():
    from personalmemory.domain.policy.admission import AutoAdmissionPolicy

    decision = AutoAdmissionPolicy().evaluate(
        make_draft("项目需要研究向量数据库的取舍。", type="goal")
    )

    assert decision.action.value == "accept"
    assert decision.reason_code == "LOW_RISK_AUTHORIZED_SOURCE"


def test_policy_discards_malformed_self_statement_and_question_candidate():
    from personalmemory.domain.policy.admission import AutoAdmissionPolicy

    policy = AutoAdmissionPolicy()
    malformed = policy.evaluate(make_draft("我会：", type="skill"))
    question = policy.evaluate(
        make_draft("是个什么记忆从哪儿来的，它是被截断了吗", type="skill")
    )

    assert malformed.action.value == "discard"
    assert malformed.reason_code == "MALFORMED_SELF_STATEMENT"
    assert question.action.value == "discard"
    assert question.reason_code == "QUESTION_SHAPED_CANDIDATE"


def test_policy_keeps_explicit_research_goal_even_when_question_shaped():
    from personalmemory.domain.policy.admission import AutoAdmissionPolicy

    decision = AutoAdmissionPolicy().evaluate(
        make_draft("研究为什么关联召回没有命中相关经验。", type="goal")
    )

    assert decision.action.value == "accept"
    assert decision.reason_code == "LOW_RISK_AUTHORIZED_SOURCE"


def test_policy_marks_cross_namespace_exact_duplicate_provisional():
    from personalmemory.domain.policy.admission import AutoAdmissionPolicy

    decision = AutoAdmissionPolicy().evaluate(
        make_draft("用户偏好详细的工程化分析。", type="preference"),
        has_cross_namespace_duplicate=True,
    )

    assert decision.action.value == "provisional"
    assert decision.epistemic_state.value == "inferred"
    assert decision.reason_code == "CROSS_NAMESPACE_EXACT_DUPLICATE"


def test_policy_discards_transient_agent_process_narration():
    from personalmemory.domain.policy.admission import AutoAdmissionPolicy

    policy = AutoAdmissionPolicy()
    for statement in (
        "now exploring dynamic keyword discovery",
        "Now delving into identifying comprehensive tests",
        "currently focused on rigorously testing the search pipeline",
        "下一步继续检查检索实现",
    ):
        decision = policy.evaluate(make_draft(statement))
        assert decision.action.value == "discard"
        assert decision.reason_code == "TRANSIENT_PROCESS_NARRATION"


def test_policy_keeps_durable_current_project_fact():
    from personalmemory.domain.policy.admission import AutoAdmissionPolicy

    decision = AutoAdmissionPolicy().evaluate(make_draft("用户正在开发 PersonalMemory。"))

    assert decision.action.value == "accept"


def test_policy_quarantines_conflict():
    from personalmemory.domain.policy.admission import AutoAdmissionPolicy

    decision = AutoAdmissionPolicy().evaluate(
        make_draft("用户当前目标是暂停该项目。", type="goal"),
        has_conflict=True,
    )

    assert decision.action.value == "quarantine"
    assert decision.reason_code == "AUTHORITATIVE_CONFLICT"


def test_policy_restricts_sensitive_and_third_party_content():
    from personalmemory.domain.policy.admission import AutoAdmissionPolicy

    policy = AutoAdmissionPolicy()
    health = policy.evaluate(make_draft("用户最近被诊断为高血压。"))
    third_party = policy.evaluate(
        make_draft("同事正在寻找新工作。", payload={"subject_kind": "third_party"})
    )

    assert health.action.value == "restrict"
    assert health.sensitivity.value == "sensitive"
    assert third_party.action.value == "restrict"
    assert third_party.reason_code == "THIRD_PARTY_INFORMATION"


def test_policy_discards_secret_before_other_rules():
    from personalmemory.domain.policy.admission import AutoAdmissionPolicy

    decision = AutoAdmissionPolicy().evaluate(
        make_draft("password = CorrectHorseBatteryStaple", confidence=0.2),
        has_conflict=True,
    )

    assert decision.action.value == "discard"
    assert decision.sensitivity.value == "secret"
    assert decision.reason_code == "SECRET_DETECTED"


@given(st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")), min_size=8))
def test_password_assignment_can_never_be_admitted(secret_value):
    from personalmemory.domain.policy.admission import AutoAdmissionPolicy

    decision = AutoAdmissionPolicy().evaluate(make_draft(f"api_token: {secret_value}"))

    assert decision.action.value not in {"accept", "provisional"}
