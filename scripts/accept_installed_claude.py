from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

PROMPT = (
    "如果我准备继续推进手头最重要的产品，你认为它是什么，核心目标又是什么？"
    "请直接回答；没有依据就说不知道。"
)


def observation_count(database: Path) -> int:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM agent_turn_observations"
        ).fetchone()[0]


def latest_observation(database: Path) -> dict[str, Any] | None:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT id, session_id, prompt_id, status, "
            "user_hash IS NOT NULL, assistant_hash IS NOT NULL "
            "FROM agent_turn_observations ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "session_id": row[1],
        "prompt_id": row[2],
        "status": row[3],
        "user_hash_present": bool(row[4]),
        "assistant_hash_present": bool(row[5]),
    }


def wait_for_completed_observation(
    database: Path, before: int, timeout: float = 30.0
) -> tuple[int, dict[str, Any] | None]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        count = observation_count(database)
        latest = latest_observation(database)
        if (
            count > before
            and latest is not None
            and latest["status"] == "processed"
            and latest["user_hash_present"]
            and latest["assistant_hash_present"]
        ):
            return count, latest
        time.sleep(0.1)
    return observation_count(database), latest_observation(database)


def main() -> None:
    parser = argparse.ArgumentParser(description="Accept the installed Claude Hook lifecycle.")
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()

    project = args.project.resolve(strict=True)
    database = args.database.resolve(strict=True)
    before = observation_count(database)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="personalmemory-installed-acceptance-") as raw:
        mcp_config = Path(raw) / "mcp.json"
        mcp_config.write_text('{"mcpServers":{}}\n', encoding="utf-8")
        completed = subprocess.run(
            [
                args.claude,
                "-p",
                PROMPT,
                "--output-format",
                "json",
                "--permission-mode",
                "dontAsk",
                "--max-budget-usd",
                "0.15",
                "--mcp-config",
                str(mcp_config),
                "--strict-mcp-config",
                "--tools",
                "",
            ],
            cwd=project,
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
    response = json.loads(completed.stdout)
    after, observation = wait_for_completed_observation(database, before)
    answer = str(response.get("result") or "")
    normalized = answer.casefold()
    answer_mentions_product = "personalmemory" in normalized
    answer_mentions_continuity = "连续" in answer or "continuity" in normalized
    passed = bool(
        after == before + 1
        and observation is not None
        and observation["status"] == "processed"
        and observation["user_hash_present"]
        and observation["assistant_hash_present"]
        and answer_mentions_product
        and answer_mentions_continuity
    )
    evidence = {
        "date": time.strftime("%Y-%m-%d"),
        "status": "pass" if passed else "fail",
        "installed_acceptance": True,
        "prompt": PROMPT,
        "prompt_contains_memory_cue": False,
        "strict_empty_mcp_config": True,
        "tools_selector": "empty",
        "return_code": completed.returncode,
        "elapsed_ms": round(elapsed_ms, 3),
        "model": response.get("modelUsage"),
        "cost_usd": response.get("total_cost_usd"),
        "answer": answer,
        "answer_mentions_product": answer_mentions_product,
        "answer_mentions_continuity": answer_mentions_continuity,
        "observations_before": before,
        "observations_after": after,
        "observation": observation,
        "passed": passed,
    }
    rendered = json.dumps(evidence, ensure_ascii=False, indent=2)
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
