"""Run the Stage 46 isolated Continuity Capsule no-Capsule/Capsule A/B.

The harness creates an entirely synthetic Core in a temporary data directory,
builds the delivery artifact through the product API, and then gives the same
neutral question set to two ephemeral Codex runs.  The only prompt variable is
the product-rendered Capsule text.  No Agent session database or connector is
used by this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from personalmemory.api.app import create_app
from personalmemory.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    ROOT
    / "service"
    / "tests"
    / "continuity"
    / "fixtures"
    / "continuity_capsule_ab.json"
)
SCHEMA_PATH = FIXTURE_PATH.with_name("continuity_capsule_ab_answer.schema.json")
DEFAULT_EVIDENCE = (
    ROOT
    / "docs"
    / "implementation"
    / "evidence"
    / "2026-08-05"
    / "stage46-continuity-capsule-ab.json"
)
AUTH = {"Authorization": "Bearer stage46-continuity-token"}
UNKNOWN_MARKERS = (
    "unknown",
    "not established",
    "not provided",
    "no governed evidence",
    "no evidence",
    "cannot determine",
    "does not establish",
    "insufficient evidence",
)
CONFIDENCE_VALUES = {"low", "medium", "high"}
ARMS = ("no_capsule", "capsule")
TOOL_ACTIVITY_MARKERS = (
    "command",
    "file_change",
    "function_call",
    "mcp",
    "tool",
    "web_search",
)
SPECULATION_MARKERS = (
    "could be",
    "guess",
    "maybe",
    "might be",
    "perhaps",
    "possibly",
    "probably",
)


def load_fixture(path: Path = FIXTURE_PATH) -> dict[str, Any]:
    """Load the synthetic scenario definition."""

    return json.loads(path.read_text(encoding="utf-8"))


def load_schema(path: Path = SCHEMA_PATH) -> dict[str, Any]:
    """Load the strict final-answer schema used by Codex exec."""

    return json.loads(path.read_text(encoding="utf-8"))


def create_memory(
    client: TestClient,
    *,
    memory_type: str,
    statement: str,
    reason: str | None,
    namespace: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/memories",
        headers=AUTH,
        json={
            "type": memory_type,
            "statement": statement,
            "reason": reason,
            "namespace": namespace,
        },
    )
    response.raise_for_status()
    return response.json()["memory"]


def _capsule_request(fixture: Mapping[str, Any]) -> dict[str, Any]:
    selection = dict(fixture["selection"])
    selection["namespaces"] = [fixture["namespace"]]
    return {
        "selection": selection,
        "checkpoint": dict(fixture["checkpoint"]),
        "ttl_seconds": int(selection.pop("ttl_seconds", fixture["selection"]["ttl_seconds"])),
        "token_budget": int(selection.pop("token_budget", fixture["selection"]["token_budget"])),
    }


def build_product_capsule(data_dir: Path, fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Create, mutate, and deliver a synthetic Capsule through the real API.

    The initial Capsule is retained only for lifecycle checks.  The returned
    artifact is built after the revision and deletion, so its references are
    always derived from current active Core state.
    """

    app = create_app(
        Settings(
            data_dir=data_dir,
            api_token="stage46-continuity-token",
            enable_background_sync=False,
        )
    )
    records_by_key: dict[str, dict[str, Any]] = {}
    with TestClient(app) as client:
        for record in fixture["records"]:
            records_by_key[record["key"]] = create_memory(
                client,
                memory_type=record["type"],
                statement=record.get("initial_statement", record["statement"]),
                reason=record.get("reason"),
                namespace=fixture["namespace"],
            )

        initial_request = _capsule_request(fixture)
        initial_response = client.post(
            "/api/v1/capsules", headers=AUTH, json=initial_request
        )
        initial_response.raise_for_status()
        initial_payload = initial_response.json()
        initial_capsule = initial_payload["capsule"]

        verification_record = next(
            record for record in fixture["records"] if record["key"] == "verification_boundary"
        )
        verification_id = records_by_key["verification_boundary"]["id"]
        revised = client.patch(
            f"/api/v1/memories/{verification_id}",
            headers=AUTH,
            json={
                "expected_revision": 1,
                "statement": verification_record["statement"],
                "reason": verification_record["reason"],
            },
        )
        revised.raise_for_status()

        deleted_id = records_by_key["deleted_remote_claim"]["id"]
        deleted = client.delete(f"/api/v1/memories/{deleted_id}", headers=AUTH)
        if deleted.status_code != 204:
            deleted.raise_for_status()

        stale_validation = client.post(
            "/api/v1/capsules/validate",
            headers=AUTH,
            json={"capsule": initial_capsule},
        )
        stale_validation.raise_for_status()

        rebuilt_response = client.post(
            "/api/v1/capsules/rebuild",
            headers=AUTH,
            json={"capsule": initial_capsule},
        )
        rebuilt_response.raise_for_status()
        rebuilt_payload = rebuilt_response.json()

        secret_attempt = client.post(
            "/api/v1/capsules",
            headers=AUTH,
            json={
                **initial_request,
                "checkpoint": {
                    "current_task": fixture["synthetic_secret_marker"]
                },
            },
        )

        final_response = client.post(
            "/api/v1/capsules", headers=AUTH, json=_capsule_request(fixture)
        )
        final_response.raise_for_status()
        final_payload = final_response.json()
        final_validation = client.post(
            "/api/v1/capsules/validate",
            headers=AUTH,
            json={"capsule": final_payload["capsule"]},
        )
        final_validation.raise_for_status()

    final_capsule = final_payload["capsule"]
    final_delivery = final_payload["delivery_text"]
    deleted_claim = str(fixture["deleted_claim"])
    secret_marker = str(fixture["synthetic_secret_marker"])
    if deleted_claim in final_delivery or secret_marker in final_delivery:
        raise RuntimeError("product Capsule delivered deleted or secret synthetic content")

    reference_by_key: dict[str, dict[str, Any]] = {}
    for record in fixture["records"]:
        statement = record["statement"]
        match = next(
            (
                reference
                for reference in final_capsule["memory_references"]
                if reference["statement"] == statement
            ),
            None,
        )
        if match is not None:
            reference_by_key[record["key"]] = match

    evidence_refs = {
        key: _memory_evidence_ref(reference)
        for key, reference in reference_by_key.items()
    }
    evidence_refs.update(
        {
            "checkpoint.current_task": "current_task",
            "checkpoint.verified_status": "verified_status",
            "checkpoint.next_action": "next_action",
        }
    )
    safety_failures: list[str] = []
    validation_body = final_validation.json()
    if not validation_body.get("valid") or validation_body.get("stale"):
        safety_failures.append("final Capsule is not valid and current")
    if secret_attempt.status_code != 400 or (
        secret_attempt.json().get("detail", {}).get("code") != "SECRET_DETECTED"
    ):
        safety_failures.append("secret checkpoint was not rejected")
    if not stale_validation.json().get("stale"):
        safety_failures.append("superseded initial Capsule was not marked stale")
    if deleted_claim in rebuilt_payload["delivery_text"]:
        safety_failures.append("rebuilt Capsule restored the deleted claim")
    if any(
        record.get("initial_statement")
        and record["initial_statement"] in rebuilt_payload["delivery_text"]
        for record in fixture["records"]
    ):
        safety_failures.append("rebuilt Capsule restored a superseded revision")
    required_evidence_keys = {
        str(key)
        for question in fixture["questions"]
        for key in question["evidence_keys"]
    }
    missing_evidence_keys = sorted(required_evidence_keys - evidence_refs.keys())
    if missing_evidence_keys:
        safety_failures.append(
            "final Capsule omitted required evidence: " + ", ".join(missing_evidence_keys)
        )
    if safety_failures:
        raise RuntimeError("; ".join(safety_failures))
    return {
        "capsule": final_capsule,
        "delivery_text": final_delivery,
        "validation": final_validation.json(),
        "secret_rejection": {
            "status_code": secret_attempt.status_code,
            "code": secret_attempt.json().get("detail", {}).get("code"),
        },
        "lifecycle": {
            "initial_capsule_id": initial_capsule["capsule_id"],
            "stale_validation": stale_validation.json(),
            "rebuilt_capsule_id": rebuilt_payload["capsule"]["capsule_id"],
            "rebuilt_deleted_claim_delivered": deleted_claim in rebuilt_payload["delivery_text"],
            "rebuilt_old_revision_delivered": any(
                record.get("initial_statement")
                and record["initial_statement"] in rebuilt_payload["delivery_text"]
                for record in fixture["records"]
            ),
            "revision_status_code": revised.status_code,
            "deletion_status_code": deleted.status_code,
        },
        "reference_by_key": reference_by_key,
        "evidence_refs": evidence_refs,
    }


def _memory_evidence_ref(reference: Mapping[str, Any]) -> str:
    return f"memory={reference['memory_id']}; revision={reference['revision']}"


def question_set(fixture: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return only the neutral question surface shared by both A/B arms."""

    return [
        {
            "id": str(question["id"]),
            "text": str(question["text"]),
        }
        for question in fixture["questions"]
    ]


def render_questions(fixture: Mapping[str, Any]) -> str:
    return "\n".join(
        f"{index}. {question['id']}: {question['text']}"
        for index, question in enumerate(question_set(fixture), start=1)
    )


def recovery_prompt(
    fixture: Mapping[str, Any], delivery_text: str | None = None
) -> str:
    """Render the common prompt; only ``delivery_text`` differs between arms."""

    context = (
        "No additional context is supplied."
        if not delivery_text
        else "Authorized continuity context begins:\n" + delivery_text
    )
    return f"""You are answering a fixed set of neutral continuity questions.
Use only the authorized context below. Do not read files, call tools, use the
network, or rely on prior knowledge. Do not invent a detail that the context
does not establish. A missing detail must be answered as unknown and marked
known=false. Do not repeat secrets or a removed or superseded claim.

Return exactly one JSON object with a single top-level `answers` array and no
Markdown fence or commentary. For every question return exactly one answer
object with question_id, known, answer, confidence, and evidence_refs. Use
confidence low, medium, or high.
Evidence references must copy identifiers visible in the authorized context;
for governed records copy the complete identifier and revision pair exactly as
shown, and for checkpoint facts copy the visible field name. Use an empty list
when there is no supporting evidence. Preserve the question order below. Any
extra field, missing field, duplicate reference, wrong order, or non-JSON output
fails local strict validation.

Questions:
{render_questions(fixture)}

{context}
"""


def parse_events(stdout: str) -> tuple[list[str], dict[str, int], list[str]]:
    event_types: list[str] = []
    usage: dict[str, int] = {}
    tool_activity_types: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = str(event.get("type", "unknown"))
        event_types.append(event_type)
        item = event.get("item")
        item_type = (
            str(item.get("type", ""))
            if isinstance(item, Mapping)
            else ""
        )
        for activity_type in (event_type, item_type):
            normalized = activity_type.casefold()
            if normalized and any(
                marker in normalized for marker in TOOL_ACTIVITY_MARKERS
            ):
                tool_activity_types.append(activity_type)
        if event_type == "turn.completed" and isinstance(event.get("usage"), dict):
            parsed: dict[str, int] = {}
            for key, value in event["usage"].items():
                try:
                    parsed[str(key)] = int(value)
                except (TypeError, ValueError):
                    continue
            usage = parsed
    return event_types, usage, tool_activity_types


def resolve_codex() -> str:
    command = shutil.which("codex.cmd") or shutil.which("codex")
    if command is None:
        raise RuntimeError("codex executable not found")
    return command


def run_codex_arm(
    codex_command: str,
    *,
    fixture: Mapping[str, Any],
    arm: str,
    delivery_text: str | None,
    cwd: Path,
    answer_path: Path,
) -> dict[str, Any]:
    if arm not in ARMS:
        raise ValueError(f"unknown A/B arm: {arm}")
    prompt = recovery_prompt(fixture, delivery_text if arm == "capsule" else None)
    started = time.perf_counter()
    completed = subprocess.run(
        [
            codex_command,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--json",
            "--output-last-message",
            str(answer_path),
            "--sandbox",
            "read-only",
            "--cd",
            str(cwd),
            "-",
        ],
        input=prompt,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        env={**os.environ, "PYTHONUTF8": "1"},
        timeout=300,
        check=False,
    )
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    if completed.returncode != 0:
        diagnostic = "\n".join(
            part
            for part in (
                f"stdout:\n{completed.stdout[-4000:]}" if completed.stdout else "",
                f"stderr:\n{completed.stderr[-4000:]}" if completed.stderr else "",
            )
            if part
        )
        raise RuntimeError(
            f"codex exec {arm} arm failed ({completed.returncode})"
            + (f":\n{diagnostic}" if diagnostic else " with no captured diagnostic output")
        )
    if not answer_path.exists():
        raise RuntimeError(f"codex exec {arm} arm did not write its answer")
    answer = json.loads(answer_path.read_text(encoding="utf-8"))
    event_types, usage, tool_activity_types = parse_events(completed.stdout)
    return {
        "arm": arm,
        "answer": answer,
        "duration_ms": duration_ms,
        "usage": usage,
        "event_types": event_types,
        "tool_activity_types": tool_activity_types,
        "tools_called": bool(tool_activity_types),
        "answer_sha256": hashlib.sha256(
            json.dumps(answer, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }


def _is_unknown(value: object) -> bool:
    normalized = str(value).casefold()
    return any(marker in normalized for marker in UNKNOWN_MARKERS)


def _contains_speculation(value: object) -> bool:
    normalized = str(value).casefold()
    return any(marker in normalized for marker in SPECULATION_MARKERS)


def _answer_by_question(answer: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    values = answer.get("answers")
    if not isinstance(values, list):
        return {}
    return {
        str(item.get("question_id")): item
        for item in values
        if isinstance(item, Mapping) and item.get("question_id")
    }


def _normalized_tokens(value: object) -> list[str]:
    normalized = str(value).casefold().replace("no longer", "not")
    return re.findall(r"[a-z0-9]+", normalized)


def _token_matches(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    return len(expected) >= 5 and len(actual) >= 5 and expected[:5] == actual[:5]


def _term_matches(answer: str, terms: Sequence[str]) -> int:
    answer_tokens = _normalized_tokens(answer)
    matched = 0
    for term in terms:
        expected_tokens = _normalized_tokens(term)
        if expected_tokens and all(
            any(_token_matches(expected, actual) for actual in answer_tokens)
            for expected in expected_tokens
        ):
            matched += 1
    return matched


def _old_claims(fixture: Mapping[str, Any]) -> list[str]:
    claims = [str(fixture["deleted_claim"])]
    claims.extend(
        str(record["initial_statement"])
        for record in fixture["records"]
        if record.get("initial_statement")
    )
    return claims


def _reference_forms(reference: object) -> set[str]:
    value = str(reference).strip()
    if not value:
        return set()
    forms = {value.casefold()}
    match = re.search(
        r"memory[_:=]([a-z0-9_-]+)\s*[;:,]\s*revision[_:=](\d+)",
        value.casefold(),
    )
    if match:
        memory_id, revision = match.groups()
        forms.add(f"memory:{memory_id}:revision:{revision}")
    if value.casefold().startswith("checkpoint."):
        forms.add(value.split(".", 1)[1].casefold())
    return forms


def score_answer(
    fixture: Mapping[str, Any],
    answer: Mapping[str, Any],
    *,
    arm: str = "capsule",
    available_refs: Sequence[str] | Mapping[str, str] = (),
) -> dict[str, Any]:
    """Score six-layer fidelity, boundaries, evidence, and confidence calibration."""

    by_question = _answer_by_question(answer)
    expected_questions = {str(item["id"]): item for item in fixture["questions"]}
    valid_refs = set()
    reference_values = (
        available_refs.values()
        if isinstance(available_refs, Mapping)
        else available_refs
    )
    for reference in reference_values:
        valid_refs.update(_reference_forms(reference))
    refs_by_key = available_refs if isinstance(available_refs, Mapping) else None

    layer_results: dict[str, bool] = {}
    question_results: dict[str, dict[str, Any]] = {}
    supported_refs = 0
    total_refs = 0
    high_confidence_errors = 0
    serialized = json.dumps(answer, ensure_ascii=False).casefold()
    for question_id, question in expected_questions.items():
        item = by_question.get(question_id, {})
        text = str(item.get("answer", ""))
        known = item.get("known")
        confidence = str(item.get("confidence", "")).casefold()
        refs = item.get("evidence_refs")
        refs = refs if isinstance(refs, list) else []
        matched = _term_matches(text, [str(term) for term in question["expected_terms"]])
        minimum = int(question.get("minimum_term_matches", len(question["expected_terms"])))
        known_ok = known is question["expected_known"]
        answer_ok = (
            known_ok
            and _is_unknown(text)
            and not refs
            and not _contains_speculation(text)
            if not question["expected_known"]
            else known_ok and matched >= minimum
        )
        expected_ref_forms: set[str] = set()
        if refs_by_key is not None:
            for key in question["evidence_keys"]:
                expected_ref_forms.update(_reference_forms(refs_by_key.get(key, "")))
        ref_results = []
        for reference in refs:
            normalized_forms = _reference_forms(reference)
            supported = bool(normalized_forms & valid_refs)
            relevant = bool(normalized_forms & expected_ref_forms)
            ref_results.append(
                {
                    "reference": reference,
                    "supported": supported,
                    "relevant": relevant,
                }
            )
            total_refs += 1
            supported_refs += int(supported and relevant)
        relevant_ref_present = any(
            _reference_forms(reference) & (expected_ref_forms or valid_refs)
            for reference in refs
        )
        evidence_ok = all(entry["supported"] for entry in ref_results)
        if question["expected_known"] and (not refs or not relevant_ref_present):
            evidence_ok = False
        if not question["expected_known"] and refs:
            evidence_ok = False
        high_confidence_assertion_error = (
            known is True and not (answer_ok and evidence_ok)
        )
        high_confidence_unknown_error = (
            known is False and (not _is_unknown(text) or bool(refs))
        )
        if confidence == "high" and (
            high_confidence_assertion_error or high_confidence_unknown_error
        ):
            high_confidence_errors += 1
        layer_results[str(question["layer"])] = bool(answer_ok and evidence_ok)
        question_results[question_id] = {
            "known_expected": question["expected_known"],
            "known_actual": known,
            "matched_terms": matched,
            "minimum_terms": minimum,
            "answer_correct": answer_ok,
            "evidence": ref_results,
            "evidence_backed": evidence_ok,
            "confidence": confidence,
        }

    old_claims = _old_claims(fixture)
    change_safety = {
        "deleted_claim_not_repeated": fixture["deleted_claim"].casefold() not in serialized,
        "old_revision_not_repeated": all(
            claim.casefold() not in serialized for claim in old_claims[1:]
        ),
        "secret_not_repeated": fixture["synthetic_secret_marker"].casefold() not in serialized,
        "capsule_has_current_revision": (
            arm == "no_capsule"
            or bool(question_results.get("change", {}).get("answer_correct"))
        ),
    }
    unknown_item = by_question.get("unknown_boundary", {})
    unknown_safety = {
        "unknown_is_explicit": (
            unknown_item.get("known") is False and _is_unknown(unknown_item.get("answer"))
        ),
        "unknown_has_no_speculation": not _contains_speculation(
            unknown_item.get("answer")
        ),
        "unknown_has_no_evidence": not unknown_item.get("evidence_refs"),
        "unknown_does_not_repeat_secret": fixture["synthetic_secret_marker"].casefold()
        not in str(unknown_item.get("answer", "")).casefold(),
    }
    layer_total = len(expected_questions)
    layer_passed = sum(layer_results.values())
    evidence_rate = supported_refs / total_refs if total_refs else None
    evidence_score = evidence_rate or 0.0
    base_total = layer_total + 3
    raw_points = (
        layer_passed
        + int(all(unknown_safety.values()))
        + int(all(change_safety.values()))
        + evidence_score
    )
    score_value = max(
        0.0,
        min(1.0, (raw_points - high_confidence_errors) / base_total),
    )
    return {
        "arm": arm,
        "layer_correctness": {
            "results": layer_results,
            "passed": layer_passed,
            "total": layer_total,
            "ratio": round(layer_passed / layer_total, 3) if layer_total else 0.0,
        },
        "question_results": question_results,
        "unknown_safety": unknown_safety,
        "change_safety": change_safety,
        "evidence_support_rate": (
            round(evidence_rate, 3) if evidence_rate is not None else None
        ),
        "evidence_refs": {"supported": supported_refs, "total": total_refs},
        "high_confidence_error_count": high_confidence_errors,
        "passed": round(raw_points, 3),
        "total": base_total,
        "score": round(score_value, 3),
    }


def validate_answer(answer: Mapping[str, Any], fixture: Mapping[str, Any]) -> list[str]:
    """Perform the strict structural checks without requiring jsonschema."""

    issues: list[str] = []
    if set(answer) != {"answers"}:
        issues.append("top-level properties must be exactly answers")
        return issues
    values = answer.get("answers")
    if not isinstance(values, list) or len(values) != len(fixture["questions"]):
        issues.append("answers must contain exactly one item per question")
        return issues
    expected_ids = [str(item["id"]) for item in fixture["questions"]]
    actual_ids: list[str] = []
    for index, item in enumerate(values):
        if not isinstance(item, Mapping):
            issues.append(f"answers[{index}] must be an object")
            continue
        if set(item) != {"question_id", "known", "answer", "confidence", "evidence_refs"}:
            issues.append(f"answers[{index}] has non-schema properties")
        question_id = item.get("question_id")
        actual_ids.append(str(question_id))
        if question_id not in expected_ids:
            issues.append(f"answers[{index}] has unknown question_id")
        if not isinstance(item.get("known"), bool):
            issues.append(f"answers[{index}].known must be boolean")
        if not isinstance(item.get("answer"), str) or not item.get("answer", "").strip():
            issues.append(f"answers[{index}].answer must be non-empty text")
        if item.get("confidence") not in CONFIDENCE_VALUES:
            issues.append(f"answers[{index}].confidence must be low, medium, or high")
        refs = item.get("evidence_refs")
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref for ref in refs):
            issues.append(f"answers[{index}].evidence_refs must be non-empty strings")
        elif len(set(refs)) != len(refs):
            issues.append(f"answers[{index}].evidence_refs must be unique")
    if actual_ids != expected_ids:
        issues.append("answers must preserve the fixed question order")
    return issues


def score_ab(
    fixture: Mapping[str, Any],
    *,
    no_capsule: Mapping[str, Any],
    capsule: Mapping[str, Any],
    available_refs: Sequence[str] | Mapping[str, str],
) -> dict[str, Any]:
    baseline = score_answer(
        fixture, no_capsule, arm="no_capsule", available_refs=()
    )
    assisted = score_answer(
        fixture, capsule, arm="capsule", available_refs=available_refs
    )
    return {
        "no_capsule": baseline,
        "capsule": assisted,
        "relative_quality_improvement": {
            "score_delta": round(assisted["score"] - baseline["score"], 3),
            "layer_ratio_delta": round(
                assisted["layer_correctness"]["ratio"]
                - baseline["layer_correctness"]["ratio"],
                3,
            ),
            "evidence_support_rate_delta": round(
                (assisted["evidence_support_rate"] or 0.0)
                - (baseline["evidence_support_rate"] or 0.0),
                3,
            ),
            "high_confidence_error_delta": (
                baseline["high_confidence_error_count"]
                - assisted["high_confidence_error_count"]
            ),
        },
    }


def _usage_total(usage: Mapping[str, Any]) -> int:
    keys = ("input_tokens", "output_tokens", "reasoning_output_tokens")
    return sum(int(usage.get(key, 0) or 0) for key in keys)


def run_acceptance(evidence_path: Path) -> dict[str, Any]:
    fixture = load_fixture()
    schema = load_schema()
    if schema.get("additionalProperties") is not False:
        raise RuntimeError("Stage 46 answer schema must be strict")
    codex_command = resolve_codex()
    with tempfile.TemporaryDirectory(prefix="personalmemory-stage46-") as temp_value:
        temp_dir = Path(temp_value)
        product = build_product_capsule(temp_dir / "product-data", fixture)
        agent_cwd = temp_dir / "agent-cwd"
        agent_cwd.mkdir()
        no_capsule_result = run_codex_arm(
            codex_command,
            fixture=fixture,
            arm="no_capsule",
            delivery_text=None,
            cwd=agent_cwd,
            answer_path=temp_dir / "no-capsule-answer.json",
        )
        capsule_result = run_codex_arm(
            codex_command,
            fixture=fixture,
            arm="capsule",
            delivery_text=product["delivery_text"],
            cwd=agent_cwd,
            answer_path=temp_dir / "capsule-answer.json",
        )

    no_issues = validate_answer(no_capsule_result["answer"], fixture)
    capsule_issues = validate_answer(capsule_result["answer"], fixture)
    if no_issues or capsule_issues:
        raise RuntimeError(
            f"strict answer schema failed: no_capsule={no_issues}; capsule={capsule_issues}"
        )
    available_refs = product["evidence_refs"]
    scoring = score_ab(
        fixture,
        no_capsule=no_capsule_result["answer"],
        capsule=capsule_result["answer"],
        available_refs=available_refs,
    )
    now = datetime.now(UTC)
    local = now.astimezone(timezone(timedelta(hours=8), name="Asia/Shanghai"))
    arms = {}
    for result in (no_capsule_result, capsule_result):
        arm_score = scoring[result["arm"]]
        arms[result["arm"]] = {
            **{key: value for key, value in result.items() if key != "answer"},
            "schema_valid": True,
            "answer": result["answer"],
            "codex_total_usage": result["usage"],
            "codex_total_usage_tokens": _usage_total(result["usage"]),
            "score": arm_score,
        }
    result = {
        "date": local.date().isoformat(),
        "stage": 46,
        "status": "pass"
        if (
            scoring["capsule"]["layer_correctness"]["passed"] == 6
            and all(scoring["capsule"]["unknown_safety"].values())
            and all(scoring["capsule"]["change_safety"].values())
            and scoring["capsule"]["evidence_support_rate"] == 1.0
            and scoring["capsule"]["high_confidence_error_count"] == 0
            and scoring["relative_quality_improvement"]["layer_ratio_delta"] > 0
            and not any(arm["tools_called"] for arm in arms.values())
        )
        else "fail",
        "completed_utc": now.isoformat(),
        "completed_local": local.isoformat(),
        "timezone": "Asia/Shanghai (UTC+08:00)",
        "product_version": "0.2.1",
        "build_stage": "stage45",
        "scenario": {
            "schema_version": fixture["schema_version"],
            "scenario_id": fixture["scenario_id"],
            "synthetic_only": True,
            "answer_schema_enforcement": "local_fail_closed",
            "provider_structured_output_required": False,
            "questions": question_set(fixture),
            "question_ids": [question["id"] for question in fixture["questions"]],
        },
        "product_capsule": {
            "schema_version": product["capsule"]["schema_version"],
            "capsule_id": product["capsule"]["capsule_id"],
            "generated_at": product["capsule"]["generated_at"],
            "expires_at": product["capsule"]["expires_at"],
            "ttl_seconds": product["capsule"]["ttl_seconds"],
            "memory_reference_count": len(product["capsule"]["memory_references"]),
            "source_digest": product["capsule"]["source_digest"],
            "token_budget": product["capsule"]["token_budget"],
            "estimated_capsule_tokens": product["capsule"]["estimated_tokens"],
            "dropped_fields": product["capsule"]["dropped_fields"],
            "validation": product["validation"],
            "secret_rejection": product["secret_rejection"],
            "deleted_claim_delivered": fixture["deleted_claim"] in product["delivery_text"],
            "secret_marker_delivered": (
                fixture["synthetic_secret_marker"] in product["delivery_text"]
            ),
            "lifecycle": product["lifecycle"],
            "evidence_refs": product["evidence_refs"],
        },
        "arms": arms,
        "scoring": scoring,
        "cost_latency_comparison": {
            "capsule_payload_estimated_tokens": product["capsule"]["estimated_tokens"],
            "codex_total_usage_token_delta": (
                arms["capsule"]["codex_total_usage_tokens"]
                - arms["no_capsule"]["codex_total_usage_tokens"]
            ),
            "latency_delta_ms": round(
                arms["capsule"]["duration_ms"]
                - arms["no_capsule"]["duration_ms"],
                3,
            ),
            "combined_cost_adjusted_score": None,
            "interpretation": (
                "Quality and cost are reported separately because no arbitrary exchange rate "
                "between correctness, tokens, and latency is imposed."
            ),
        },
        "cost_interpretation": (
            "estimated_capsule_tokens is the product Capsule marginal payload; "
            "codex_total_usage_tokens includes host/system context and is not Capsule-only cost."
        ),
        "safety_boundary": {
            "synthetic_data_only": True,
            "production_memory_database_read": False,
            "production_memory_database_modified": False,
            "agent_private_session_database_read": False,
            "agent_private_session_database_write": False,
            "connectors_created_or_modified": False,
            "tool_access_hard_disabled": False,
            "tool_use_prompt_prohibited": True,
            "agent_working_directory_empty": True,
            "tool_activity_observed": any(arm["tools_called"] for arm in arms.values()),
            "agent_file_reads_observed": any(arm["tools_called"] for arm in arms.values()),
        },
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="strict")
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()
    result = run_acceptance(args.evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
