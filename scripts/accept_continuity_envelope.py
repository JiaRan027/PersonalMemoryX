"""Run Stage 41 Continuity Envelope acceptance over a real loopback HTTP server."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = (
    ROOT
    / "docs"
    / "implementation"
    / "evidence"
    / "2026-08-04"
    / "stage41-1-continuity-envelope-acceptance.json"
)
TOKEN = "stage41-isolated-acceptance-token"


def reserve_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def envelope(
    connector_id: str,
    *,
    text: str,
    key: str,
    namespace: str = "projects/atlas",
    authority: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "connector_id": connector_id,
        "envelope": {
            "schema_version": "1",
            "source_kind": "interaction",
            "source_family": "stage41_acceptance_agent",
            "capture_mode": "structured_api",
            "capabilities": {
                "has_actor_roles": True,
                "has_session": True,
                "has_timestamps": True,
                "has_tool_calls": False,
                "has_attachments": False,
                "is_realtime": True,
            },
            "evidence": {
                "locator": f"acceptance://session-1/{key}",
                "digest": hashlib.sha256(text.encode()).hexdigest(),
                "interaction_id": key,
                "message_ids": [f"message-{key}"],
                "evidence_kind": "message",
                "actor_kind": "user",
            },
            "authority": authority
            or {
                "actor_kind": "user",
                "assertion_kind": "explicit",
                "actor_id": "acceptance-user",
            },
            "namespace": namespace,
            "idempotency_key": key,
            "user_text": text,
            "assistant_text": "Acknowledged.",
            "session_id": "session-1",
        },
    }


def wait_until_ready(client: httpx.Client) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if client.get("/api/v1/health").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("isolated PersonalMemory service did not become ready")


def run_acceptance(evidence_path: Path) -> dict[str, Any]:
    port = reserve_port()
    with tempfile.TemporaryDirectory(prefix="personalmemory-stage41-") as temp_value:
        data_dir = Path(temp_value) / "data"
        env = os.environ.copy()
        env.update(
            {
                "PYTHONPATH": str(ROOT / "service" / "src"),
                "PYTHONUTF8": "1",
                "PERSONALMEMORY_DATA_DIR": str(data_dir),
                "PERSONALMEMORY_API_TOKEN": TOKEN,
                "PERSONALMEMORY_HOST": "127.0.0.1",
                "PERSONALMEMORY_PORT": str(port),
                "PERSONALMEMORY_ENABLE_BACKGROUND_SYNC": "false",
                "PERSONALMEMORY_LLM_ENABLED": "false",
            }
        )
        process = subprocess.Popen(
            [sys.executable, "-m", "personalmemory.main"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        started = time.perf_counter()
        headers = {"Authorization": f"Bearer {TOKEN}"}
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", headers=headers, timeout=10
            ) as client:
                wait_until_ready(client)
                health = client.get("/api/v1/health")
                connector_response = client.post(
                    "/api/v1/connectors",
                    json={
                        "name": "Stage 41 generic source",
                        "profile_id": "continuity_envelope_api",
                        "source_path": None,
                        "target_path": None,
                        "namespace": "projects/atlas",
                        "read_enabled": True,
                        "write_enabled": False,
                    },
                )
                connector_response.raise_for_status()
                connector_id = connector_response.json()["id"]

                explicit_payload = envelope(
                    connector_id,
                    text=(
                        "I decided to keep Continuity Core platform-neutral because Agents "
                        "are replaceable."
                    ),
                    key="explicit-1",
                )
                explicit = client.post("/api/v1/envelopes", json=explicit_payload)
                replay = client.post("/api/v1/envelopes", json=explicit_payload)
                alias_payload = json.loads(json.dumps(explicit_payload))
                alias_payload["envelope"]["idempotency_key"] = "explicit-alias"
                alias = client.post("/api/v1/envelopes", json=alias_payload)
                alias_changed = json.loads(json.dumps(alias_payload))
                alias_changed["envelope"]["user_text"] = (
                    "I decided to make one Agent authoritative."
                )
                alias_changed_response = client.post(
                    "/api/v1/envelopes", json=alias_changed
                )
                changed = client.post(
                    "/api/v1/envelopes",
                    json=envelope(
                        connector_id,
                        text="I decided to make one Agent authoritative.",
                        key="explicit-1",
                    ),
                )
                inferred = client.post(
                    "/api/v1/envelopes",
                    json=envelope(
                        connector_id,
                        text="I prefer TypeScript for every future project.",
                        key="inferred-1",
                        authority={
                            "actor_kind": "agent",
                            "assertion_kind": "inferred",
                            "actor_id": "stage41_acceptance_agent",
                        },
                    ),
                )
                denied = client.post(
                    "/api/v1/envelopes",
                    json=envelope(
                        connector_id,
                        text="I prefer an unauthorized namespace.",
                        key="denied-1",
                        namespace="projects/other",
                    ),
                )
                secret = client.post(
                    "/api/v1/envelopes",
                    json=envelope(
                        connector_id,
                        text="I decided to use api_token=TEST_ONLY_REDACTED_VALUE.",
                        key="secret-1",
                    ),
                )
                private_payload = envelope(
                    connector_id,
                    text="I prefer governed visible evidence.",
                    key="private-1",
                )
                private_payload["envelope"]["thinking"] = "must not cross boundary"
                private_response = client.post(
                    "/api/v1/envelopes", json=private_payload
                )
                metadata_secret_payload = envelope(
                    connector_id,
                    text="I prefer governed visible evidence.",
                    key="metadata-secret-1",
                )
                metadata_secret_payload["envelope"]["evidence"]["locator"] = (
                    "https://example.test/source?api_token=TEST_ONLY_REDACTED_VALUE"
                )
                metadata_secret = client.post(
                    "/api/v1/envelopes", json=metadata_secret_payload
                )
                context = client.post(
                    "/api/v1/memories/context",
                    json={"query": "platform-neutral Continuity Core decision"},
                )
                memories = client.get("/api/v1/memories")
                for response in (health, explicit, replay, alias, inferred, context, memories):
                    response.raise_for_status()
                client.post("/api/v1/system/shutdown")
        finally:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
        if process.returncode not in {0, None}:
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(f"isolated service exited {process.returncode}: {stderr[-2000:]}")

    completed = datetime.now(UTC)
    local = completed.astimezone(timezone(timedelta(hours=8), name="Asia/Shanghai"))
    items = memories.json()["items"]
    checks = {
        "authenticated_create": connector_response.status_code == 201,
        "build_revision_visible": health.json()["build_revision"] == "stage41.1",
        "external_user_claim_provisional": explicit.json()["counts"]["provisional"]
        == 1,
        "exact_replay_duplicate": replay.status_code == 200 and replay.json()["duplicate"],
        "deduplicated_alias_bound": alias.status_code == 200
        and alias.json()["duplicate"],
        "alias_changed_content_conflict": alias_changed_response.status_code == 409,
        "changed_key_conflict": changed.status_code == 409,
        "agent_inference_provisional": inferred.json()["counts"]["provisional"] == 1,
        "namespace_denied": denied.status_code == 403,
        "secret_rejected": secret.status_code == 422,
        "private_payload_rejected": private_response.status_code == 422,
        "metadata_secret_rejected": metadata_secret.status_code == 422,
        "lexical_capability_truth": context.json()["delivery_receipt"]
        == {
            "requested_mode": "lexical",
            "effective_mode": "lexical",
            "status": "ok",
            "cache_class": "dynamic",
            "degradation_reason": None,
        },
        "self_attested_authority_preserved": all(
            item["epistemic_state"] == "inferred" for item in items
        ),
    }
    result = {
        "date": local.date().isoformat(),
        "stage": "41.1",
        "status": "pass" if all(checks.values()) else "fail",
        "completed_utc": completed.isoformat(),
        "completed_local": local.isoformat(),
        "timezone": "Asia/Shanghai (UTC+08:00)",
        "product_version": "0.2.1",
        "build_revision": health.json()["build_revision"],
        "transport": "real loopback HTTP to isolated uvicorn service",
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "checks": checks,
        "memory_count": len(items),
        "response_codes": {
            "explicit": explicit.status_code,
            "replay": replay.status_code,
            "alias": alias.status_code,
            "alias_changed": alias_changed_response.status_code,
            "changed": changed.status_code,
            "inferred": inferred.status_code,
            "namespace_denied": denied.status_code,
            "secret": secret.status_code,
            "private_payload": private_response.status_code,
            "metadata_secret": metadata_secret.status_code,
        },
        "delivery_receipt": context.json()["delivery_receipt"],
        "safety_boundary": {
            "isolated_temporary_database": True,
            "production_memory_database_modified": False,
            "external_llm_called": False,
            "agent_private_memory_read": False,
            "agent_private_memory_written": False,
        },
    }
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    result = run_acceptance(DEFAULT_EVIDENCE)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
