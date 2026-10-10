from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from personalmemory.domain.extraction import DeterministicExtractorAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "auto_capture_eval_set.jsonl"


def load_cases() -> list[dict[str, object]]:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]


def test_auto_capture_precision_and_assistant_boundary() -> None:
    extractor = DeterministicExtractorAdapter()
    true_positive = 0
    predicted = 0
    expected = 0
    assistant_only_predictions = 0

    for case in load_cases():
        user_text = str(case["user"])
        content = f"用户: {user_text}\n助手: {case['assistant']}"
        result = extractor.extract(
            content,
            source_revision_id=f"source:{case['id']}",
            namespace="projects/evaluation",
        )
        actual_types = Counter(item.type.value for item in result.items)
        expected_types = Counter(case["expected_types"])
        true_positive += sum((actual_types & expected_types).values())
        predicted += sum(actual_types.values())
        expected += sum(expected_types.values())
        assistant_start = content.index("助手:")
        assistant_only_predictions += sum(
            item.evidence_span[0] >= assistant_start for item in result.items
        )

    precision = true_positive / predicted
    recall = true_positive / expected
    assert precision >= 0.90
    assert recall >= 0.80
    assert assistant_only_predictions == 0
