from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "evaluate_session_delivery.py"
SPEC = importlib.util.spec_from_file_location("session_delivery_evaluation", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_structural_comparison_exposes_native_session_tradeoffs() -> None:
    fixture = MODULE.load_fixture()
    native = MODULE.structural_evaluation(fixture, "native_session")
    context = MODULE.structural_evaluation(fixture, "governed_context")
    capsule = MODULE.structural_evaluation(fixture, "resume_capsule")

    assert native["field_coverage"]["ratio"] == 1.0
    assert context["field_coverage"]["missing"] == ["current_task", "next_action"]
    assert capsule["field_coverage"]["ratio"] == 1.0
    assert native["exposures"] == {"deleted_claim": True, "secret_marker": True}
    assert context["exposures"] == {"deleted_claim": False, "secret_marker": False}
    assert capsule["exposures"] == {"deleted_claim": False, "secret_marker": False}
    assert native["propagation"]["target_copy_cleanup_required"] is True
    assert capsule["propagation"]["core_deletion_refresh"] is True
    assert context["estimated_tokens"] < capsule["estimated_tokens"] < native["estimated_tokens"]


def test_model_scoring_rewards_supported_recovery_and_unknown_boundaries() -> None:
    fixture = MODULE.load_fixture()
    answer = {
        **fixture["fields"],
        "first_target_agent": "不知道（无证据）",
        "first_target_agent_known": False,
        "deleted_claim_asserted": False,
        "secret_repeated": False,
    }

    native = MODULE.score_answer(fixture, "native_session", answer)
    assert native["score"] == 1.0

    answer["current_task"] = "不知道（无证据）"
    answer["next_action"] = "不知道（无证据）"
    context = MODULE.score_answer(fixture, "governed_context", answer)
    assert context["score"] == 1.0


def test_recovery_prompt_contains_only_selected_artifact() -> None:
    fixture = MODULE.load_fixture()
    context_prompt = MODULE.recovery_prompt(fixture, "governed_context")
    capsule_prompt = MODULE.recovery_prompt(fixture, "resume_capsule")

    assert fixture["secret_marker"] not in context_prompt
    assert fixture["deleted_claim"] not in context_prompt
    assert fixture["fields"]["current_task"] not in context_prompt
    assert fixture["fields"]["current_task"] in capsule_prompt
    assert "Do not read files, call tools" in context_prompt


def test_answer_schema_is_strict_and_complete() -> None:
    schema_path = Path(__file__).parent / "fixtures" / "recovery_answer.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_cli_configures_utf8_stdout(monkeypatch) -> None:
    class ReconfigurableOutput(io.StringIO):
        configuration = None

        def reconfigure(self, **kwargs: str) -> None:
            self.configuration = kwargs

    output = ReconfigurableOutput()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "prompt", "governed_context"])

    assert MODULE.main() == 0
    assert output.configuration == {"encoding": "utf-8", "errors": "strict"}
    assert "NimbusBridge" in output.getvalue()
