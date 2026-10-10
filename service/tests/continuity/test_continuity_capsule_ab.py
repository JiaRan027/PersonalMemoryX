from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "accept_continuity_capsule_ab.py"
SPEC = importlib.util.spec_from_file_location("continuity_capsule_ab", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _fixture() -> dict[str, Any]:
    return MODULE.load_fixture()


def _good_answer(fixture: dict[str, Any], evidence_refs: dict[str, str]) -> dict[str, Any]:
    answers = []
    for question in fixture["questions"]:
        if question["id"] == "unknown_boundary":
            text = "unknown (no governed evidence)"
            refs: list[str] = []
            confidence = "low"
        else:
            text = "; ".join(question["expected_terms"])
            refs = [evidence_refs[key] for key in question["evidence_keys"]]
            confidence = "medium"
        answers.append(
            {
                "question_id": question["id"],
                "known": question["expected_known"],
                "answer": text,
                "confidence": confidence,
                "evidence_refs": refs,
            }
        )
    return {"answers": answers}


def _placeholder_refs(fixture: dict[str, Any]) -> dict[str, str]:
    keys = {
        key
        for question in fixture["questions"]
        for key in question["evidence_keys"]
    }
    return {key: f"memory:{key}:revision:1" for key in keys}


def test_product_capsule_is_current_and_synthetic(tmp_path: Path) -> None:
    fixture = _fixture()
    product = MODULE.build_product_capsule(tmp_path / "data", fixture)
    capsule = product["capsule"]

    assert product["validation"]["valid"] is True
    assert product["secret_rejection"] == {
        "status_code": 400,
        "code": "SECRET_DETECTED",
    }
    assert len(capsule["memory_references"]) == 7
    assert fixture["deleted_claim"] not in product["delivery_text"]
    assert fixture["synthetic_secret_marker"] not in product["delivery_text"]
    assert product["lifecycle"]["stale_validation"]["stale"] is True
    assert product["lifecycle"]["rebuilt_deleted_claim_delivered"] is False
    assert product["lifecycle"]["rebuilt_old_revision_delivered"] is False
    assert product["lifecycle"]["initial_capsule_id"] != capsule["capsule_id"]
    assert capsule["memory_references"]
    assert all(reference["revision"] >= 1 for reference in capsule["memory_references"])


def test_neutral_questions_are_identical_and_no_capsule_prompt_has_no_answers() -> None:
    fixture = _fixture()
    no_capsule = MODULE.recovery_prompt(fixture)
    capsule = MODULE.recovery_prompt(fixture, "synthetic artifact text")

    assert MODULE.render_questions(fixture) in no_capsule
    assert MODULE.render_questions(fixture) in capsule
    no_questions = no_capsule.split("Questions:\n", 1)[1].split("\n\n", 1)[0]
    capsule_questions = capsule.split("Questions:\n", 1)[1].split("\n\n", 1)[0]
    assert no_questions == capsule_questions
    for record in fixture["records"]:
        assert record["statement"] not in no_capsule
        assert record.get("reason", "") not in no_capsule
    assert fixture["checkpoint"]["current_task"] not in no_capsule
    assert "memory" not in no_capsule.casefold()
    assert "mcp" not in no_capsule.casefold()
    assert "query" not in no_capsule.casefold()
    assert "synthetic artifact text" in capsule


def test_scoring_rewards_evidence_and_penalizes_high_confidence_hallucinations(
    tmp_path: Path,
) -> None:
    fixture = _fixture()
    product = MODULE.build_product_capsule(tmp_path / "data", fixture)
    good = _good_answer(fixture, product["evidence_refs"])
    good_score = MODULE.score_answer(
        fixture,
        good,
        arm="capsule",
        available_refs=product["evidence_refs"],
    )

    bad = {
        "answers": [
            {
                "question_id": question["id"],
                "known": True,
                "answer": "I know this confidently, even without supporting context.",
                "confidence": "high",
                "evidence_refs": ["invented-reference"],
            }
            for question in fixture["questions"]
        ]
    }
    bad_score = MODULE.score_answer(
        fixture, bad, arm="no_capsule", available_refs=()
    )
    safe_baseline = {
        "answers": [
            {
                "question_id": question["id"],
                "known": False,
                "answer": "unknown (no governed evidence)",
                "confidence": "low",
                "evidence_refs": [],
            }
            for question in fixture["questions"]
        ]
    }
    safe_baseline_score = MODULE.score_answer(
        fixture, safe_baseline, arm="no_capsule", available_refs=()
    )

    assert good_score["layer_correctness"]["passed"] == 6
    assert good_score["evidence_support_rate"] == 1.0
    assert good_score["high_confidence_error_count"] == 0
    assert bad_score["high_confidence_error_count"] == 6
    assert safe_baseline_score["evidence_support_rate"] is None
    assert safe_baseline_score["high_confidence_error_count"] == 0
    assert good_score["score"] > bad_score["score"]
    comparison = MODULE.score_ab(
        fixture,
        no_capsule=bad,
        capsule=good,
        available_refs=product["evidence_refs"],
    )
    assert comparison["relative_quality_improvement"]["score_delta"] > 0


def test_scoring_requires_current_revision_relevant_evidence_and_safe_unknowns() -> None:
    fixture = _fixture()
    refs = _placeholder_refs(fixture)
    answer = _good_answer(fixture, refs)
    current_ref = refs["verification_boundary"]
    answer["answers"][4]["evidence_refs"] = [current_ref.replace("revision:1", "revision:0")]
    answer["answers"][3].update(
        {
            "answer": "unknown, perhaps Acme Cloud",
            "known": False,
            "confidence": "low",
        }
    )

    score = MODULE.score_answer(
        fixture,
        answer,
        arm="capsule",
        available_refs=refs,
    )

    assert score["question_results"]["change"]["evidence_backed"] is False
    assert score["question_results"]["unknown_boundary"]["answer_correct"] is False
    assert score["layer_correctness"]["passed"] == 4
    assert score["score"] < 1.0


def test_scoring_accepts_clear_paraphrases_and_high_confidence_unknown() -> None:
    fixture = _fixture()
    refs = _placeholder_refs(fixture)
    answer = _good_answer(fixture, refs)
    answer["answers"][0]["answer"] = (
        "LumenRail is a local-first provenance ledger. The Core remains "
        "platform-neutral and does not write to Agent private sessions."
    )
    answer["answers"][1]["answer"] = (
        "Use an append-only evidence journal with thin source translators "
        "because independently evolving providers must not contaminate the Core."
    )
    answer["answers"][2]["answer"] = (
        "Repeated provider-specific release failures required a durable local "
        "ledger; use concise, evidence-backed checkpoints and explicit unknowns."
    )
    answer["answers"][3]["confidence"] = "high"
    answer["answers"][4]["answer"] = (
        "Validation uses isolated fixtures before any remote write; the earlier "
        "remote-provider validation claim no longer remains current because the "
        "revised boundary supersedes it."
    )

    score = MODULE.score_answer(
        fixture,
        answer,
        arm="capsule",
        available_refs=refs,
    )

    assert score["layer_correctness"]["passed"] == 6
    assert score["unknown_safety"]["unknown_is_explicit"] is True
    assert score["high_confidence_error_count"] == 0
    assert MODULE._is_unknown(
        "The authorized context does not establish which provider was selected."
    )


def test_strict_answer_shape_rejects_extra_fields_and_wrong_order() -> None:
    fixture = _fixture()
    answer = _good_answer(fixture, _placeholder_refs(fixture))
    assert MODULE.validate_answer(answer, fixture) == []

    answer["answers"][0]["extra"] = "forbidden"
    assert MODULE.validate_answer(answer, fixture)

    wrong_order = _good_answer(fixture, _placeholder_refs(fixture))
    wrong_order["answers"][0], wrong_order["answers"][1] = (
        wrong_order["answers"][1],
        wrong_order["answers"][0],
    )
    assert any(
        "fixed question order" in issue
        for issue in MODULE.validate_answer(wrong_order, fixture)
    )


def test_codex_arm_uses_ephemeral_read_only_invocation_without_real_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture()
    captured: list[tuple[list[str], str]] = []
    answer = _good_answer(fixture, _placeholder_refs(fixture))

    def fake_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        captured.append((command, kwargs["input"]))
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(json.dumps(answer), encoding="utf-8")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {"type": "turn.completed", "usage": {"input_tokens": 8, "output_tokens": 4}}
            ),
            stderr="",
        )

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)
    first = MODULE.run_codex_arm(
        "codex.cmd",
        fixture=fixture,
        arm="no_capsule",
        delivery_text=None,
        cwd=tmp_path,
        answer_path=tmp_path / "a.json",
    )
    second = MODULE.run_codex_arm(
        "codex.cmd",
        fixture=fixture,
        arm="capsule",
        delivery_text="artifact",
        cwd=tmp_path,
        answer_path=tmp_path / "b.json",
    )

    assert first["usage"] == {"input_tokens": 8, "output_tokens": 4}
    assert second["usage"] == first["usage"]
    assert len(captured) == 2
    for command, _prompt in captured:
        assert command[1:4] == ["exec", "--ephemeral", "--skip-git-repo-check"]
        assert "--sandbox" in command
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert "--output-schema" not in command
        assert "--tools" not in command
    assert MODULE.render_questions(fixture) in captured[0][1]
    assert MODULE.render_questions(fixture) in captured[1][1]


def test_event_parser_detects_nested_tool_activity() -> None:
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "command_execution", "command": "Get-ChildItem"},
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 8, "output_tokens": 4},
                }
            ),
        ]
    )

    event_types, usage, tool_activity_types = MODULE.parse_events(stdout)

    assert event_types == ["item.completed", "turn.completed"]
    assert usage == {"input_tokens": 8, "output_tokens": 4}
    assert tool_activity_types == ["command_execution"]
