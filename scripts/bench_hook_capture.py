from __future__ import annotations

import argparse
import json
import math
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient
from personalmemory.api.app import create_app
from personalmemory.settings import Settings


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return ordered[index]


def event(
    connector_id: str,
    project_root: Path,
    transcript: Path,
    prompt_id: str,
    *,
    fragment_kind: str,
) -> dict[str, object]:
    return {
        "schema_version": "1",
        "connector_id": connector_id,
        "agent_family": "claude_code",
        "fragment_kind": fragment_kind,
        "session_id": "benchmark-session",
        "prompt_id": prompt_id,
        "cwd": str(project_root),
        "transcript_path": str(transcript),
        "user_text": (
            f"我决定采用本地方案 {prompt_id}。" if fragment_kind == "user_message" else None
        ),
        "assistant_text": "已完成。" if fragment_kind == "assistant_message" else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark local hook observation delivery.")
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 1000:
        raise SystemExit("iterations must be between 1 and 1000")

    with tempfile.TemporaryDirectory(prefix="personalmemory-hook-bench-") as raw_dir:
        root = Path(raw_dir)
        project_root = root / "project"
        project_root.mkdir()
        transcript = root / "session.jsonl"
        transcript.write_text("", encoding="utf-8")
        settings = Settings(
            data_dir=root / "data",
            api_token="benchmark-token",
            enable_background_sync=False,
            llm_enabled=False,
        )
        headers = {"Authorization": "Bearer benchmark-token"}
        latencies_ms: list[float] = []

        with TestClient(create_app(settings)) as client:
            connector_response = client.post(
                "/api/v1/connectors",
                headers=headers,
                json={
                    "name": "Hook benchmark",
                    "profile_id": "claude_code_hook",
                    "source_path": str(project_root),
                    "target_path": None,
                    "namespace": "projects/benchmark",
                    "read_enabled": True,
                    "write_enabled": False,
                },
            )
            connector_response.raise_for_status()
            connector_id = connector_response.json()["id"]

            for index in range(args.iterations):
                prompt_id = f"prompt-{index:04d}"
                for fragment_kind in ("user_message", "assistant_message"):
                    started = time.perf_counter()
                    response = client.post(
                        "/api/v1/observations/agent-turns",
                        headers=headers,
                        json=event(
                            connector_id,
                            project_root,
                            transcript,
                            prompt_id,
                            fragment_kind=fragment_kind,
                        ),
                    )
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    response.raise_for_status()
                    latencies_ms.append(elapsed_ms)

        result = {
            "iterations": args.iterations,
            "requests": len(latencies_ms),
            "p50_ms": round(percentile(latencies_ms, 0.50), 3),
            "p95_ms": round(percentile(latencies_ms, 0.95), 3),
            "max_ms": round(max(latencies_ms), 3),
            "threshold_ms": 500,
            "passed": percentile(latencies_ms, 0.95) < 500,
            "model_calls": 0,
            "model_tokens": 0,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
