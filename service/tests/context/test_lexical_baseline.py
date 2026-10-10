from __future__ import annotations

from scripts.evaluate_lexical_baseline import build_database, evaluate, load_fixture


def test_fixed_lexical_baseline_has_expected_receipt_and_boundaries(tmp_path) -> None:
    fixture = load_fixture()
    database = build_database(fixture, tmp_path / "data")
    try:
        result = evaluate(fixture, database)
    finally:
        database.dispose()

    assert result["fixture_version"] == "lexical-baseline/1"
    assert result["mode"] == "lexical"
    assert result["cache_class"] == "dynamic"
    assert result["case_count"] == 12
    assert result["known_case_recall"] == 1.0
    # The baseline intentionally records the current false-positive gap: a
    # broad unknown database question still receives lexical fallback items.
    assert result["unknown_safe_cases"] == 1
    assert result["unknown_case_count"] == 2
    assert result["delivery_receipts"] == [
        {
            "requested_mode": "lexical",
            "effective_mode": "lexical",
            "status": "ok",
            "cache_class": "dynamic",
        }
    ]
