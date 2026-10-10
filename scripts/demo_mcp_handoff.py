"""Run a deterministic, local-only MCP memory handoff demonstration.

The demo proves the product loop without a model call or external network:
Agent A writes an insight, Agent B reads it through MCP search, Agent B writes
a decision, and Agent A reads that decision back.  All data is synthetic and
the temporary SQLite directory is removed before the process exits.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import suppress
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SERVICE_SOURCE = ROOT / "service" / "src"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_service(endpoint: str, token: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 15
    request = urllib.request.Request(
        f"{endpoint}/api/v1/health",
        headers={"Authorization": f"Bearer {token}"},
    )
    while time.monotonic() < deadline and process.poll() is None:
        try:
            with urllib.request.urlopen(request, timeout=1) as response:
                if json.load(response).get("status") == "ready":
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    raise RuntimeError("PersonalMemory Service did not become ready within 15 seconds")


class McpProcess:
    def __init__(self, endpoint: str, token: str, env: dict[str, str]) -> None:
        mcp_env = env | {
            "PERSONALMEMORY_ENDPOINT": endpoint,
            "PERSONALMEMORY_TOKEN": token,
        }
        self.process = subprocess.Popen(
            [sys.executable, "-m", "personalmemory.agent_mcp"],
            cwd=ROOT,
            env=mcp_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
        )
        self._request_id = 0

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._request_id += 1
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise RuntimeError(f"MCP server closed its stream: {stderr}")
        response = json.loads(line)
        if "error" in response:
            raise RuntimeError(f"MCP JSON-RPC error: {response['error']}")
        return response["result"]

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def call_tool(client: McpProcess, name: str, arguments: dict[str, Any]) -> str:
    result = client.request(
        "tools/call",
        {"name": name, "arguments": arguments},
    )
    if result.get("isError"):
        raise RuntimeError(f"MCP tool {name} failed: {result}")
    content = result.get("content") or []
    return str(content[0]["text"])


def shutdown_service(endpoint: str, token: str) -> None:
    request = urllib.request.Request(
        f"{endpoint}/api/v1/system/shutdown",
        headers={"Authorization": f"Bearer {token}"},
        method="POST",
    )
    with suppress(OSError, urllib.error.URLError):
        urllib.request.urlopen(request, timeout=2).read()


def main() -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="strict")
    port = free_port()
    endpoint = f"http://127.0.0.1:{port}"
    token = "demo-" + os.urandom(12).hex()
    data_dir = Path(tempfile.mkdtemp(prefix="personalmemory-mcp-handoff-"))
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(SERVICE_SOURCE),
            "PERSONALMEMORY_DATA_DIR": str(data_dir),
            "PERSONALMEMORY_HOST": "127.0.0.1",
            "PERSONALMEMORY_PORT": str(port),
            "PERSONALMEMORY_API_TOKEN": token,
            "PERSONALMEMORY_ENABLE_BACKGROUND_SYNC": "false",
        }
    )
    service = subprocess.Popen(
        [sys.executable, "-m", "personalmemory.main"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    mcp: McpProcess | None = None
    try:
        wait_for_service(endpoint, token, service)
        mcp = McpProcess(endpoint, token, env)
        initialized = mcp.request("initialize")
        tools = mcp.request("tools/list")["tools"]

        initial_text = call_tool(
            mcp,
            "remember",
            {
                "type": "insight",
                "statement": "结构化信息优先于纯向量检索",
                "reason": "个人记忆中有大量明确事实和决策",
            },
        )
        inherited_text = call_tool(
            mcp,
            "search_memories",
            {"query": "结构化", "limit": 5},
        )
        returned_memory = json.loads(inherited_text)[0]
        decision_text = call_tool(
            mcp,
            "remember",
            {
                "type": "decision",
                "statement": "Memory Core 先使用结构化查询和 FTS",
                "reason": "在真实规模证明不足前不引入向量数据库",
            },
        )
        round_trip_text = call_tool(
            mcp,
            "search_memories",
            {"query": "Memory Core FTS", "limit": 5},
        )
        round_trip_memory = json.loads(round_trip_text)[0]

        evidence = {
            "mode": "local_synthetic_no_model",
            "protocol_version": initialized["protocolVersion"],
            "tools": [tool["name"] for tool in tools],
            "agent_a_write": json.loads(initial_text)["action"],
            "agent_b_inherited": returned_memory["statement"],
            "agent_b_write": json.loads(decision_text)["action"],
            "agent_a_round_trip": round_trip_memory["statement"],
            "assertions": {
                "mcp_handshake": initialized["serverInfo"]["name"] == "personalmemory",
                "context_crossed_agent_boundary": returned_memory["type"] == "insight",
                "memory_returned_to_core": round_trip_memory["type"] == "decision",
            },
        }
        if not all(evidence["assertions"].values()):
            raise RuntimeError(f"MCP handoff assertions failed: {evidence}")
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 0
    finally:
        if mcp is not None:
            mcp.close()
        shutdown_service(endpoint, token)
        if service.poll() is None:
            try:
                service.wait(timeout=5)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait(timeout=5)
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
