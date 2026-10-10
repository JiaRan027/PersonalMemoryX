from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from personalmemory.agent_hook import build_claude_hook_settings

NATURAL_PROMPT = (
    "我在做一个支持多个模型供应商的评测平台。想让业务层保持干净，同时每家模型的能力、"
    "错误码和计费又不一样。你会怎么划分架构？给出实施顺序和不能抽象掉的边界。"
    "Please answer in English."
)
FORBIDDEN_PROMPT_TERMS = (
    "PersonalMemory",
    "记忆",
    "历史",
    "MCP",
    "Continuity Core",
    "Translator",
    "稳定核心",
    "薄适配层",
)


def wait_for_connection(path: Path, timeout: float = 20.0) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            time.sleep(0.1)
            continue
        if value.get("endpoint") and value.get("token"):
            return {"endpoint": value["endpoint"], "token": value["token"]}
    raise RuntimeError("isolated PersonalMemory Service did not become ready")


def run_claude(
    executable: str,
    *,
    cwd: Path,
    env: dict[str, str],
    settings: Path | None,
) -> tuple[dict[str, Any], float]:
    command = [
        executable,
        "-p",
        NATURAL_PROMPT,
        "--output-format",
        "json",
        "--permission-mode",
        "dontAsk",
        "--max-budget-usd",
        "0.50",
        "--tools",
        "",
        "--session-id",
        str(uuid4()),
    ]
    if settings is not None:
        command.extend(["--settings", str(settings)])
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
        check=False,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    if completed.returncode != 0:
        raise RuntimeError(
            f"Claude exited with {completed.returncode}: {completed.stderr[-1000:]}"
        )
    return json.loads(completed.stdout), elapsed_ms


def post_hook(
    executable: Path,
    connector_id: str,
    payload: dict[str, object],
    *,
    env: dict[str, str],
) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.perf_counter()
    completed = subprocess.run(
        [str(executable), "hook", "--connector-id", connector_id],
        input=json.dumps(payload, ensure_ascii=False),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=10,
        check=False,
    )
    return completed, (time.perf_counter() - started) * 1000


def answer_signals(answer: str) -> dict[str, bool]:
    lowered = answer.casefold()
    return {
        "stable_core_thin_adapter": (
            ("稳定" in answer and "适配" in answer)
            or ("core" in lowered and "adapter" in lowered)
        ),
        "unified_envelope": (
            "统一" in answer
            and any(term in answer for term in ("模型", "协议", "请求", "响应", "结构"))
        )
        or "envelope" in lowered,
        "capability_matrix": (
            "能力矩阵" in answer
            or "capability matrix" in lowered
            or (
                "capability" in lowered
                and any(term in lowered for term in ("validation", "planner", "driven"))
            )
        ),
        "non_transferable_boundaries": (
            all(term in answer for term in ("能力", "错误", "计费"))
            or all(term in lowered for term in ("capabil", "error", "billing"))
        ),
        "provider_boundary_ledger": "provider boundary ledger" in lowered,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run real Claude associative recall A/B.")
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--mcp-executable", type=Path, required=True)
    parser.add_argument("--hook-executable", type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()

    if any(term.casefold() in NATURAL_PROMPT.casefold() for term in FORBIDDEN_PROMPT_TERMS):
        raise RuntimeError("acceptance prompt contains a forbidden memory cue")
    claude = shutil.which(args.claude) or args.claude
    mcp_executable = args.mcp_executable.resolve()
    hook_executable = (
        args.hook_executable.resolve() if args.hook_executable else mcp_executable
    )
    if not mcp_executable.is_file():
        raise SystemExit(f"missing MCP executable: {mcp_executable}")
    if not hook_executable.is_file():
        raise SystemExit(f"missing Hook executable: {hook_executable}")

    evidence: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="personalmemory-associative-") as raw_root:
        root = Path(raw_root)
        current_project = root / "model-evaluation"
        current_project.mkdir()
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
            [sys.executable, "-m", "personalmemory.main"],
            env=service_env,
            cwd=current_project,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        endpoint: str | None = None
        headers: dict[str, str] | None = None
        try:
            connection = wait_for_connection(data_dir / "runtime" / "connection.json")
            endpoint = connection["endpoint"]
            headers = {"Authorization": f"Bearer {connection['token']}"}
            hook_env = os.environ.copy()
            hook_env["PERSONALMEMORY_DATA_DIR"] = str(data_dir)

            with httpx.Client(base_url=endpoint + "/api/v1", headers=headers, timeout=5) as client:
                connector_response = client.post(
                    "/connectors",
                    json={
                        "name": "Real Claude associative recall",
                        "profile_id": "claude_code_hook",
                        "source_path": str(current_project),
                        "target_path": None,
                        "namespace": "projects/model-evaluation",
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
                        "statement": (
                            "平台差异应限制在薄 Translator 中，Continuity Core 保持平台无关。"
                        ),
                        "reason": "Agent 可以替换，而连续性内核不能绑定任何厂商。",
                        "namespace": "projects/personalmemory",
                        "transfer": {
                            "scope": "cross_project",
                            "problem_shape": "多个外部模型供应商格式不同但共享业务语义",
                            "principle": (
                                "稳定核心 + 薄适配层；写 Adapter 前先建立 Provider Boundary "
                                "Ledger（能力、错误、计费三列），再定义统一 Envelope"
                            ),
                            "applies_when": [
                                "接入多个模型供应商",
                                "避免各家 API 判断污染业务核心",
                            ],
                            "avoid_when": ["供应商能力、错误码或计费差异本身就是业务语义"],
                        },
                    },
                )
                memory_response.raise_for_status()
                memory = memory_response.json()["memory"]
                isolated_response = client.post(
                    "/memories",
                    json={
                        "type": "decision",
                        "statement": "旧评测项目规定所有供应商响应保存为 XML。",
                        "namespace": "projects/legacy-evaluation",
                    },
                )
                isolated_response.raise_for_status()
                isolated_memory_id = isolated_response.json()["memory"]["id"]
                preflight_response = client.post(
                    "/memories/preflight",
                    json={
                        "connector_id": connector_id,
                        "cwd": str(current_project),
                        "query": NATURAL_PROMPT,
                    },
                )
                preflight_response.raise_for_status()
                preflight = preflight_response.json()

            settings_path = root / "claude-settings.json"
            settings_path.write_text(
                json.dumps(
                    build_claude_hook_settings(str(hook_executable), connector_id),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            baseline, baseline_ms = run_claude(
                claude,
                cwd=current_project,
                env=hook_env,
                settings=None,
            )

            transcript = current_project / "probe.jsonl"
            transcript.write_text("", encoding="utf-8")
            hook_probe, hook_probe_ms = post_hook(
                hook_executable,
                connector_id,
                {
                    "session_id": "preflight-probe-session",
                    "prompt_id": "preflight-probe-prompt",
                    "transcript_path": str(transcript),
                    "cwd": str(current_project),
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": NATURAL_PROMPT,
                },
                env=hook_env,
            )
            hook_output = json.loads(hook_probe.stdout) if hook_probe.stdout else {}
            assisted, assisted_ms = run_claude(
                claude,
                cwd=current_project,
                env=hook_env,
                settings=settings_path,
            )

            baseline_answer = str(baseline.get("result") or "")
            assisted_answer = str(assisted.get("result") or "")
            preflight_ids = [item["id"] for item in preflight["items"]]
            hook_context = (
                hook_output.get("hookSpecificOutput", {}).get("additionalContext", "")
            )
            evidence = {
                "client": "Claude Code 2.1.220",
                "model": assisted.get("modelUsage") or baseline.get("modelUsage"),
                "isolated_database": True,
                "global_claude_config_modified": False,
                "personalmemory_external_llm_enabled": False,
                "prompt_contains_forbidden_memory_cue": False,
                "mcp_tools_available": False,
                "preflight": {
                    "memory_id": memory["id"],
                    "selected_ids": preflight_ids,
                    "selected_transfer": memory["id"] in preflight_ids,
                    "excluded_unscoped_project_memory": isolated_memory_id not in preflight_ids,
                    "delivered_tokens": preflight["delivered_tokens"],
                    "match_kinds": [item["match_kind"] for item in preflight["items"]],
                },
                "packaged_hook": {
                    "exit_code": hook_probe.returncode,
                    "stderr_empty": not hook_probe.stderr,
                    "additional_context_emitted": bool(hook_context),
                    "contains_memory_id": memory["id"] in hook_context,
                    "elapsed_ms": round(hook_probe_ms, 3),
                },
                "baseline": {
                    "signals": answer_signals(baseline_answer),
                    "elapsed_ms": round(baseline_ms, 3),
                    "cost_usd": baseline.get("total_cost_usd"),
                    "answer_excerpt": baseline_answer[:1200],
                },
                "assisted": {
                    "signals": answer_signals(assisted_answer),
                    "elapsed_ms": round(assisted_ms, 3),
                    "cost_usd": assisted.get("total_cost_usd"),
                    "answer_excerpt": assisted_answer[:1200],
                },
            }
        finally:
            if service.poll() is None:
                if endpoint is not None and headers is not None:
                    try:
                        with httpx.Client(
                            base_url=endpoint + "/api/v1", headers=headers, timeout=2
                        ) as client:
                            client.post("/system/shutdown")
                    except httpx.HTTPError:
                        pass
                try:
                    service.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    service.terminate()
                    service.wait(timeout=5)

    preflight_result = evidence["preflight"]
    hook_result = evidence["packaged_hook"]
    baseline_signals = evidence["baseline"]["signals"]
    assisted_signals = evidence["assisted"]["signals"]
    evidence["passed"] = all(
        (
            preflight_result["selected_transfer"],
            preflight_result["excluded_unscoped_project_memory"],
            preflight_result["delivered_tokens"] <= 900,
            hook_result["exit_code"] == 0,
            hook_result["stderr_empty"],
            hook_result["additional_context_emitted"],
            hook_result["contains_memory_id"],
            assisted_signals["stable_core_thin_adapter"],
            assisted_signals["unified_envelope"],
            assisted_signals["capability_matrix"],
            assisted_signals["non_transferable_boundaries"],
            assisted_signals["provider_boundary_ledger"],
            not baseline_signals["provider_boundary_ledger"],
        )
    )
    rendered = json.dumps(evidence, ensure_ascii=False, indent=2)
    if args.evidence is not None:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if not evidence["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
