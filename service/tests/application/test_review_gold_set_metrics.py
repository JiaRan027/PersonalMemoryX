from scripts.evaluate_review_gold_set import evaluate


def _item(suggested: str, effective: str, overridden: bool) -> dict:
    return {
        "suggested_verdict": suggested,
        "effective_verdict": effective,
        "review_triage": {"overridden_by_user": overridden},
        "provenance": {"evidence_excerpt": "user evidence"},
    }


def test_metrics_only_call_user_overrides_gold_truth() -> None:
    result = evaluate(
        {
            "schema_version": "personalmemory.review-gold-set.v1",
            "items": [
                _item("needs_context", "keep", True),
                _item("needs_context", "test_artifact", True),
                _item("source_only", "source_only", False),
            ],
        }
    )
    summary = result["summary"]
    assert summary["user_reviewed_count"] == 2
    assert summary["useful_capture_user_reviewed"] == 0.5
    assert summary["false_rejection_user_reviewed"] == 1
    assert summary["false_admission_user_reviewed"] == 0
    assert summary["test_pollution_user_reviewed"] == 1
    assert summary["type_accuracy"] is None
    assert summary["scope_accuracy"] is None
