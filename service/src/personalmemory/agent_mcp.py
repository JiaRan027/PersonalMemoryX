"""PersonalMemory MCP stdio server（Agent 接入闭环）。

让 Claude Code / ChatGPT / 任意支持 MCP 的 Agent 通过 stdio 调用本地记忆：

- get_user_context  返回有界的稳定用户上下文（fact/goal/skill/preference）
- memory_context    为当前任务编译有界、带来源的相关 Context
- search_memories   按关键词检索相关长期记忆（FTS）
- remember          写入一条新记忆（复用准入/指纹/墓碑语义，幂等）

连接发现顺序：
1. 环境变量 `PERSONALMEMORY_ENDPOINT` / `PERSONALMEMORY_TOKEN`（显式配置优先）；
2. `PERSONALMEMORY_DATA_DIR` 下的 `runtime/connection.json`（桌面端 Service
   启动后自动写入，跟随当前会话地址与 Token，无需手工复制）；
3. 默认地址 `http://127.0.0.1:19191`（仅自托管固定端口场景）。

实现说明：这是本地 REST API 的薄客户端，不直接访问数据库；stdio 传输使用
换行分隔的 JSON-RPC 2.0（MCP stdio 约定），无 Content-Length 帧。
"""

from __future__ import annotations

import json
import os
import sys
import time
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from personalmemory import __version__
from personalmemory.settings import default_data_dir

SERVER_NAME = "personalmemory"
SERVER_VERSION = __version__
PROTOCOL_VERSION = "2024-11-05"
DEFAULT_ENDPOINT = "http://127.0.0.1:19191"
MEMORY_TYPES = ("fact", "goal", "skill", "preference", "event", "insight", "decision")
MAX_CONTEXT_TOKEN_BUDGET = 4096
SERVICE_RECONNECT_DELAYS = (0.25, 0.5, 1.0, 1.5, 2.0, 2.0, 2.0)


def configure_stdio_utf8() -> None:
    """Force MCP's stdio transport to UTF-8, independent of Windows code pages."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


class MemoryNotAdmitted(RuntimeError):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail))
        self.detail = detail


def trust_environment_proxy(endpoint: str) -> bool:
    """Loopback Service traffic must not leak into a Windows system proxy."""
    hostname = urlparse(endpoint).hostname
    if not hostname:
        return True
    if hostname.casefold() == "localhost":
        return False
    try:
        return not ip_address(hostname).is_loopback
    except ValueError:
        return True


class MemoryClient:
    def __init__(self, endpoint: str, token: str, timeout: float = 8.0) -> None:
        self._base = endpoint.rstrip("/") + "/api/v1"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout
        self._trust_env = trust_environment_proxy(endpoint)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with httpx.Client(
            base_url=self._base,
            headers=self._headers,
            timeout=self._timeout,
            trust_env=self._trust_env,
        ) as client:
            response = client.get(path, params=params)
            response.raise_for_status()
            return response.json()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(
            base_url=self._base,
            headers=self._headers,
            timeout=self._timeout,
            trust_env=self._trust_env,
        ) as client:
            response = client.post(path, json=payload)
            if response.status_code == 409:
                detail = response.json().get("detail") or {}
                raise MemoryNotAdmitted(detail)
            response.raise_for_status()
            return response.json()

    def user_context(self, limit_per_type: int = 5) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for memory_type in ("fact", "goal", "skill", "preference"):
            # Profile recall is a delivery surface, not an administrative list.
            # Read a bounded candidate window, then fail closed unless the
            # governance result explicitly allows delivery. This prevents
            # source-only indexes, test artifacts, and unresolved inferences
            # from leaking into a normal Agent profile answer.
            candidates = self._get(
                "/memories",
                {"type": memory_type, "limit": min(100, max(limit_per_type * 10, limit_per_type))},
            ).get("items", [])
            eligible = [
                item
                for item in candidates
                if isinstance(item, dict)
                and isinstance(item.get("review_triage"), dict)
                and item["review_triage"].get("delivery_eligible") is True
            ]
            items.extend(eligible[:limit_per_type])
        return items

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return self._get("/memories", {"search": query, "limit": limit}).get("items", [])

    def context(
        self,
        query: str,
        *,
        namespaces: list[str] | None = None,
        token_budget: int = 1600,
        limit: int = 10,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "token_budget": token_budget,
            "limit": limit,
        }
        if namespaces is not None:
            payload["namespaces"] = namespaces
        return self._post("/memories/context", payload)

    def remember(
        self,
        memory_type: str,
        statement: str,
        reason: str | None = None,
        transfer: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": memory_type,
            "statement": statement,
            "reason": reason,
        }
        if transfer is not None:
            payload["transfer"] = transfer
        return self._post("/memories", payload)


def resolve_connection() -> tuple[str, str]:
    """按环境变量 → connection.json → 默认地址的顺序解析连接信息。"""
    endpoint = os.environ.get("PERSONALMEMORY_ENDPOINT", "").strip()
    token = os.environ.get("PERSONALMEMORY_TOKEN", "").strip()
    if endpoint and token:
        return endpoint, token

    data_dir = os.environ.get("PERSONALMEMORY_DATA_DIR", "").strip()
    data_dir_path = Path(data_dir) if data_dir else default_data_dir()
    try:
        from personalmemory.infrastructure.runtime_connection import (
            read_runtime_connection,
        )

        discovered = read_runtime_connection(data_dir_path)
    except OSError:
        discovered = None
    if discovered is not None:
        endpoint = endpoint or discovered["endpoint"]
        token = token or discovered["token"]
        if endpoint and token:
            return endpoint, token
    return endpoint or DEFAULT_ENDPOINT, token


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_user_context",
        "description": (
            "返回紧凑的稳定用户上下文：每类最多 5 条事实、目标、能力、偏好。"
            "回答简单的用户背景或偏好问题时调用。复杂项目任务优先调用 memory_context。"
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "memory_context",
        "description": (
            "在处理非简单任务前，为当前任务编译有界的长期上下文。返回相关目标、"
            "决策、洞察、经验和偏好，并附选择原因、Revision 与来源；不确定时返回空集合，"
            "不得补造事实。涉及项目方案、历史原因、架构取向或用户既有经验时优先调用。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "当前任务或用户问题"},
                "namespaces": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 20,
                    "description": "可选的记忆命名空间；省略时检索当前授权空间",
                },
                "token_budget": {
                    "type": "integer",
                    "minimum": 128,
                    "maximum": MAX_CONTEXT_TOKEN_BUDGET,
                    "default": 1600,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_memories",
        "description": (
            "按关键词检索用户的长期记忆（FTS），返回类型、语句、原因与命名空间。"
            "用于查找用户过去的目标、决策、经验与洞察。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词，多个词自动 AND"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
        },
    },
    {
        "name": "remember",
        "description": (
            "把本次对话形成的一条长期记忆写入 PersonalMemory。只写长期有效的"
            "信息（用户事实/目标/能力/偏好/经历/洞察/决策），不写临时任务细节；"
            "推荐附上形成原因。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": list(MEMORY_TYPES),
                    "description": "记忆类型",
                },
                "statement": {"type": "string", "description": "记忆内容"},
                "reason": {"type": "string", "description": "形成原因（可选但推荐）"},
                "transfer": {
                    "type": "object",
                    "description": (
                        "可选的经验迁移边界；仅 decision/insight/event 可用。"
                        "只有 scope=cross_project 才允许被其他项目作为类比候选。"
                    ),
                    "additionalProperties": False,
                    "properties": {
                        "scope": {
                            "type": "string",
                            "enum": ["project", "cross_project"],
                        },
                        "problem_shape": {"type": "string", "maxLength": 500},
                        "principle": {"type": "string", "maxLength": 500},
                        "applies_when": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 240},
                            "maxItems": 8,
                        },
                        "avoid_when": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 240},
                            "maxItems": 8,
                        },
                    },
                    "required": ["scope", "problem_shape", "principle"],
                },
            },
            "required": ["type", "statement"],
        },
    },
]


def render_context(items: list[dict[str, Any]]) -> str:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(item["type"], []).append(item)
    if not grouped:
        return "（暂无长期记忆。用户正在从零开始。）"
    lines: list[str] = []
    for memory_type in ("fact", "goal", "skill", "preference"):
        for item in grouped.get(memory_type, []):
            reason = f"（原因：{item['reason']}）" if item.get("reason") else ""
            lines.append(f"- [{item['type']}] {item['statement']}{reason}")
    return "\n".join(lines)


def call_tool(
    name: str,
    arguments: dict[str, Any],
    client: MemoryClient,
    *,
    propagate_reconnect_errors: bool = False,
) -> dict[str, Any]:
    arguments = arguments or {}
    try:
        if name == "get_user_context":
            text = render_context(client.user_context())
        elif name == "memory_context":
            query = str(arguments.get("query", "")).strip()
            if not query:
                raise ValueError("query 不能为空")
            namespaces = arguments.get("namespaces")
            if namespaces is not None and (
                not isinstance(namespaces, list)
                or not namespaces
                or any(not isinstance(value, str) or not value.strip() for value in namespaces)
            ):
                raise ValueError("namespaces 必须是非空字符串数组")
            token_budget = int(arguments.get("token_budget", 1600))
            limit = int(arguments.get("limit", 10))
            if not 128 <= token_budget <= MAX_CONTEXT_TOKEN_BUDGET:
                raise ValueError(f"token_budget 必须在 128 到 {MAX_CONTEXT_TOKEN_BUDGET} 之间")
            if not 1 <= limit <= 10:
                raise ValueError("limit 必须在 1 到 10 之间")
            bundle = client.context(
                query,
                namespaces=namespaces,
                token_budget=token_budget,
                limit=limit,
            )
            text = json.dumps(bundle, ensure_ascii=False, indent=2)
        elif name == "search_memories":
            query = str(arguments.get("query", "")).strip()
            if not query:
                raise ValueError("query 不能为空")
            limit = int(arguments.get("limit", 10))
            if not 1 <= limit <= 50:
                raise ValueError("limit 必须在 1 到 50 之间")
            text = json.dumps(client.search(query, limit), ensure_ascii=False, indent=2)
        elif name == "remember":
            memory_type = str(arguments.get("type", ""))
            statement = str(arguments.get("statement", "")).strip()
            if memory_type not in MEMORY_TYPES:
                raise ValueError(f"type 必须是 {', '.join(MEMORY_TYPES)} 之一")
            if not statement:
                raise ValueError("statement 不能为空")
            transfer = arguments.get("transfer")
            if transfer is not None and not isinstance(transfer, dict):
                raise ValueError("transfer 必须是对象")
            result = client.remember(
                memory_type,
                statement,
                arguments.get("reason"),
                transfer,
            )
            text = json.dumps(result, ensure_ascii=False, indent=2)
        else:
            return {"content": [], "isError": True, "error": f"未知工具：{name}"}
        return {"content": [{"type": "text", "text": text}], "isError": False}
    except MemoryNotAdmitted as error:
        return {
            "content": [{"type": "text", "text": f"记忆未被接纳：{error.detail}"}],
            "isError": True,
        }
    except httpx.ConnectError:
        if propagate_reconnect_errors:
            raise
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "无法连接到 PersonalMemory Service。若使用桌面版，请确认桌面端已启动；"
                        "如果刚重启过桌面端，请重试，MCP 会自动刷新本机连接。"
                    ),
                }
            ],
            "isError": True,
        }
    except httpx.HTTPStatusError as error:
        if propagate_reconnect_errors and error.response.status_code == 401:
            # The local Service rotates its session token after a restart. A
            # 401 means the request was rejected before any tool-side effect,
            # so it is safe to reload connection.json and retry once per
            # bounded reconnect attempt.
            raise
        if error.response.status_code == 401:
            text = "PersonalMemory Service 拒绝了当前会话凭据；请确认桌面端已启动后重试。"
        else:
            text = f"调用失败：PersonalMemory Service 返回 HTTP {error.response.status_code}。"
        return {"content": [{"type": "text", "text": text}], "isError": True}
    except httpx.TimeoutException:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "连接 PersonalMemory Service 超时，请检查服务状态后重试。",
                }
            ],
            "isError": True,
        }
    except Exception as error:  # noqa: BLE001 - 边界外错误统一以 isError 返回给 Agent
        return {
            "content": [{"type": "text", "text": f"调用失败：{error}"}],
            "isError": True,
        }


def handle_message(
    message: dict[str, Any], client: MemoryClient | None = None
) -> dict[str, Any] | None:
    method = message.get("method")
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        # The desktop Service rotates its loopback port and session token on
        # every start. Resolve the runtime connection at call time so a
        # long-lived MCP stdio process follows Service restarts instead of
        # retaining a stale endpoint from its own startup.
        params = message.get("params") or {}
        tool_name = str(params.get("name", ""))
        arguments = params.get("arguments") or {}
        if client is None:
            # Keep the stdio session alive across a Service restart/update. A
            # refused loopback connection is safe to retry: no HTTP request was
            # accepted. Resolve connection.json on every attempt because the
            # restarted Service rotates both its port and session token.
            result = None
            for delay in (*SERVICE_RECONNECT_DELAYS, None):
                endpoint, token = resolve_connection()
                if not token:
                    if delay is None:
                        result = {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "尚未发现 PersonalMemory Service 的本机会话；"
                                        "MCP 已等待并重试，请启动桌面端后再试。"
                                    ),
                                }
                            ],
                            "isError": True,
                        }
                        break
                    time.sleep(delay)
                    continue
                current_client = MemoryClient(endpoint, token)
                try:
                    result = call_tool(
                        tool_name,
                        arguments,
                        current_client,
                        propagate_reconnect_errors=True,
                    )
                    break
                except httpx.ConnectError:
                    if delay is None:
                        result = {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "PersonalMemory Service 暂时不可用；"
                                        "MCP 已自动重连多次仍未恢复。"
                                        "请保持桌面端运行后重试。"
                                    ),
                                }
                            ],
                            "isError": True,
                        }
                        break
                    time.sleep(delay)
                except httpx.HTTPStatusError as error:
                    if error.response.status_code != 401:
                        raise
                    if delay is None:
                        result = {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "PersonalMemory Service 会话正在更新；"
                                        "MCP 已自动刷新连接多次仍未恢复。"
                                        "请保持桌面端运行后重试。"
                                    ),
                                }
                            ],
                            "isError": True,
                        }
                        break
                    time.sleep(delay)
        else:
            result = call_tool(tool_name, arguments, client)
        if "error" in result:
            return {
                "error": result.pop("error"),
                "result": result,
            }
        return {"result": result}
    if method == "ping":
        return {}
    if isinstance(method, str) and method.startswith("notifications/"):
        return None
    return None


def main() -> None:
    configure_stdio_utf8()
    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        try:
            connector_index = sys.argv.index("--connector-id") + 1
            connector_id = sys.argv[connector_index]
        except (ValueError, IndexError):
            connector_id = ""
        try:
            agent_family_index = sys.argv.index("--agent-family") + 1
            agent_family = sys.argv[agent_family_index]
        except (ValueError, IndexError):
            agent_family = "claude_code"
        if connector_id:
            from personalmemory.agent_hook import run_hook

            if agent_family not in {"claude_code", "codex"}:
                return
            if agent_family == "claude_code":
                sys.exit(run_hook(connector_id))
            sys.exit(run_hook(connector_id, agent_family=agent_family))
        return
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_message(message)
        if response is None:
            continue
        envelope: dict[str, Any] = {"jsonrpc": "2.0", "id": message.get("id")}
        if "error" in response:
            envelope["error"] = {"code": -32603, "message": response.pop("error")}
        elif "result" in response:
            envelope.update(response)
        else:
            envelope["result"] = response
        sys.stdout.write(json.dumps(envelope, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
