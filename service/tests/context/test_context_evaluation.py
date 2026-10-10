from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

EVAL_SET = Path(__file__).parent / "fixtures" / "context_eval_set.jsonl"


def make_client(tmp_path) -> TestClient:
    from personalmemory.api.app import create_app
    from personalmemory.settings import Settings

    settings = Settings(
        data_dir=tmp_path / "data",
        api_token="desktop-session-token",
        enable_background_sync=False,
    )
    return TestClient(create_app(settings))


def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer desktop-session-token"}


def load_cases() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in EVAL_SET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


SEED_MEMORIES = [
    {
        "type": "goal",
        "statement": "打造个人 AI 记忆基础设施，让用户跨 AI 平台携带自己的长期经验资产。",
        "reason": "这是 PersonalMemory 的长期产品目标。",
        "namespace": "projects/personalmemory",
    },
    {
        "type": "preference",
        "statement": "喜欢详细、工程化、能落地验证的分析与协作方式。",
        "reason": "项目 review 与开发协作中持续体现的偏好。",
        "namespace": "general",
    },
    {
        "type": "decision",
        "statement": "PersonalMemory 采用自部署优先路线，而不是以 SaaS 为默认形态。",
        "reason": "保护用户数据主权与可迁移性。",
        "namespace": "projects/personalmemory",
    },
    {
        "type": "insight",
        "statement": "Memory 系统的核心不是保存聊天，而是保存可迁移的经验、决策和认知。",
        "reason": "聊天记录只是来源，不是长期资产本身。",
        "namespace": "projects/personalmemory",
    },
    {
        "type": "decision",
        "statement": "当前暂停商业化和宽泛架构扩展。",
        "reason": "先验证 Context 交付质量，再扩大产品范围。",
        "namespace": "projects/personalmemory",
    },
    {
        "type": "event",
        "statement": "纯向量检索难以表达明确决策和原因。",
        "reason": "此前的方案评估暴露了可解释性不足。",
        "namespace": "projects/personalmemory",
    },
    {
        "type": "decision",
        "statement": "另一个项目采用 SaaS 交付。",
        "reason": "该项目没有本地数据主权要求。",
        "namespace": "projects/other",
    },
    {
        "type": "fact",
        "statement": "用户有高血压诊断。",
        "reason": "医疗信息仅用于敏感度边界测试。",
        "namespace": "general",
    },
]


def seed_memories(client: TestClient) -> None:
    for memory in SEED_MEMORIES:
        response = client.post(
            "/api/v1/memories", headers=auth_headers(), json=memory
        )
        assert response.status_code == 201


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: str(case["id"]))
def test_context_evaluation_set(tmp_path, case: dict[str, object]) -> None:
    with make_client(tmp_path) as client:
        seed_memories(client)
        response = client.post(
            "/api/v1/memories/context",
            headers=auth_headers(),
            json={
                "query": case["query"],
                "namespaces": case["namespaces"],
                "token_budget": 800,
                "limit": 6,
            },
        )

    assert response.status_code == 200
    body = response.json()
    rendered = "\n".join(
        f"{item['statement']} {item.get('reason') or ''}" for item in body["items"]
    )
    for expected in case["expected"]:
        assert expected in rendered
    for forbidden in case["forbidden"]:
        assert forbidden not in rendered
    assert body["estimated_tokens"] <= body["token_budget"]
    for item in body["items"]:
        assert item["selection_reasons"]
        assert item["revision"] >= 1
        assert "provenance" in item


def test_context_respects_allowed_types_and_whole_object_budget(tmp_path) -> None:
    with make_client(tmp_path) as client:
        seed_memories(client)
        oversized_statement = "预算边界决策：" + "必须保留完整原因，不能截断权威记忆。" * 20
        created = client.post(
            "/api/v1/memories",
            headers=auth_headers(),
            json={
                "type": "decision",
                "statement": oversized_statement,
                "namespace": "projects/personalmemory",
            },
        )
        assert created.status_code == 201
        response = client.post(
            "/api/v1/memories/context",
            headers=auth_headers(),
            json={
                "query": "预算边界决策和 PersonalMemory 架构取向",
                "namespaces": ["projects/personalmemory"],
                "allowed_types": ["decision"],
                "token_budget": 128,
                "limit": 10,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert {item["type"] for item in body["items"]} <= {"decision"}
    assert body["estimated_tokens"] <= 128
    assert body["truncated"] is True
    assert oversized_statement not in {item["statement"] for item in body["items"]}


def test_context_rejects_unknown_memory_type(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/memories/context",
            headers=auth_headers(),
            json={"query": "项目", "allowed_types": ["chat"]},
        )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "override",
    [
        {"token_budget": 4097},
        {"limit": 11},
    ],
)
def test_context_rejects_agent_request_above_server_budget(
    tmp_path, override: dict[str, int]
) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/memories/context",
            headers=auth_headers(),
            json={"query": "项目架构", **override},
        )

    assert response.status_code == 422


def test_context_accepts_larger_explicit_budget(tmp_path) -> None:
    with make_client(tmp_path) as client:
        seed_memories(client)
        response = client.post(
            "/api/v1/memories/context",
            headers=auth_headers(),
            json={"query": "PersonalMemory 项目架构", "token_budget": 4096},
        )

    assert response.status_code == 200
    assert response.json()["token_budget"] == 4096
    assert response.json()["estimated_tokens"] <= 4096


def test_context_without_namespaces_searches_authorized_space(tmp_path) -> None:
    with make_client(tmp_path) as client:
        seed_memories(client)
        response = client.post(
            "/api/v1/memories/context",
            headers=auth_headers(),
            json={"query": "PersonalMemory 为什么选择自部署？", "limit": 4},
        )

    assert response.status_code == 200
    statements = {item["statement"] for item in response.json()["items"]}
    assert any("采用自部署优先路线" in statement for statement in statements)


def test_memory_list_applies_database_limit(tmp_path) -> None:
    with make_client(tmp_path) as client:
        for index in range(5):
            response = client.post(
                "/api/v1/memories",
                headers=auth_headers(),
                json={"type": "fact", "statement": f"有界列表记忆 {index}"},
            )
            assert response.status_code == 201
        listed = client.get(
            "/api/v1/memories",
            headers=auth_headers(),
            params={"limit": 2},
        )
        searched = client.get(
            "/api/v1/memories",
            headers=auth_headers(),
            params={"search": "有界 列表 记忆", "limit": 2},
        )

    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 2
    assert listed.json()["total"] == 5
    assert searched.status_code == 200
    assert len(searched.json()["items"]) == 2
    assert searched.json()["total"] == 5


def test_cross_namespace_duplicates_are_visible_but_delivered_once(tmp_path) -> None:
    statement = "用户重视数据主权与本地优先。"
    with make_client(tmp_path) as client:
        first = client.post(
            "/api/v1/memories",
            headers=auth_headers(),
            json={"type": "fact", "statement": statement, "namespace": "general"},
        )
        second = client.post(
            "/api/v1/memories",
            headers=auth_headers(),
            json={
                "type": "fact",
                "statement": statement,
                "namespace": "projects/personalmemory",
            },
        )
        listed = client.get("/api/v1/memories", headers=auth_headers())
        context = client.post(
            "/api/v1/memories/context",
            headers=auth_headers(),
            json={
                "query": "数据主权和本地优先",
                "namespaces": ["projects/personalmemory", "general"],
            },
        )
        exceptions = client.get("/api/v1/exceptions", headers=auth_headers())

    assert first.status_code == 201
    assert second.status_code == 201
    memories = listed.json()["items"]
    assert len(memories) == 2
    assert all(
        any(issue["code"] == "CROSS_NAMESPACE_EXACT_DUPLICATE" for issue in item["quality_issues"])
        for item in memories
    )
    assert [item["statement"] for item in context.json()["items"]].count(statement) == 1
    assert context.json()["automatically_excluded_count"] == 1
    assert context.json()["deduplicated_exact_count"] == 0
    quality_items = [
        item
        for item in exceptions.json()["items"]
        if item["reason_code"] == "CROSS_NAMESPACE_EXACT_DUPLICATE"
    ]
    assert len(quality_items) == 1
    assert quality_items[0]["memory_id"] in {item["id"] for item in memories}
    assert quality_items[0]["related_namespaces"] == [
        "general",
        "projects/personalmemory",
    ]
