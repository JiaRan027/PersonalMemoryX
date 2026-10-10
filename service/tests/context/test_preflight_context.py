from __future__ import annotations

from fastapi.testclient import TestClient


def make_client(tmp_path) -> TestClient:
    from personalmemory.api.app import create_app
    from personalmemory.settings import Settings

    return TestClient(
        create_app(
            Settings(
                data_dir=tmp_path / "data",
                api_token="desktop-session-token",
                enable_background_sync=False,
            )
        )
    )


def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer desktop-session-token"}


def create_connector(client: TestClient, root, namespace: str) -> str:
    response = client.post(
        "/api/v1/connectors",
        headers=auth_headers(),
        json={
            "name": "Preflight reference translator",
            "profile_id": "claude_code_hook",
            "source_path": str(root),
            "target_path": None,
            "namespace": namespace,
            "read_enabled": True,
            "write_enabled": False,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def remember(client: TestClient, payload: dict[str, object]) -> dict[str, object]:
    response = client.post("/api/v1/memories", headers=auth_headers(), json=payload)
    assert response.status_code == 201, response.text
    return response.json()["memory"]


def preflight(client: TestClient, connector_id: str, cwd, query: str):
    return client.post(
        "/api/v1/memories/preflight",
        headers=auth_headers(),
        json={"connector_id": connector_id, "cwd": str(cwd), "query": query},
    )


def test_natural_query_reuses_only_explicit_cross_project_experience(tmp_path) -> None:
    old_root = tmp_path / "personalmemory"
    current_root = tmp_path / "model-evaluation"
    old_root.mkdir()
    current_root.mkdir()

    with make_client(tmp_path) as client:
        connector_id = create_connector(client, current_root, "projects/model-evaluation")
        transferable = remember(
            client,
            {
                "type": "decision",
                "statement": "Continuity Core 保持平台无关，平台差异只进入薄 Translator。",
                "reason": "Agent 可以替换，而连续性内核不能绑定任何厂商。",
                "namespace": "projects/personalmemory",
                "transfer": {
                    "scope": "cross_project",
                    "problem_shape": "多个外部模型供应商格式不同但共享业务语义",
                    "principle": "稳定核心 + 薄适配层",
                    "applies_when": ["接入多个模型供应商", "避免各家 API 判断污染业务核心"],
                    "avoid_when": ["供应商能力差异本身就是业务语义"],
                },
            },
        )
        isolated = remember(
            client,
            {
                "type": "decision",
                "statement": "旧项目的评测结果必须保存为 XML。",
                "namespace": "projects/legacy-evaluation",
            },
        )
        response = preflight(
            client,
            connector_id,
            current_root,
            "我在做一个支持多个模型供应商的评测平台，怎样避免业务代码被各家 API 差异污染？",
        )

    assert response.status_code == 200
    body = response.json()
    ids = {item["id"] for item in body["items"]}
    assert transferable["id"] in ids
    assert isolated["id"] not in ids
    transfer_item = next(item for item in body["items"] if item["id"] == transferable["id"])
    assert transfer_item["match_kind"] == "transfer"
    assert "transfer_overlap" in transfer_item["selection_reasons"]
    assert "稳定核心 + 薄适配层" in body["context"]
    assert "an analogy, not a decision" in body["context"]
    assert body["delivered_tokens"] <= 900
    assert len(body["items"]) <= 6
    assert sum(item["match_kind"] == "transfer" for item in body["items"]) <= 2


def test_unrelated_query_does_not_force_transfer_analogy(tmp_path) -> None:
    current_root = tmp_path / "current"
    current_root.mkdir()
    with make_client(tmp_path) as client:
        connector_id = create_connector(client, current_root, "projects/current")
        remember(
            client,
            {
                "type": "insight",
                "statement": "稳定核心与薄适配层适合处理多个外部模型接口。",
                "namespace": "projects/old",
                "transfer": {
                    "scope": "cross_project",
                    "problem_shape": "多个模型供应商接口不同",
                    "principle": "稳定核心 + 薄适配层",
                    "applies_when": ["接入多个外部 API"],
                    "avoid_when": [],
                },
            },
        )
        response = preflight(client, connector_id, current_root, "帮我设计周末的家庭采购清单")

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["context"] == ""


def test_profile_question_delivers_bounded_general_identity_without_keywords(tmp_path) -> None:
    current_root = tmp_path / "current"
    current_root.mkdir()
    with make_client(tmp_path) as client:
        connector_id = create_connector(client, current_root, "projects/current")
        student = remember(
            client,
            {
                "type": "fact",
                "statement": "用户是一名学生。",
                "namespace": "general",
            },
        )
        preference = remember(
            client,
            {
                "type": "preference",
                "statement": "用户喜欢中文和有证据的工程分析。",
                "namespace": "general",
            },
        )
        unrelated = remember(
            client,
            {
                "type": "event",
                "statement": "昨天完成了一次临时构建。",
                "namespace": "general",
            },
        )
        response = preflight(
            client,
            connector_id,
            current_root,
            "你觉得我是一个什么样的人，说说你对我的了解",
        )

    assert response.status_code == 200
    body = response.json()
    ids = {item["id"] for item in body["items"]}
    assert {student["id"], preference["id"]} <= ids
    assert unrelated["id"] not in ids
    assert all("profile_baseline" in item["selection_reasons"] for item in body["items"])
    assert body["delivered_tokens"] <= 900
    assert len(body["items"]) <= 6


def test_specific_why_question_does_not_expand_to_full_profile(tmp_path) -> None:
    current_root = tmp_path / "current"
    current_root.mkdir()
    with make_client(tmp_path) as client:
        connector_id = create_connector(client, current_root, "projects/current")
        preference = remember(
            client,
            {
                "type": "preference",
                "statement": "用户喜欢中文和详细分析。",
                "namespace": "general",
            },
        )
        decision = remember(
            client,
            {
                "type": "decision",
                "statement": "项目选择 SQLite 作为本地数据库。",
                "namespace": "projects/current",
            },
        )
        response = preflight(client, connector_id, current_root, "你知道我为什么选择 SQLite 吗？")

    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert decision["id"] in ids
    assert preference["id"] not in ids


def test_old_transfer_candidate_survives_large_recent_memory_churn(tmp_path) -> None:
    current_root = tmp_path / "current"
    current_root.mkdir()
    with make_client(tmp_path) as client:
        connector_id = create_connector(client, current_root, "projects/current")
        transferable = remember(
            client,
            {
                "type": "decision",
                "statement": "供应商差异进入薄适配层。",
                "namespace": "projects/old",
                "transfer": {
                    "scope": "cross_project",
                    "problem_shape": "多个模型供应商接口不同",
                    "principle": "稳定核心 + 薄适配层",
                    "applies_when": ["接入多个模型供应商"],
                    "avoid_when": [],
                },
            },
        )
        for index in range(205):
            remember(
                client,
                {
                    "type": "fact",
                    "statement": f"最近但无关的构建记录 {index}",
                    "namespace": "projects/noise",
                },
            )
        response = preflight(
            client,
            connector_id,
            current_root,
            "多个模型供应商应该怎样隔离接口差异？",
        )

    assert response.status_code == 200
    assert transferable["id"] in {item["id"] for item in response.json()["items"]}


def test_preflight_rejects_cwd_outside_connector_root(tmp_path) -> None:
    project_root = tmp_path / "project"
    outside = tmp_path / "outside"
    project_root.mkdir()
    outside.mkdir()
    with make_client(tmp_path) as client:
        connector_id = create_connector(client, project_root, "projects/demo")
        response = preflight(client, connector_id, outside, "项目架构")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "PROJECT_SCOPE_DENIED"


def test_preflight_excludes_sensitive_memory_even_on_direct_overlap(tmp_path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    with make_client(tmp_path) as client:
        connector_id = create_connector(client, project_root, "projects/demo")
        sensitive = remember(
            client,
            {
                "type": "fact",
                "statement": "用户有高血压诊断。",
                "reason": "医疗信息",
                "namespace": "projects/demo",
            },
        )
        response = preflight(client, connector_id, project_root, "高血压诊断需要注意什么？")

    assert response.status_code == 200
    assert sensitive["sensitivity"] == "sensitive"
    assert sensitive["id"] not in {item["id"] for item in response.json()["items"]}


def test_transfer_metadata_is_restricted_to_experience_types(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/memories",
            headers=auth_headers(),
            json={
                "type": "preference",
                "statement": "喜欢简洁回答",
                "transfer": {
                    "scope": "cross_project",
                    "problem_shape": "回答风格",
                    "principle": "简洁",
                },
            },
        )

    assert response.status_code == 422
