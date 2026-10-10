"""POST /api/v1/memories（Agent / 用户主动写入）测试。"""

from fastapi.testclient import TestClient


def make_client(tmp_path):
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


def test_agent_write_creates_memory(tmp_path) -> None:
    with make_client(tmp_path) as client:
        created = client.post(
            "/api/v1/memories",
            headers=auth_headers(),
            json={
                "type": "insight",
                "statement": "结构化信息优先于纯向量检索",
                "reason": "大量个人记忆属于明确事实",
            },
        )
        memories = client.get("/api/v1/memories", headers=auth_headers())

    assert created.status_code == 201
    body = created.json()
    assert body["action"] == "provisional"
    assert body["duplicate"] is False
    assert body["memory"]["type"] == "insight"
    assert body["memory"]["statement"] == "结构化信息优先于纯向量检索"
    assert body["memory"]["reason"] == "大量个人记忆属于明确事实"
    assert memories.json()["total"] == 1


def test_agent_write_is_idempotent_by_semantic_fingerprint(tmp_path) -> None:
    payload = {
        "type": "fact",
        "statement": "用户正在构建 PersonalMemory。",
    }
    with make_client(tmp_path) as client:
        first = client.post("/api/v1/memories", headers=auth_headers(), json=payload)
        second = client.post("/api/v1/memories", headers=auth_headers(), json=payload)
        memories = client.get("/api/v1/memories", headers=auth_headers())

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["memory"]["id"] == first.json()["memory"]["id"]
    assert memories.json()["total"] == 1


def test_agent_written_memories_support_natural_chinese_search(tmp_path) -> None:
    payloads = [
        {
            "type": "goal",
            "statement": "打造个人 AI 记忆基础设施，让用户跨 AI 平台携带自己的长期经验资产。",
            "reason": "这是 PersonalMemory 的长期产品目标。",
        },
        {
            "type": "preference",
            "statement": "喜欢详细、工程化、能落地验证的分析与协作方式。",
            "reason": "项目 review 与开发协作中持续体现的偏好。",
        },
        {
            "type": "decision",
            "statement": "PersonalMemory 采用自部署优先路线，而不是以 SaaS 为默认形态。",
            "reason": "保护用户数据主权与可迁移性。",
        },
    ]
    expected_types = {
        "个人 AI 记忆 基础设施": "goal",
        "跨 AI 平台 长期 经验 资产": "goal",
        "详细 工程化 落地 验证": "preference",
        "协作 偏好": "preference",
        "自部署 优先": "decision",
        "数据 主权 可迁移性": "decision",
    }
    with make_client(tmp_path) as client:
        for payload in payloads:
            response = client.post(
                "/api/v1/memories", headers=auth_headers(), json=payload
            )
            assert response.status_code == 201

        results = {
            query: client.get(
                "/api/v1/memories",
                headers=auth_headers(),
                params={"search": query},
            ).json()
            for query in expected_types
        }

    for query, expected_type in expected_types.items():
        assert results[query]["total"] == 1
        assert results[query]["items"][0]["type"] == expected_type


def test_agent_write_rejects_unknown_type(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/memories",
            headers=auth_headers(),
            json={"type": "chat", "statement": "一段聊天"},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "UNKNOWN_MEMORY_TYPE"


def test_agent_write_rejects_empty_statement(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/memories",
            headers=auth_headers(),
            json={"type": "fact", "statement": "   "},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "EMPTY_STATEMENT"


def test_agent_write_requires_session_token(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/memories",
            json={"type": "fact", "statement": "未授权写入"},
        )

    assert response.status_code == 401
