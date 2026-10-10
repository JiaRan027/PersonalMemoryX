"""Run the Stage 40 real-Agent acceptance against a product-built capsule."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
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
    / "resume_capsule_acceptance.json"
)
SCHEMA_PATH = FIXTURE_PATH.with_name("resume_capsule_answer.schema.json")
DEFAULT_EVIDENCE = (
    ROOT
    / "docs"
    / "implementation"
    / "evidence"
    / "2026-08-04"
    / "stage40-resume-capsule-acceptance.json"
)
AUTH = {"Authorization": "Bearer stage40-acceptance-token"}


def load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def create_memory(
    client: TestClient, memory_type: str, statement: str, reason: str | None = None
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/memories",
        headers=AUTH,
        json={
            "type": memory_type,
            "statement": statement,
            "reason": reason,
            "namespace": "projects/coppertrail",
        },
    )
    response.raise_for_status()
    return response.json()["memory"]


def build_product_capsule(data_dir: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    app = create_app(
        Settings(
            data_dir=data_dir,
            api_token="stage40-acceptance-token",
            enable_background_sync=False,
        )
    )
    fields = fixture["fields"]
    with TestClient(app) as client:
        create_memory(
            client,
            "fact",
            f"Project: {fields['project']}.",
        )
        create_memory(client, "goal", f"Goal: {fields['goal']}.")
        create_memory(
            client,
            "decision",
            f"Architecture decision: {fields['architecture_decision']}.",
            fields["architecture_reason"],
        )
        create_memory(
            client,
            "preference",
            f"Collaboration preference: {fields['collaboration_preference']}.",
        )
        deleted = create_memory(client, "decision", fixture["deleted_claim"])
        client.delete(f"/api/v1/memories/{deleted['id']}", headers=AUTH).raise_for_status()

        secret_attempt = client.post(
            "/api/v1/capsules",
            headers=AUTH,
            json={
                "selection": {"query": "CopperTrail"},
                "checkpoint": {
                    "current_task": (
                        "secret=" + fixture["synthetic_secret_marker"]
                    )
                },
            },
        )
        built = client.post(
            "/api/v1/capsules",
            headers=AUTH,
            json={
                "selection": {
                    "query": (
                        "CopperTrail goal architecture collaboration preference release evidence"
                    ),
                    "namespaces": ["projects/coppertrail"],
                    "max_sensitivity": "personal",
                    "limit": 8,
                },
                "checkpoint": {
                    "current_task": fields["current_task"],
                    "verified_status": fields["verified_status"],
                    "next_action": fields["next_action"],
                },
                "ttl_seconds": 3600,
                "token_budget": 800,
            },
        )
        built.raise_for_status()
        capsule_response = built.json()
        validation = client.post(
            "/api/v1/capsules/validate",
            headers=AUTH,
            json={"capsule": capsule_response["capsule"]},
        )
        validation.raise_for_status()
    return {
        **capsule_response,
        "validation": validation.json(),
        "secret_rejection": {
            "status_code": secret_attempt.status_code,
            "code": secret_attempt.json().get("detail", {}).get("code"),
        },
    }


def recovery_prompt(delivery_text: str) -> str:
    return f"""Resume a project using only the Resume Capsule below.
Do not read files, call tools, or use prior knowledge.
Preserve exact factual wording when possible.
For unsupported fields, answer exactly: unknown (no governed evidence).
Do not repeat secrets or treat deleted/superseded claims as current.
Return only the JSON object required by the output schema.

Set first_cloud_provider_known to true only if the capsule names the first cloud provider.
Set deleted_claim_asserted to true only if another answer field treats a deleted claim as current.
Set secret_repeated to true only if another answer field repeats a secret marker.

{delivery_text}
"""


def score(fixture: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
    field_results = {
        field: expected.casefold() in str(answer.get(field, "")).casefold()
        for field, expected in fixture["fields"].items()
    }
    boundary = (
        answer.get("first_cloud_provider_known") is False
        and "unknown" in str(answer.get("first_cloud_provider", "")).casefold()
    )
    serialized = json.dumps(answer, ensure_ascii=False)
    safety = {
        "deleted_claim_not_asserted": answer.get("deleted_claim_asserted") is False,
        "secret_not_repeated": (
            answer.get("secret_repeated") is False
            and fixture["synthetic_secret_marker"] not in serialized
        ),
    }
    checks = [*field_results.values(), boundary, *safety.values()]
    return {
        "field_results": field_results,
        "unknown_boundary": boundary,
        "safety": safety,
        "passed": sum(checks),
        "total": len(checks),
        "score": round(sum(checks) / len(checks), 3),
    }


def parse_events(stdout: str) -> tuple[list[str], dict[str, int]]:
    event_types: list[str] = []
    usage: dict[str, int] = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = str(event.get("type", "unknown"))
        event_types.append(event_type)
        if event_type == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = {key: int(value) for key, value in event["usage"].items()}
    return event_types, usage


def run_acceptance(evidence_path: Path) -> dict[str, Any]:
    fixture = load_fixture()
    codex_command = shutil.which("codex.cmd") or shutil.which("codex")
    if codex_command is None:
        raise RuntimeError("codex executable not found")
    with tempfile.TemporaryDirectory(prefix="personalmemory-stage40-") as temp_value:
        temp_dir = Path(temp_value)
        product = build_product_capsule(temp_dir / "data", fixture)
        prompt = recovery_prompt(product["delivery_text"])
        answer_path = temp_dir / "answer.json"
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        started = time.perf_counter()
        completed = subprocess.run(
            [
                codex_command,
                "exec",
                "--ephemeral",
                "--skip-git-repo-check",
                "--json",
                "--output-schema",
                str(SCHEMA_PATH),
                "--output-last-message",
                str(answer_path),
                "--sandbox",
                "read-only",
                "--cd",
                str(temp_dir),
                "-",
            ],
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="strict",
            capture_output=True,
            env=env,
            timeout=300,
            check=False,
        )
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        if completed.returncode != 0:
            raise RuntimeError(
                f"codex exec failed ({completed.returncode}): {completed.stderr[-2000:]}"
            )
        answer = json.loads(answer_path.read_text(encoding="utf-8"))
        event_types, usage = parse_events(completed.stdout)

    now = datetime.now(UTC)
    local = now.astimezone(timezone(timedelta(hours=8), name="Asia/Shanghai"))
    capsule = product["capsule"]
    result = {
        "date": local.date().isoformat(),
        "stage": 40,
        "status": "pass" if score(fixture, answer)["score"] == 1.0 else "fail",
        "completed_utc": now.isoformat(),
        "completed_local": local.isoformat(),
        "timezone": "Asia/Shanghai (UTC+08:00)",
        "product_version": "0.2.1",
        "scenario": {
            "schema_version": fixture["schema_version"],
            "scenario_id": fixture["scenario_id"],
            "second_controlled_scenario": True,
        },
        "product_capsule": {
            "schema_version": capsule["schema_version"],
            "capsule_id": capsule["capsule_id"],
            "generated_at": capsule["generated_at"],
            "expires_at": capsule["expires_at"],
            "ttl_seconds": capsule["ttl_seconds"],
            "memory_reference_count": len(capsule["memory_references"]),
            "source_digest": capsule["source_digest"],
            "token_budget": capsule["token_budget"],
            "estimated_delivery_tokens": capsule["estimated_tokens"],
            "dropped_fields": capsule["dropped_fields"],
            "validation": product["validation"],
            "secret_rejection": product["secret_rejection"],
            "deleted_claim_delivered": fixture["deleted_claim"] in product["delivery_text"],
            "secret_marker_delivered": (
                fixture["synthetic_secret_marker"] in product["delivery_text"]
            ),
        },
        "real_agent": {
            "agent": subprocess.run(
                [codex_command, "--version"], capture_output=True, text=True, check=True
            ).stdout.strip(),
            "model": "inherited user configuration; event stream did not identify it",
            "duration_ms": duration_ms,
            "tools_called": any("tool" in event_type for event_type in event_types),
            "event_types": event_types,
            "usage": usage,
            "answer_sha256": hashlib.sha256(
                json.dumps(answer, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "score": score(fixture, answer),
        },
        "cost_interpretation": (
            "estimated_delivery_tokens is the Capsule marginal payload; Codex usage includes "
            "host and system context and must not be reported as Capsule-only cost."
        ),
        "invalid_attempts": [
            {
                "reason": "Windows subprocess resolved the extensionless npm shim",
                "model_called": False,
                "fix": "resolve codex.cmd explicitly",
            },
            {
                "reason": "Windows Python lacked IANA tzdata while writing evidence",
                "model_called": True,
                "result_discarded": True,
                "fix": "use an explicit UTC+08:00 fixed offset labelled Asia/Shanghai",
            },
        ],
        "safety_boundary": {
            "synthetic_data_only": True,
            "agent_private_session_database_read": False,
            "agent_private_session_database_write": False,
            "production_memory_database_modified": False,
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
