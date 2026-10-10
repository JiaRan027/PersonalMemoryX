from scripts.generate_review_gold_set import build_gold_set


def _memory(memory_id: str, memory_type: str, *, triage: dict, issues=None) -> dict:
    return {
        "id": memory_id,
        "statement": f"statement {memory_id}",
        "type": memory_type,
        "namespace": "general",
        "revision": 1,
        "review_triage": triage,
        "quality_issues": issues or [],
        "provenance": {"source_kind": "test"},
    }


def test_gold_set_includes_high_risk_and_external_disagreement() -> None:
    memories = [
        _memory(
            "high-risk",
            "preference",
            triage={
                "suggested_verdict": "needs_context",
                "effective_verdict": "needs_context",
                "requires_user_review": True,
                "overridden_by_user": False,
                "reason_codes": ["UNVERIFIED_AGENT_HIGH_IMPACT"],
            },
        ),
        _memory(
            "disagreement",
            "insight",
            triage={
                "suggested_verdict": "keep",
                "effective_verdict": "keep",
                "requires_user_review": False,
                "overridden_by_user": False,
                "reason_codes": ["GOVERNED_PROVISIONAL"],
            },
        ),
    ]
    result = build_gold_set(
        memories,
        assistance={
            "items": {
                "disagreement": {"recommendation": "rewrite"},
            }
        },
        decisions={},
        sample_per_type=0,
    )
    by_id = {item["id"]: item for item in result["items"]}
    assert by_id["high-risk"]["selection_reasons"] == ["HIGH_RISK_EXCEPTION"]
    assert by_id["disagreement"]["selection_reasons"] == ["SYSTEM_EXTERNAL_DISAGREEMENT"]


def test_gold_set_preserves_user_override_and_is_stable() -> None:
    memories = [
        _memory(
            f"memory-{index}",
            "event",
            triage={
                "suggested_verdict": "source_only",
                "effective_verdict": "keep" if index == 0 else "source_only",
                "requires_user_review": False,
                "overridden_by_user": index == 0,
                "reason_codes": ["USER_OVERRIDE"] if index == 0 else ["TRANSIENT_PROCESS"],
            },
        )
        for index in range(4)
    ]
    kwargs = {"assistance": {"items": {}}, "decisions": {}, "sample_per_type": 1}
    first = build_gold_set(memories, **kwargs)
    second = build_gold_set(list(reversed(memories)), **kwargs)
    assert [item["id"] for item in first["items"]] == [item["id"] for item in second["items"]]
    assert first["summary"]["user_override_count"] == 1
    assert first["summary"]["gold_set_size"] == 4
