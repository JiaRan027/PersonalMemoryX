"""Connector 契约测试套件（v0.3 发布门槛）。

对每个正式 AgentMemoryProfile 独立执行完整生命周期契约：
1. 摄入 + 投影（managed）：同步后产生预期类型与语句的记忆；可写 profile 正确写回 managed block；
2. 幂等：二次同步不重复摄入；
3. 撤销：撤销摄入活动后记忆消失；
4. 删除传播：删除记忆后，即使来源继续变化也不复活；
5. Connector 移除：删除连接器后 Memory Core 数据保持完整；
6. 只读边界：无写能力的 profile 拒绝启用写入（PROFILE_CANNOT_WRITE）；
7. 未知格式降级：未来/未知结构不崩溃、不静默产生垃圾记忆（skip 型 profile accepted=0）；
8. 失败隔离：一个 Connector 同步失败不影响其他 Connector 的数据与可用性。

所有断言基于当场 API 行为（fresh TestClient 实例）。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from service.tests.api.test_api_contract import auth_headers, make_client

MEMORY_MD = r"""# Task Group: Demo

scope: contract demo

## User Profile

The user works on ExampleProject across uniapp and backend.

## User preferences

- Update the project development log in the same change.

## General Tips

- Keep verification status precise.

## What's in Memory

### C:\Users\demo\project

#### 2026-07-25

- ExampleProject comment replies
  - learnings: Node suite reported 7 passed.
"""

CLAUDE_FEEDBACK = """---
name: 始终用中文回答
type: feedback
---
始终用中文回答用户的问题。

**Why:** 用户明确要求使用中文交流。
"""


@dataclass(frozen=True)
class ContractSpec:
    profile_id: str
    namespace: str
    expected_types: frozenset[str]
    markers: tuple[str, ...]
    managed: bool = False
    new_marker: str = ""


def _write_source(profile_id: str, tmp_path: Path) -> Path:
    if profile_id == "generic_markdown_readonly":
        path = tmp_path / "source.md"
        path.write_text(
            "## Fact\n- 用户正在构建 PersonalMemory。\n\n## Goal\n- 进入 AI 行业。\n",
            encoding="utf-8",
        )
        return path
    if profile_id == "generic_markdown_managed":
        path = tmp_path / "source.md"
        path.write_text(
            "## Fact\n- 用户正在构建 PersonalMemory。\n\n## Goal\n- 进入 AI 行业。\n",
            encoding="utf-8",
        )
        return path
    if profile_id == "codex_memory_markdown":
        path = tmp_path / "MEMORY.md"
        path.write_text(MEMORY_MD, encoding="utf-8")
        return path
    if profile_id == "claude_memory_markdown":
        path = tmp_path / "feedback_language.md"
        path.write_text(CLAUDE_FEEDBACK, encoding="utf-8")
        return path
    if profile_id == "codex_jsonl":
        path = tmp_path / "rollout.jsonl"
        path.write_text(
            "\n".join(
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": text}],
                        },
                    },
                    ensure_ascii=False,
                )
                for text in ("我完成了 Agent 项目。", "我决定采用结构化查询优先。")
            ),
            encoding="utf-8",
        )
        return path
    if profile_id == "claude_jsonl":
        path = tmp_path / "history.jsonl"
        path.write_text(
            "\n".join(
                json.dumps(
                    {"type": "user", "message": {"role": "user", "content": text}},
                    ensure_ascii=False,
                )
                for text in ("我完成了 Agent 项目。", "我决定采用结构化查询优先。")
            ),
            encoding="utf-8",
        )
        return path
    if profile_id == "gemini_sqlite":
        path = tmp_path / "conversations.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE conversations (id INTEGER PRIMARY KEY, role TEXT, content TEXT)"
        )
        conn.executemany(
            "INSERT INTO conversations (role, content) VALUES (?, ?)",
            [
                ("user", "我完成了 Agent 项目。"),
                ("user", "我决定采用结构化查询优先。"),
            ],
        )
        conn.commit()
        conn.close()
        return path
    if profile_id == "conversation_text":
        path = tmp_path / "conversation.txt"
        path.write_text("我完成了 Agent 项目。\n我决定采用结构化查询优先。\n", encoding="utf-8")
        return path
    raise AssertionError(f"unknown profile {profile_id}")


def _evolve_source(profile_id: str, path: Path) -> None:
    """把来源改写为包含一条新记忆的版本（用于删除传播契约）。"""
    if profile_id in ("generic_markdown_readonly", "generic_markdown_managed"):
        path.write_text(
            "## Fact\n- 用户正在构建 PersonalMemory。\n\n## Goal\n- 进入 AI 行业。\n"
            "\n## Decision\n- 用户决定采用结构化查询优先。\n",
            encoding="utf-8",
        )
        return
    if profile_id == "codex_memory_markdown":
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text + "\n## User preferences\n\n- 代码中保留英文术语。\n", encoding="utf-8"
        )
        return
    if profile_id == "claude_memory_markdown":
        path.write_text(
            CLAUDE_FEEDBACK + "代码中保留英文术语。\n", encoding="utf-8"
        )
        return
    if profile_id in ("codex_jsonl", "claude_jsonl"):
        text = path.read_text(encoding="utf-8")
        new_line = json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "我掌握 Python。"}],
                },
            },
            ensure_ascii=False,
        )
        path.write_text(text + "\n" + new_line + "\n", encoding="utf-8")
        return
    if profile_id == "conversation_text":
        text = path.read_text(encoding="utf-8")
        path.write_text(text + "\n我掌握 Python。\n", encoding="utf-8")
        return
    if profile_id == "gemini_sqlite":
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO conversations (role, content) VALUES (?, ?)",
            ("user", "我掌握 Python。"),
        )
        conn.commit()
        conn.close()
        return
    raise AssertionError(f"unknown profile {profile_id}")


def _unknown_source(profile_id: str, path: Path) -> None:
    """把来源改写为未来/未知结构的版本（用于降级契约）。"""
    if profile_id in ("generic_markdown_readonly", "generic_markdown_managed"):
        path.write_text("## FutureUnknownSection\n- future stuff\n", encoding="utf-8")
        return
    if profile_id in ("codex_memory_markdown", "claude_memory_markdown"):
        path.write_text(
            "## FutureUnknownSection\n- future stuff\n\n## AnotherUnknown\n- more\n",
            encoding="utf-8",
        )
        return
    if profile_id in ("codex_jsonl", "claude_jsonl"):
        path.write_text(
            json.dumps({"type": "future_item", "payload": {"data": "unknown"}})
            + "\n",
            encoding="utf-8",
        )
        return
    if profile_id == "gemini_sqlite":
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE future_table (future_id INTEGER PRIMARY KEY, payload TEXT)"
        )
        conn.execute("INSERT INTO future_table (payload) VALUES ('unknown')")
        conn.commit()
        conn.close()
        return
    if profile_id == "conversation_text":
        path.write_text("这段内容不属于任何已知规则。\n", encoding="utf-8")
        return
    raise AssertionError(f"unknown profile {profile_id}")


SPECS = (
    ContractSpec(
        profile_id="generic_markdown_readonly",
        namespace="contract_generic",
        expected_types=frozenset({"fact", "goal"}),
        markers=("用户正在构建 PersonalMemory", "进入 AI 行业"),
        new_marker="结构化查询优先",
    ),
    ContractSpec(
        profile_id="generic_markdown_managed",
        namespace="contract_managed",
        expected_types=frozenset({"fact", "goal"}),
        markers=("用户正在构建 PersonalMemory", "进入 AI 行业"),
        managed=True,
        new_marker="结构化查询优先",
    ),
    ContractSpec(
        profile_id="codex_memory_markdown",
        namespace="contract_codex",
        expected_types=frozenset({"fact", "preference"}),
        markers=(
            "ExampleProject across uniapp",
            "development log",
        ),
        new_marker="英文术语",
    ),
    ContractSpec(
        profile_id="claude_memory_markdown",
        namespace="contract_claude",
        expected_types=frozenset({"preference"}),
        markers=("始终用中文回答",),
        new_marker="英文术语",
    ),
    ContractSpec(
        profile_id="codex_jsonl",
        namespace="contract_codex_jsonl",
        expected_types=frozenset({"event", "decision"}),
        markers=("Agent 项目", "结构化查询优先"),
        new_marker="Python",
    ),
    ContractSpec(
        profile_id="claude_jsonl",
        namespace="contract_claude_jsonl",
        expected_types=frozenset({"event", "decision"}),
        markers=("Agent 项目", "结构化查询优先"),
        new_marker="Python",
    ),
    ContractSpec(
        profile_id="gemini_sqlite",
        namespace="contract_gemini",
        expected_types=frozenset({"event", "decision"}),
        markers=("Agent 项目", "结构化查询优先"),
        new_marker="Python",
    ),
    ContractSpec(
        profile_id="conversation_text",
        namespace="contract_conversation",
        expected_types=frozenset({"event", "decision"}),
        markers=("Agent 项目", "结构化查询优先"),
        new_marker="Python",
    ),
)


def _create_connector(client, spec: ContractSpec, source: Path) -> dict:
    target = None
    write_enabled = False
    if spec.managed:
        target = source.parent / "target.md"
        # 受管块标记由用户预先显式声明（PersonalMemory 只写受管块，绝不覆盖块外正文）
        target.write_text(
            "# 目标文件\n用户正文\n"
            "<!-- personalmemory:start -->\n<!-- personalmemory:end -->\n"
            "尾部正文\n",
            encoding="utf-8",
        )
        write_enabled = True
    response = client.post(
        "/api/v1/connectors",
        headers=auth_headers(),
        json={
            "name": f"契约 {spec.profile_id}",
            "profile_id": spec.profile_id,
            "source_path": str(source),
            "target_path": str(target) if target else None,
            "namespace": spec.namespace,
            "read_enabled": True,
            "write_enabled": write_enabled,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _sync(client, connector_id: str) -> dict:
    response = client.post(
        f"/api/v1/connectors/{connector_id}/sync", headers=auth_headers()
    )
    assert response.status_code == 200, response.text
    return response.json()


def _memories(client) -> dict:
    return client.get("/api/v1/memories", headers=auth_headers()).json()


def test_manual_sync_endpoint_only_processes_the_selected_connector(tmp_path):
    first_source = tmp_path / "first.txt"
    second_source = tmp_path / "second.txt"
    first_source.write_text("我决定采用本地优先架构。\n", encoding="utf-8")
    second_source.write_text("我掌握 Rust。\n", encoding="utf-8")

    with make_client(tmp_path) as client:
        connectors = []
        for name, source in (("第一份会话", first_source), ("第二份会话", second_source)):
            response = client.post(
                "/api/v1/connectors",
                headers=auth_headers(),
                json={
                    "name": name,
                    "profile_id": "conversation_text",
                    "source_path": str(source),
                    "target_path": None,
                    "namespace": "general",
                    "read_enabled": True,
                    "write_enabled": False,
                },
            )
            assert response.status_code == 201, response.text
            connectors.append(response.json())

        assert connectors[0]["background_sync"] is False
        first_sync = client.post(
            f"/api/v1/connectors/{connectors[0]['id']}/sync",
            headers=auth_headers(),
        )
        assert first_sync.status_code == 200, first_sync.text
        assert first_sync.json()["ingested"] == 1
        statements = " ".join(item["statement"] for item in _memories(client)["items"])
        assert "本地优先架构" in statements
        assert "Rust" not in statements

        second_sync = client.post(
            f"/api/v1/connectors/{connectors[1]['id']}/sync",
            headers=auth_headers(),
        )
        assert second_sync.status_code == 200, second_sync.text
        statements = " ".join(item["statement"] for item in _memories(client)["items"])
        assert "Rust" in statements


@pytest.mark.parametrize("spec", SPECS, ids=[s.profile_id for s in SPECS])
def test_contract_ingest_projection_and_idempotency(tmp_path, spec):
    """摄入 + managed 投影 + 二次同步幂等。"""
    source = _write_source(spec.profile_id, tmp_path)
    with make_client(tmp_path) as client:
        connector = _create_connector(client, spec, source)
        first = _sync(client, connector["id"])
        assert first["accepted"] >= 1, first
        assert first["failed"] == 0, first

        memories = _memories(client)
        assert memories["total"] >= 1
        types = {item["type"] for item in memories["items"]}
        assert types == spec.expected_types, types
        statements = " ".join(item["statement"] for item in memories["items"])
        for marker in spec.markers:
            assert marker in statements, f"missing marker: {marker}"

        if spec.managed:
            # Source imports are provisional by default; only explicitly
            # confirmed memories may enter a managed Agent projection.
            from personalmemory.infrastructure.database.models import MemoryObjectRecord
            from sqlalchemy import update

            with client.app.state.database.session_factory.begin() as session:
                session.execute(
                    update(MemoryObjectRecord).where(
                        MemoryObjectRecord.memory_space_id == "space_default"
                    ).values(
                        epistemic_state="asserted",
                        revision=MemoryObjectRecord.revision + 1,
                    )
                )

        second = _sync(client, connector["id"])
        assert second["accepted"] == 0, second
        if spec.managed:
            assert second["projected"] == 1, second
            target = Path(connector["target_path"])
            assert target.exists()
            projected = target.read_text(encoding="utf-8")
            assert "<!-- personalmemory:start -->" in projected
            assert "用户正在构建 PersonalMemory" in projected
            assert "用户正文" in projected
            assert "尾部正文" in projected
        assert _memories(client)["total"] == memories["total"]


@pytest.mark.parametrize("spec", SPECS, ids=[s.profile_id for s in SPECS])
def test_contract_undo_reverts_ingestion(tmp_path, spec):
    source = _write_source(spec.profile_id, tmp_path)
    with make_client(tmp_path) as client:
        connector = _create_connector(client, spec, source)
        _sync(client, connector["id"])
        assert _memories(client)["total"] >= 1

        activities = client.get("/api/v1/activities", headers=auth_headers()).json()[
            "items"
        ]
        ingested = [a for a in activities if a["kind"] == "source_ingested"]
        assert ingested, activities
        undone = client.post(
            f"/api/v1/activities/{ingested[0]['operation_id']}/undo",
            headers=auth_headers(),
        )
        assert undone.status_code == 200, undone.text
        assert undone.json()["undone"] >= 1
        assert _memories(client)["total"] == 0


@pytest.mark.parametrize("spec", SPECS, ids=[s.profile_id for s in SPECS])
def test_contract_delete_blocks_resurrection(tmp_path, spec):
    source = _write_source(spec.profile_id, tmp_path)
    with make_client(tmp_path) as client:
        connector = _create_connector(client, spec, source)
        _sync(client, connector["id"])
        items = _memories(client)["items"]
        assert items
        for item in items:
            deleted = client.delete(
                f"/api/v1/memories/{item['id']}", headers=auth_headers()
            )
            assert deleted.status_code == 204, deleted.text

        # 来源继续变化（新增一条记忆），被删除的记忆不得复活
        _evolve_source(spec.profile_id, source)
        _sync(client, connector["id"])
        memories = _memories(client)
        statements = " ".join(item["statement"] for item in memories["items"])
        for marker in spec.markers:
            assert marker not in statements, f"deleted memory resurrected: {marker}"
        if spec.new_marker:
            assert spec.new_marker in statements, f"new memory missing: {spec.new_marker}"


@pytest.mark.parametrize("spec", SPECS, ids=[s.profile_id for s in SPECS])
def test_contract_removal_keeps_core_data(tmp_path, spec):
    source = _write_source(spec.profile_id, tmp_path)
    with make_client(tmp_path) as client:
        connector = _create_connector(client, spec, source)
        _sync(client, connector["id"])
        total_before = _memories(client)["total"]
        assert total_before >= 1

        removed = client.delete(
            f"/api/v1/connectors/{connector['id']}", headers=auth_headers()
        )
        assert removed.status_code == 204, removed.text
        assert _memories(client)["total"] == total_before


@pytest.mark.parametrize("spec", SPECS, ids=[s.profile_id for s in SPECS])
def test_contract_unknown_format_degrades_gracefully(tmp_path, spec):
    """未来/未知结构不崩溃；skip 型 profile 不产生垃圾记忆。"""
    from personalmemory.domain.connector.profile import get_builtin_profile

    source = _write_source(spec.profile_id, tmp_path)
    with make_client(tmp_path) as client:
        connector = _create_connector(client, spec, source)
        _sync(client, connector["id"])
        _unknown_source(spec.profile_id, source)
        result = _sync(client, connector["id"])
        assert "accepted" in result

        profile = get_builtin_profile(spec.profile_id)
        skip_unknown = spec.profile_id in (
            "codex_memory_markdown",
            "claude_memory_markdown",
            "codex_jsonl",
            "claude_jsonl",
            "gemini_sqlite",
            "conversation_text",
        )
        if skip_unknown and not profile.capabilities.write_memory_file:
            assert result["accepted"] == 0, result


@pytest.mark.parametrize("spec", SPECS, ids=[s.profile_id for s in SPECS])
def test_contract_readonly_profiles_refuse_writes(tmp_path, spec):
    """无写能力的 profile：启用写入必须被拒绝（不安全写出防线）。"""
    from personalmemory.domain.connector.profile import get_builtin_profile

    profile = get_builtin_profile(spec.profile_id)
    source = _write_source(spec.profile_id, tmp_path)
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/connectors",
            headers=auth_headers(),
            json={
                "name": "只读越权",
                "profile_id": spec.profile_id,
                "source_path": str(source),
                "target_path": str(source.parent / "target.md"),
                "namespace": spec.namespace,
                "read_enabled": True,
                "write_enabled": True,
            },
        )
        if profile.capabilities.write_memory_file:
            assert response.status_code == 201, response.text
        else:
            assert response.status_code == 400, response.text
            assert response.json()["detail"]["code"] == "PROFILE_CANNOT_WRITE"


@pytest.mark.parametrize("spec", SPECS, ids=[s.profile_id for s in SPECS])
def test_contract_failure_isolation(tmp_path, spec):
    """一个 Connector 失败不影响另一个 Connector 的数据与可用性。"""
    good_source = _write_source(spec.profile_id, tmp_path)
    with make_client(tmp_path) as client:
        good = _create_connector(client, spec, good_source)
        _sync(client, good["id"])
        assert _memories(client)["total"] >= 1

        # B：损坏的 SQLite 来源（同步返回 failed=1，不抛异常）
        bad = tmp_path / "broken.db"
        bad.write_bytes(b"this is not a sqlite database")
        bad_connector = client.post(
            "/api/v1/connectors",
            headers=auth_headers(),
            json={
                "name": "损坏来源",
                "profile_id": "gemini_sqlite",
                "source_path": str(bad),
                "target_path": None,
                "namespace": "broken",
                "read_enabled": True,
                "write_enabled": False,
            },
        )
        assert bad_connector.status_code == 201, bad_connector.text
        bad_sync = client.post(
            f"/api/v1/connectors/{bad_connector.json()['id']}/sync",
            headers=auth_headers(),
        )
        assert bad_sync.status_code == 200, bad_sync.text
        assert bad_sync.json()["failed"] == 1, bad_sync.text

        # 一个来源失败不影响另一个来源；手动同步只针对明确选择的 Connector。
        assert _memories(client)["total"] >= 1
        resync = _sync(client, good["id"])
        assert resync["accepted"] == 0, resync
        # 好连接器仍能摄入新内容：B 的失败不影响 A 的管线
        _evolve_source(spec.profile_id, good_source)
        after_evolve = _sync(client, good["id"])
        assert after_evolve["accepted"] >= 1, after_evolve
        statements = " ".join(item["statement"] for item in _memories(client)["items"])
        assert spec.new_marker in statements, statements
