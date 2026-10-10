"""Deterministic extractor evaluation on the fixed eval set (v0.2 Step 1/2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from personalmemory.domain.extraction.deterministic import DeterministicExtractorAdapter
from personalmemory.domain.memory.types import MemoryType

FIXTURES = Path(__file__).parent / "fixtures" / "eval_set.jsonl"


def _load_samples():
    with FIXTURES.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


SAMPLES = _load_samples()


def _extract(text: str):
    adapter = DeterministicExtractorAdapter()
    return adapter.extract(text, source_revision_id="rev_test", namespace="general")


@pytest.mark.parametrize(
    "sample",
    SAMPLES,
    ids=[s["id"] for s in SAMPLES],
)
def test_eval_set_sample(sample):
    result = _extract(sample["text"])
    if sample["expected_type"] == "none":
        assert result.items == [], (
            f"{sample['id']}: expected no extraction, got {[i.type for i in result.items]}"
        )
        return
    assert len(result.items) == 1, (
        f"{sample['id']}: expected exactly 1 item, got {len(result.items)}"
    )
    item = result.items[0]
    assert item.type == MemoryType(sample["expected_type"]), (
        f"{sample['id']}: type {item.type} != {sample['expected_type']}"
    )
    for sub in sample.get("expected_statement_substr", []):
        assert sub in item.statement, (
            f"{sample['id']}: statement {item.statement!r} missing {sub!r}"
        )
    reason_subs = sample.get("expected_reason_substr") or []
    for reason_sub in reason_subs:
        assert item.reason is not None, f"{sample['id']}: expected reason"
        assert reason_sub in item.reason, (
            f"{sample['id']}: reason {item.reason!r} missing {reason_sub!r}"
        )
    assert 0.0 <= item.confidence <= 1.0
    assert item.evidence_span[1] > item.evidence_span[0]
    assert item.source_signal == "deterministic"



def test_extract_strips_user_role_prefix(tmp_path=None):
    result = _extract("用户: 我的目标是进入人工智能行业。")
    assert len(result.items) == 1
    assert result.items[0].type == MemoryType.GOAL
    assert "人工智能行业" in result.items[0].statement


def test_extract_skips_assistant_role_prefix():
    result = _extract("助手: 我建议你先学习 Python。\n用户: 我掌握 Python。")
    assert len(result.items) == 1
    assert result.items[0].type == MemoryType.SKILL
    assert "Python" in result.items[0].statement


def test_extract_skips_identity_examples_in_fenced_text_and_keeps_role_context():
    text = (
        "用户: 我在讨论身份记忆。\n"
        "```\n我是示例工程师\n```\n"
        "我是一名示例学生。"
    )
    items = _extract(text).items
    assert [item.statement for item in items] == ["讨论身份记忆", "一名示例学生"]
    start, end = items[-1].evidence_span
    assert text[start:end] == "我是一名示例学生"


def test_extract_plain_text_without_prefix_unchanged():
    result = _extract("我决定采用结构化查询优先。")
    assert len(result.items) == 1
    assert result.items[0].type == MemoryType.DECISION

def test_eval_set_quality_gates():
    """v0.2 release gates: precision >= 0.80, miss <= 0.35 on the eval set."""
    positives = [s for s in SAMPLES if s["expected_type"] != "none"]
    negatives = [s for s in SAMPLES if s["expected_type"] == "none"]

    true_positive = 0
    false_positive = 0
    missed = 0
    for sample in positives:
        items = _extract(sample["text"]).items
        if any(i.type == MemoryType(sample["expected_type"]) for i in items):
            true_positive += 1
        else:
            missed += 1
    for sample in negatives:
        items = _extract(sample["text"]).items
        if items:
            false_positive += 1

    denominator = true_positive + false_positive
    precision = true_positive / denominator if denominator else 1.0
    miss_rate = missed / len(positives)

    assert true_positive + false_positive > 0
    assert precision >= 0.80, f"precision {precision:.3f} < 0.80"
    assert miss_rate <= 0.35, f"miss_rate {miss_rate:.3f} > 0.35"
    assert false_positive == 0, f"negative samples produced items: {false_positive}"


def test_priority_goal_over_event():
    result = _extract("我的计划是先完成评估集，再做事件记录。")
    assert result.items and result.items[0].type == MemoryType.GOAL


@pytest.mark.parametrize(
    "text, expected",
    [
        ("我要开发一个示例应用。", "开发一个示例应用"),
        ("用户: 我想开发一个示例应用。", "开发一个示例应用"),
        ("我准备创建一款示例应用。", "创建一款示例应用"),
    ],
)
def test_explicit_project_creation_intent_is_a_goal(text, expected):
    items = _extract(text).items
    assert len(items) == 1
    assert items[0].type == MemoryType.GOAL
    assert items[0].statement == expected


@pytest.mark.parametrize(
    "text",
    [
        "我要你打开这个文件。",
        "我想看看日志。",
        "我准备删除临时目录。",
        "我要查一下构建错误。",
        "我想让你扫描工作区。",
    ],
)
def test_immediate_agent_instructions_are_not_goals(text):
    assert _extract(text).items == []


def test_question_is_skipped():
    result = _extract("你能帮我优化这段代码吗？")
    assert result.items == []


@pytest.mark.parametrize(
    "text",
    [
        "我会：",
        "我会：是个 什么记忆从哪儿来的，它是被截断了吗？",
    ],
)
def test_self_statement_prefix_and_questions_are_skipped(text):
    assert _extract(text).items == []


def test_empty_and_blank():
    assert _extract("").items == []
    assert _extract("   ").items == []


def test_skill_rule_rejects_intention_instructions():
    """Agent 会话里的"我会+中文动作短语"是意图指令，不是能力（精度优先回归）。"""
    for text in [
        "用户: 我会只给这个工程补一个 local.properties 指向它，不碰业务代码。",
        "我会用更长的构建窗口重跑，并在完成后读取实际安装包路径和哈希。",
        "用户: 我会先从工作区根目录重新检查实际差异和语法。",
    ]:
        assert _extract(text).items == [], f"intention instruction extracted: {text}"


def test_skill_rule_accepts_named_technology_after_wohui():
    """"我会<具名技术>"仍是有效能力表达。"""
    for text, expected in [
        ("我会 Python，熟悉 FastAPI 和 SQLAlchemy。", "Python"),
        ("我会Python3。", "Python3"),
    ]:
        items = _extract(text).items
        assert len(items) == 1, f"expected 1 skill, got {items}"
        assert items[0].type == MemoryType.SKILL
        assert expected in items[0].statement
