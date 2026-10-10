"""MCP stdio server（Agent 接入闭环）协议与连接发现测试。"""

from __future__ import annotations

import io
import json

import personalmemory
import personalmemory.agent_mcp as mcp
import pytest
from personalmemory.infrastructure.runtime_connection import write_runtime_connection


class FakeMemoryClient:
    def __init__(self, endpoint: str, token: str) -> None:
        self.endpoint = endpoint
        self.token = token

    def user_context(self) -> list[dict[str, object]]:
        return [
            {
                "type": "fact",
                "statement": "用户正在构建 PersonalMemory",
                "reason": None,
            },
            {
                "type": "goal",
                "statement": "进入 AI 行业",
                "reason": "职业方向",
            },
        ]

    def search(self, query: str, limit: int = 10) -> list[dict[str, object]]:
        return [
            {
                "type": "decision",
                "statement": f"选择结构化查询优先（{query}）",
                "reason": None,
            }
        ]

    def context(
        self,
        query: str,
        *,
        namespaces: list[str] | None = None,
        token_budget: int = 1600,
        limit: int = 10,
    ) -> dict[str, object]:
        return {
            "query": query,
            "token_budget": token_budget,
            "estimated_tokens": 42,
            "truncated": False,
            "items": [
                {
                    "id": "memory_decision",
                    "type": "decision",
                    "statement": "PersonalMemory 采用自部署优先路线",
                    "reason": "保护数据主权",
                    "namespace": (namespaces or ["general"])[0],
                    "sensitivity": "personal",
                    "epistemic_state": "asserted",
                    "revision": 1,
                    "selection_reasons": ["query_match", "decision_intent"],
                    "provenance": {"source": "agent_mcp"},
                }
            ],
        }

    def remember(
        self,
        memory_type: str,
        statement: str,
        reason: str | None = None,
        transfer: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "memory": {
                "id": "memory_1",
                "type": memory_type,
                "statement": statement,
                "reason": reason,
                "transfer": transfer,
            },
            "action": "accept",
        }


class RejectingMemoryClient(FakeMemoryClient):
    def remember(
        self,
        memory_type: str,
        statement: str,
        reason: str | None = None,
        transfer: dict[str, object] | None = None,
    ) -> dict[str, object]:
        raise mcp.MemoryNotAdmitted(
            {"code": "MEMORY_NOT_ADMITTED", "reason_code": "TOMBSTONED_MEMORY"}
        )


def make_client() -> FakeMemoryClient:
    return FakeMemoryClient("http://127.0.0.1:19191", "test-token")


def test_initialize_handshake() -> None:
    response = mcp.handle_message({"id": 1, "method": "initialize"}, make_client())

    assert response is not None
    assert response["protocolVersion"] == mcp.PROTOCOL_VERSION
    assert response["serverInfo"]["name"] == "personalmemory"
    assert response["serverInfo"]["version"] == personalmemory.__version__
    assert response["capabilities"] == {"tools": {}}


def test_tools_list_exposes_four_tools() -> None:
    response = mcp.handle_message({"id": 2, "method": "tools/list"}, make_client())

    names = [tool["name"] for tool in response["tools"]]
    assert names == [
        "get_user_context",
        "memory_context",
        "search_memories",
        "remember",
    ]
    remember_schema = response["tools"][3]["inputSchema"]
    assert set(remember_schema["required"]) == {"type", "statement"}


def test_get_user_context_renders_grouped_context() -> None:
    response = mcp.handle_message(
        {
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_user_context", "arguments": {}},
        },
        make_client(),
    )

    assert response["result"]["isError"] is False
    text = response["result"]["content"][0]["text"]
    assert "[fact] 用户正在构建 PersonalMemory" in text
    assert "[goal] 进入 AI 行业（原因：职业方向）" in text


def test_get_user_context_empty_returns_clear_message() -> None:
    class EmptyClient(FakeMemoryClient):
        def user_context(self) -> list[dict[str, object]]:
            return []

    response = mcp.handle_message(
        {
            "id": 4,
            "method": "tools/call",
            "params": {"name": "get_user_context", "arguments": {}},
        },
        EmptyClient("http://127.0.0.1:19191", "t"),
    )

    assert "暂无长期记忆" in response["result"]["content"][0]["text"]


def test_memory_client_profile_recall_excludes_governance_ineligible_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = mcp.MemoryClient("http://127.0.0.1:19191", "test-token")
    calls: list[dict[str, object]] = []

    def fake_get(path: str, params: dict[str, object] | None = None) -> dict[str, object]:
        assert path == "/memories"
        assert params is not None
        calls.append(params)
        memory_type = params["type"]
        return {
            "items": [
                {
                    "type": memory_type,
                    "statement": "来源索引，不应交付",
                    "review_triage": {"delivery_eligible": False},
                },
                {
                    "type": memory_type,
                    "statement": "用户明确确认的背景",
                    "review_triage": {"delivery_eligible": True},
                },
                {"type": memory_type, "statement": "缺少治理结果"},
            ]
        }

    monkeypatch.setattr(client, "_get", fake_get)

    items = client.user_context(limit_per_type=1)

    assert len(items) == 4
    assert all(item["statement"] == "用户明确确认的背景" for item in items)
    assert all(call["limit"] == 10 for call in calls)


def test_memory_context_compiles_task_context_with_budget() -> None:
    response = mcp.handle_message(
        {
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "memory_context",
                "arguments": {
                    "query": "项目架构取向",
                    "namespaces": ["projects/personalmemory"],
                    "token_budget": 900,
                    "limit": 6,
                },
            },
        },
        make_client(),
    )

    assert response["result"]["isError"] is False
    body = json.loads(response["result"]["content"][0]["text"])
    assert body["token_budget"] == 900
    assert body["items"][0]["type"] == "decision"
    assert body["items"][0]["provenance"]["source"] == "agent_mcp"


def test_memory_context_accepts_larger_explicit_budget() -> None:
    tool = next(tool for tool in mcp.TOOLS if tool["name"] == "memory_context")
    assert tool["inputSchema"]["properties"]["token_budget"]["maximum"] == 4096
    response = mcp.handle_message(
        {
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "memory_context",
                "arguments": {"query": "项目架构取向", "token_budget": 4096},
            },
        },
        make_client(),
    )
    assert response["result"]["isError"] is False
    assert json.loads(response["result"]["content"][0]["text"])["token_budget"] == 4096


def test_search_memories_passes_query_and_limit() -> None:
    response = mcp.handle_message(
        {
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "search_memories",
                "arguments": {"query": "向量数据库", "limit": 3},
            },
        },
        make_client(),
    )

    assert response["result"]["isError"] is False
    assert "结构化查询优先（向量数据库）" in response["result"]["content"][0]["text"]


def test_remember_propagates_type_statement_and_reason() -> None:
    response = mcp.handle_message(
        {
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "remember",
                "arguments": {
                    "type": "insight",
                    "statement": "结构化信息优先于纯向量检索",
                    "reason": "大量个人记忆属于明确事实",
                },
            },
        },
        make_client(),
    )

    assert response["result"]["isError"] is False
    body = json.loads(response["result"]["content"][0]["text"])
    assert body["action"] == "accept"
    assert body["memory"]["type"] == "insight"


def test_remember_propagates_optional_transfer_profile() -> None:
    transfer = {
        "scope": "cross_project",
        "problem_shape": "多个供应商共享业务语义",
        "principle": "稳定核心 + 薄适配层",
        "applies_when": ["接入多个 API"],
        "avoid_when": ["差异就是业务语义"],
    }
    response = mcp.handle_message(
        {
            "id": 61,
            "method": "tools/call",
            "params": {
                "name": "remember",
                "arguments": {
                    "type": "decision",
                    "statement": "平台差异限制在 Translator",
                    "transfer": transfer,
                },
            },
        },
        make_client(),
    )

    body = json.loads(response["result"]["content"][0]["text"])
    assert response["result"]["isError"] is False
    assert body["memory"]["transfer"] == transfer


def test_remember_rejected_returns_tool_error_not_jsonrpc_error() -> None:
    response = mcp.handle_message(
        {
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "remember",
                "arguments": {"type": "fact", "statement": "某条被删除的记忆"},
            },
        },
        RejectingMemoryClient("http://127.0.0.1:19191", "t"),
    )

    assert "error" not in response
    assert response["result"]["isError"] is True
    assert "记忆未被接纳" in response["result"]["content"][0]["text"]


def test_connection_refused_returns_actionable_error_without_os_details() -> None:
    class UnavailableClient(FakeMemoryClient):
        def user_context(self) -> list[dict[str, object]]:
            raise mcp.httpx.ConnectError("[WinError 10061] connection refused")

    response = mcp.handle_message(
        {
            "id": 9,
            "method": "tools/call",
            "params": {"name": "get_user_context", "arguments": {}},
        },
        UnavailableClient("http://127.0.0.1:19421", "test-token"),
    )

    text = response["result"]["content"][0]["text"]
    assert response["result"]["isError"] is True
    assert "无法连接到 PersonalMemory Service" in text
    assert "重试" in text
    assert "WinError" not in text


def test_mcp_call_recovers_after_service_restarts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections = iter(
        [
            ("http://127.0.0.1:19421", "stopped-session"),
            ("http://127.0.0.1:19422", "new-session"),
        ]
    )

    class RestartingClient(FakeMemoryClient):
        def user_context(self) -> list[dict[str, object]]:
            if self.endpoint.endswith(":19421"):
                raise mcp.httpx.ConnectError("connection refused")
            return super().user_context()

    monkeypatch.setattr(mcp, "resolve_connection", lambda: next(connections))
    monkeypatch.setattr(mcp, "MemoryClient", RestartingClient)
    monkeypatch.setattr(mcp.time, "sleep", lambda _delay: None)
    message = {
        "id": 10,
        "method": "tools/call",
        "params": {"name": "get_user_context", "arguments": {}},
    }

    recovered = mcp.handle_message(message)

    assert recovered["result"]["isError"] is False
    assert "用户正在构建 PersonalMemory" in recovered["result"]["content"][0]["text"]


def test_mcp_retries_during_service_restart_before_returning_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections = iter(
        [
            ("http://127.0.0.1:19421", "old-session"),
            ("http://127.0.0.1:19422", "new-session"),
        ]
    )
    attempts: list[str] = []

    class RestartingClient(FakeMemoryClient):
        def user_context(self) -> list[dict[str, object]]:
            attempts.append(self.endpoint)
            if self.endpoint.endswith(":19421"):
                raise mcp.httpx.ConnectError("connection refused")
            return super().user_context()

    monkeypatch.setattr(mcp, "resolve_connection", lambda: next(connections))
    monkeypatch.setattr(mcp, "MemoryClient", RestartingClient)
    monkeypatch.setattr(mcp.time, "sleep", lambda _delay: None)

    response = mcp.handle_message(
        {
            "id": 11,
            "method": "tools/call",
            "params": {"name": "get_user_context", "arguments": {}},
        }
    )

    assert response["result"]["isError"] is False
    assert attempts == ["http://127.0.0.1:19421", "http://127.0.0.1:19422"]


def test_mcp_refreshes_rotated_session_token_after_service_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections = iter(
        [
            ("http://127.0.0.1:19421", "old-session"),
            ("http://127.0.0.1:19421", "new-session"),
        ]
    )
    tokens: list[str] = []

    class RotatingTokenClient(FakeMemoryClient):
        def user_context(self) -> list[dict[str, object]]:
            tokens.append(self.token)
            if self.token == "old-session":
                request = mcp.httpx.Request("GET", self.endpoint)
                response = mcp.httpx.Response(401, request=request)
                raise mcp.httpx.HTTPStatusError("unauthorized", request=request, response=response)
            return super().user_context()

    monkeypatch.setattr(mcp, "resolve_connection", lambda: next(connections))
    monkeypatch.setattr(mcp, "MemoryClient", RotatingTokenClient)
    monkeypatch.setattr(mcp.time, "sleep", lambda _delay: None)

    response = mcp.handle_message(
        {
            "id": 13,
            "method": "tools/call",
            "params": {"name": "get_user_context", "arguments": {}},
        }
    )

    assert response["result"]["isError"] is False
    assert tokens == ["old-session", "new-session"]


def test_mcp_waits_for_service_connection_file_during_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections = iter(
        [
            ("http://127.0.0.1:19421", ""),
            ("http://127.0.0.1:19421", "new-session"),
        ]
    )
    monkeypatch.setattr(mcp, "resolve_connection", lambda: next(connections))
    monkeypatch.setattr(mcp.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(mcp, "MemoryClient", FakeMemoryClient)

    response = mcp.handle_message(
        {
            "id": 14,
            "method": "tools/call",
            "params": {"name": "get_user_context", "arguments": {}},
        }
    )

    assert response["result"]["isError"] is False


def test_mcp_connection_retry_is_bounded_and_does_not_leak_os_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    class UnavailableClient(FakeMemoryClient):
        def user_context(self) -> list[dict[str, object]]:
            raise mcp.httpx.ConnectError("[WinError 10061] connection refused")

    monkeypatch.setattr(
        mcp, "resolve_connection", lambda: ("http://127.0.0.1:19421", "test-token")
    )
    monkeypatch.setattr(mcp, "MemoryClient", UnavailableClient)
    monkeypatch.setattr(mcp.time, "sleep", sleeps.append)

    response = mcp.handle_message(
        {
            "id": 12,
            "method": "tools/call",
            "params": {"name": "get_user_context", "arguments": {}},
        }
    )

    text = response["result"]["content"][0]["text"]
    assert response["result"]["isError"] is True
    assert "自动重连多次仍未恢复" in text
    assert "WinError" not in text
    assert sleeps == list(mcp.SERVICE_RECONNECT_DELAYS)


def test_unknown_tool_returns_jsonrpc_error_envelope() -> None:
    response = mcp.handle_message(
        {
            "id": 8,
            "method": "tools/call",
            "params": {"name": "no_such_tool", "arguments": {}},
        },
        make_client(),
    )

    assert response["error"] == "未知工具：no_such_tool"
    assert response["result"]["isError"] is True


def test_notifications_produce_no_response() -> None:
    response = mcp.handle_message(
        {"method": "notifications/initialized", "params": {}}, make_client()
    )
    assert response is None


def test_resolve_connection_env_vars_win(monkeypatch) -> None:
    monkeypatch.setenv("PERSONALMEMORY_ENDPOINT", "http://127.0.0.1:9999")
    monkeypatch.setenv("PERSONALMEMORY_TOKEN", "env-token")
    monkeypatch.setenv("PERSONALMEMORY_DATA_DIR", "C:/unused")

    endpoint, token = mcp.resolve_connection()

    assert endpoint == "http://127.0.0.1:9999"
    assert token == "env-token"


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://127.0.0.1:19191", False),
        ("http://127.0.0.2:19191", False),
        ("http://localhost:19191", False),
        ("http://[::1]:19191", False),
        ("https://memory.example.com", True),
    ],
)
def test_loopback_service_does_not_inherit_system_proxy(
    endpoint: str, expected: bool
) -> None:
    assert mcp.trust_environment_proxy(endpoint) is expected


def test_resolve_connection_falls_back_to_runtime_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PERSONALMEMORY_ENDPOINT", raising=False)
    monkeypatch.delenv("PERSONALMEMORY_TOKEN", raising=False)
    monkeypatch.setenv("PERSONALMEMORY_DATA_DIR", str(tmp_path))
    write_runtime_connection(tmp_path, "127.0.0.1", 19421, "session-secret")

    endpoint, token = mcp.resolve_connection()

    assert endpoint == "http://127.0.0.1:19421"
    assert token == "session-secret"


def test_main_reports_missing_connection_when_tool_is_called(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("PERSONALMEMORY_ENDPOINT", raising=False)
    monkeypatch.delenv("PERSONALMEMORY_TOKEN", raising=False)
    monkeypatch.setenv("PERSONALMEMORY_DATA_DIR", str(tmp_path))
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "get_user_context", "arguments": {}},
            }
        ),
    ]
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(lines) + "\n"))
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdout", stdout)

    mcp.main()

    responses = [json.loads(line) for line in stdout.getvalue().strip().splitlines()]
    assert responses[0]["result"]["serverInfo"]["name"] == "personalmemory"
    assert responses[1]["result"]["isError"] is True
    result_text = responses[1]["result"]["content"][0]["text"]
    assert "尚未发现 PersonalMemory Service 的本机会话" in result_text


def test_configure_stdio_utf8_reconfigures_windows_code_page_streams(
    monkeypatch,
) -> None:
    class ReconfigurableStream(io.StringIO):
        def __init__(self) -> None:
            super().__init__()
            self.configuration: dict[str, str] | None = None

        def reconfigure(self, **kwargs: str) -> None:
            self.configuration = kwargs

    stdin = ReconfigurableStream()
    stdout = ReconfigurableStream()
    stderr = ReconfigurableStream()
    monkeypatch.setattr("sys.stdin", stdin)
    monkeypatch.setattr("sys.stdout", stdout)
    monkeypatch.setattr("sys.stderr", stderr)

    mcp.configure_stdio_utf8()

    expected = {"encoding": "utf-8", "errors": "strict"}
    assert stdin.configuration == expected
    assert stdout.configuration == expected
    assert stderr.configuration == expected


def test_main_stdio_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PERSONALMEMORY_TOKEN", "test-token")
    monkeypatch.setenv("PERSONALMEMORY_ENDPOINT", "http://127.0.0.1:19191")
    monkeypatch.setattr(mcp, "MemoryClient", FakeMemoryClient)
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "get_user_context", "arguments": {}},
            }
        ),
    ]
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(lines) + "\n"))
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdout", stdout)

    mcp.main()

    responses = [json.loads(line) for line in stdout.getvalue().strip().splitlines()]
    assert len(responses) == 2
    assert responses[0]["id"] == 1
    assert responses[0]["result"]["serverInfo"]["name"] == "personalmemory"
    assert responses[1]["id"] == 2
    assert "用户正在构建 PersonalMemory" in responses[1]["result"]["content"][0]["text"]


def test_tool_calls_refresh_runtime_connection_between_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections = iter(
        [
            ("http://127.0.0.1:19421", "first-session"),
            ("http://127.0.0.1:19422", "second-session"),
        ]
    )
    clients: list[FakeMemoryClient] = []

    def make_current_client(endpoint: str, token: str) -> FakeMemoryClient:
        client = FakeMemoryClient(endpoint, token)
        clients.append(client)
        return client

    monkeypatch.setattr(mcp, "resolve_connection", lambda: next(connections))
    monkeypatch.setattr(mcp, "MemoryClient", make_current_client)
    message = {
        "id": 1,
        "method": "tools/call",
        "params": {"name": "get_user_context", "arguments": {}},
    }

    first = mcp.handle_message(message)
    second = mcp.handle_message(message)

    assert first["result"]["isError"] is False
    assert second["result"]["isError"] is False
    assert [(client.endpoint, client.token) for client in clients] == [
        ("http://127.0.0.1:19421", "first-session"),
        ("http://127.0.0.1:19422", "second-session"),
    ]
