"""Evaluate a frozen review gold set without treating system triage as user truth."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

DELIVERABLE = {"keep", "project_experience"}
NON_DELIVERABLE = {"source_only", "test_artifact", "needs_context", "rewrite"}


def _has_evidence(item: dict[str, Any]) -> bool:
    provenance = item.get("provenance") or {}
    return any(
        provenance.get(key)
        for key in (
            "locator",
            "evidence_excerpt",
            "source_revision_id",
            "line_start",
            "evidence_start",
        )
    )


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    items = [item for item in payload.get("items", []) if isinstance(item, dict)]
    user_reviewed = [
        item
        for item in items
        if (item.get("review_triage") or {}).get("overridden_by_user")
    ]
    suggested = Counter(str(item.get("suggested_verdict") or "unknown") for item in items)
    effective = Counter(str(item.get("effective_verdict") or "unknown") for item in items)
    user_effective = Counter(
        str(item.get("effective_verdict") or "unknown") for item in user_reviewed
    )

    false_admission = sum(
        1
        for item in user_reviewed
        if item.get("suggested_verdict") in DELIVERABLE
        and item.get("effective_verdict") in NON_DELIVERABLE
    )
    false_rejection = sum(
        1
        for item in user_reviewed
        if item.get("suggested_verdict") in NON_DELIVERABLE
        and item.get("effective_verdict") in DELIVERABLE
    )
    evidence_count = sum(1 for item in items if _has_evidence(item))
    return {
        "schema_version": "personalmemory.review-gold-set-metrics.v1",
        "gold_set_schema_version": payload.get("schema_version"),
        "source": payload.get("source"),
        "summary": {
            "gold_set_size": len(items),
            "user_reviewed_count": len(user_reviewed),
            "system_suggested_verdicts": dict(sorted(suggested.items())),
            "effective_verdicts_snapshot": dict(sorted(effective.items())),
            "user_reviewed_effective_verdicts": dict(sorted(user_effective.items())),
            "useful_capture_user_reviewed": (
                sum(user_effective[verdict] for verdict in DELIVERABLE)
                / len(user_reviewed)
                if user_reviewed
                else None
            ),
            "false_admission_user_reviewed": false_admission,
            "false_rejection_user_reviewed": false_rejection,
            "test_pollution_user_reviewed": user_effective["test_artifact"],
            "evidence_presence_snapshot": (
                evidence_count / len(items) if items else None
            ),
            "type_accuracy": None,
            "scope_accuracy": None,
        },
        "interpretation_boundary": [
            "Only records with overridden_by_user=true are user-reviewed truth.",
            (
                "effective_verdicts_snapshot includes automatic system decisions "
                "and is not a human gold label."
            ),
            (
                "type_accuracy and scope_accuracy are unknown until expected "
                "type/scope labels are added."
            ),
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = evaluate(payload)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
