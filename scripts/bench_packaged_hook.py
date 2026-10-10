from __future__ import annotations

import argparse
import json
import math
import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path

import httpx


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def wait_for_connection(path: Path, timeout: float = 15.0) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            time.sleep(0.05)
            continue
        if payload.get("endpoint") and payload.get("token"):
            return payload
    raise TimeoutError("packaged Service did not publish a runtime connection")


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def run_hook(
    executable: Path,
    connector_id: str,
    payload: dict[str, object],
    *,
    env: dict[str, str],
) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.perf_counter()
    result = subprocess.run(
        [str(executable), "hook", "--connector-id", connector_id],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        capture_output=True,
        env=env,
        timeout=5,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        check=False,
    )
    return result, (time.perf_counter() - started) * 1000


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the packaged synchronous Hook.")
    parser.add_argument("--service-executable", type=Path, required=True)
    parser.add_argument("--hook-executable", type=Path, required=True)
    parser.add_argument("--mcp-directory", type=Path)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    if not 5 <= args.iterations <= 200:
        raise SystemExit("iterations must be between 5 and 200")
    service_executable = args.service_executable.resolve()
    hook_executable = args.hook_executable.resolve()
    if not service_executable.is_file() or not hook_executable.is_file():
        raise SystemExit("packaged Service or Hook executable is missing")

    with tempfile.TemporaryDirectory(prefix="personalmemory-hook-package-bench-") as raw:
        root = Path(raw)
        project = root / "project"
        project.mkdir()
        transcript = project / "session.jsonl"
        transcript.write_text("", encoding="utf-8")
        data_dir = root / "data"
        token = secrets.token_urlsafe(48)
        service_env = os.environ.copy()
        service_env.update(
            {
                "PERSONALMEMORY_DATA_DIR": str(data_dir),
                "PERSONALMEMORY_API_TOKEN": token,
                "PERSONALMEMORY_HOST": "127.0.0.1",
                "PERSONALMEMORY_PORT": "0",
                "PERSONALMEMORY_ENABLE_BACKGROUND_SYNC": "false",
                "PERSONALMEMORY_LLM_ENABLED": "false",
            }
        )
        service = subprocess.Popen(
            [str(service_executable)],
            env=service_env,
            cwd=service_executable.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        connection = wait_for_connection(data_dir / "runtime" / "connection.json")
        endpoint = connection["endpoint"]
        headers = {"Authorization": f"Bearer {connection['token']}"}
        hook_env = os.environ.copy()
        hook_env["PERSONALMEMORY_DATA_DIR"] = str(data_dir)
        try:
            with httpx.Client(base_url=endpoint + "/api/v1", headers=headers, timeout=5) as client:
                connector_response = client.post(
                    "/connectors",
                    json={
                        "name": "Packaged Hook benchmark",
                        "profile_id": "claude_code_hook",
                        "source_path": str(project),
                        "target_path": None,
                        "namespace": "projects/benchmark",
                        "read_enabled": True,
                        "write_enabled": False,
                    },
                )
                connector_response.raise_for_status()
                connector_id = connector_response.json()["id"]
                memory_response = client.post(
                    "/memories",
                    json={
                        "type": "decision",
                        "statement": "Use a Provider Boundary Ledger before adding providers.",
                        "namespace": "projects/benchmark",
                    },
                )
                memory_response.raise_for_status()

            user_ms: list[float] = []
            stop_ms: list[float] = []
            context_successes = 0
            for index in range(args.iterations):
                common = {
                    "session_id": "package-benchmark-session",
                    "prompt_id": f"prompt-{index:03d}",
                    "transcript_path": str(transcript),
                    "cwd": str(project),
                }
                user_result, elapsed = run_hook(
                    hook_executable,
                    connector_id,
                    {
                        **common,
                        "hook_event_name": "UserPromptSubmit",
                        "prompt": "How should I design a provider boundary ledger?",
                    },
                    env=hook_env,
                )
                user_ms.append(elapsed)
                if (
                    user_result.returncode == 0
                    and not user_result.stderr
                    and "additionalContext" in user_result.stdout
                ):
                    context_successes += 1
                stop_result, elapsed = run_hook(
                    hook_executable,
                    connector_id,
                    {
                        **common,
                        "hook_event_name": "Stop",
                        "last_assistant_message": "Done.",
                    },
                    env=hook_env,
                )
                stop_ms.append(elapsed)
                if stop_result.returncode != 0 or stop_result.stdout or stop_result.stderr:
                    raise RuntimeError(
                        "packaged Stop Hook was not silent and fail-open: "
                        f"returncode={stop_result.returncode}, "
                        f"stdout={stop_result.stdout!r}, stderr={stop_result.stderr!r}"
                    )

            with httpx.Client(base_url=endpoint + "/api/v1", headers=headers, timeout=5) as client:
                client.post("/system/shutdown").raise_for_status()
            service.wait(timeout=10)
            down_result, down_ms = run_hook(
                hook_executable,
                connector_id,
                {
                    "session_id": "service-down-session",
                    "prompt_id": "service-down-prompt",
                    "transcript_path": str(transcript),
                    "cwd": str(project),
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "How should I design a provider boundary ledger?",
                },
                env=hook_env,
            )
        finally:
            if service.poll() is None:
                service.terminate()
                service.wait(timeout=5)

    user_p95 = percentile(user_ms, 0.95)
    stop_p95 = percentile(stop_ms, 0.95)
    result = {
        "iterations": args.iterations,
        "external_llm_enabled": False,
        "context_successes": context_successes,
        "user_prompt_ms": {
            "p50": round(percentile(user_ms, 0.50), 3),
            "p95": round(user_p95, 3),
            "max": round(max(user_ms), 3),
        },
        "stop_ms": {
            "p50": round(percentile(stop_ms, 0.50), 3),
            "p95": round(stop_p95, 3),
            "max": round(max(stop_ms), 3),
        },
        "service_down_ms": round(down_ms, 3),
        "service_down_silent": bool(
            down_result.returncode == 0 and not down_result.stdout and not down_result.stderr
        ),
        "hook_package_bytes": directory_size(hook_executable.parent),
        "mcp_package_bytes": (
            directory_size(args.mcp_directory.resolve()) if args.mcp_directory else None
        ),
        "threshold_ms": 1000,
        "passed": bool(
            context_successes == args.iterations
            and user_p95 < 1000
            and stop_p95 < 1000
            and down_result.returncode == 0
            and not down_result.stdout
            and not down_result.stderr
        ),
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
