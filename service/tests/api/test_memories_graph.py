from __future__ import annotations

from fastapi.testclient import TestClient


def make_client(tmp_path):
    from personalmemory.api.app import create_app
    from personalmemory.settings import Settings

    settings = Settings(
        data_dir=tmp_path / "data",
        api_token="graph-test-token",
        enable_background_sync=False,
    )
    return TestClient(create_app(settings))


def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer graph-test-token"}


def _create_connector(client, source: str) -> str:
    response = client.post(
        "/api/v1/connectors",
        headers=auth_headers(),
        json={
            "name": "Graph source",
            "profile_id": "generic_markdown_readonly",
            "source_path": source,
            "target_path": None,
            "namespace": "general",
            "read_enabled": True,
            "write_enabled": False,
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def _sync_all(client, connector_id: str) -> None:
    synced = client.post(
        f"/api/v1/connectors/{connector_id}/sync", headers=auth_headers()
    )
    assert synced.status_code == 200
    assert synced.json()["ingested"] == 1


def test_graph_derives_topic_edges_from_shared_topics(tmp_path):
    source = tmp_path / "SOURCE.md"
    source.write_text(
        "## Insight\n- 记忆数据必须开放。\n- 记忆数据属于用户。\n"
        "## Skill\n- 掌握 Python。\n",
        encoding="utf-8",
    )
    with make_client(tmp_path) as client:
        connector_id = _create_connector(client, str(source))
        _sync_all(client, connector_id)
        graph = client.get("/api/v1/memories/graph", headers=auth_headers())

    payload = graph.json()
    assert payload["counts"]["nodes"] == 3
    # the two insight memories share 记忆/数据/记忆数据 (>=2); Python skill stays isolated
    assert payload["counts"]["edges"] == 1
    assert payload["counts"]["derived_edges"] == 1
    edge = payload["edges"][0]
    assert edge["type"] == "related_to"
    assert edge["origin"] == "derived_topic"
    assert edge["derived"] is True
    node_ids = {node["id"] for node in payload["nodes"]}
    assert edge["source"] in node_ids
    assert edge["target"] in node_ids


def test_graph_derives_role_edge_event_to_insight(tmp_path):
    source = tmp_path / "SOURCE.md"
    source.write_text(
        "## Event\n- 我完成了 Agent 项目。\n"
        "## Insight\n- 完成 Agent 项目后我认识到"
        "不能完全依赖 Agent 主动写入长期记忆。\n",
        encoding="utf-8",
    )
    with make_client(tmp_path) as client:
        connector_id = _create_connector(client, str(source))
        _sync_all(client, connector_id)
        graph = client.get("/api/v1/memories/graph", headers=auth_headers())

    payload = graph.json()
    assert payload["counts"]["nodes"] == 2
    derived = [edge for edge in payload["edges"] if edge["derived"]]
    assert len(derived) == 1
    edge = derived[0]
    assert edge["type"] == "derived_from"
    assert edge["origin"] == "derived_role"
    types_by_id = {node["id"]: node["type"] for node in payload["nodes"]}
    assert types_by_id[edge["source"]] == "event"
    assert types_by_id[edge["target"]] == "insight"


def test_graph_clusters_connected_memories(tmp_path):
    source = tmp_path / "SOURCE.md"
    source.write_text(
        "## Event\n- 我完成了 Agent 项目。\n"
        "## Insight\n- 完成 Agent 项目后我认识到"
        "不能完全依赖 Agent 主动写入长期记忆。\n"
        "## Skill\n- 掌握 Python。\n",
        encoding="utf-8",
    )
    with make_client(tmp_path) as client:
        connector_id = _create_connector(client, str(source))
        _sync_all(client, connector_id)
        graph = client.get("/api/v1/memories/graph", headers=auth_headers())

    payload = graph.json()
    assert payload["counts"]["clusters"] == 2
    cluster_by_id = {cluster["id"]: cluster for cluster in payload["clusters"]}
    assert any(cluster["size"] == 2 for cluster in cluster_by_id.values())
    assert any(cluster["size"] == 1 for cluster in cluster_by_id.values())
    node_cluster_ids = {node["id"]: node["cluster_id"] for node in payload["nodes"]}
    assert len(set(node_cluster_ids.values())) == 2


def test_graph_keeps_unrelated_memories_isolated(tmp_path):
    source = tmp_path / "SOURCE.md"
    source.write_text(
        "## Goal\n- 进入人工智能行业。\n## Skill\n- 掌握 Python。\n",
        encoding="utf-8",
    )
    with make_client(tmp_path) as client:
        connector_id = _create_connector(client, str(source))
        _sync_all(client, connector_id)
        graph = client.get("/api/v1/memories/graph", headers=auth_headers())

    payload = graph.json()
    assert payload["counts"]["nodes"] == 2
    assert payload["counts"]["edges"] == 0


def test_graph_endpoint_includes_explicit_relations(tmp_path):
    source = tmp_path / "SOURCE.md"
    source.write_text(
        "## Fact\n- User owns their memory.\n- Memory must stay portable.\n",
        encoding="utf-8",
    )
    with make_client(tmp_path) as client:
        connector_id = _create_connector(client, str(source))
        _sync_all(client, connector_id)
        memories = client.get("/api/v1/memories", headers=auth_headers()).json()["items"]
        assert len(memories) == 2

        from personalmemory.application.ingestion import new_id
        from personalmemory.infrastructure.database.models import MemoryRelationRecord

        database = client.app.state.database
        with database.session_factory.begin() as session:
            session.add(
                MemoryRelationRecord(
                    id=new_id("relation"),
                    memory_space_id="space_default",
                    source_memory_id=memories[0]["id"],
                    target_memory_id=memories[1]["id"],
                    relation_type="supports",
                    origin="user_reviewed",
                    validity_state="active",
                )
            )

        graph = client.get("/api/v1/memories/graph", headers=auth_headers())

    payload = graph.json()
    assert payload["counts"]["explicit_edges"] == 1
    explicit = [edge for edge in payload["edges"] if not edge["derived"]]
    assert explicit[0]["origin"] == "user_reviewed"
    assert explicit[0]["type"] == "supports"


def test_graph_excludes_noncanonical_duplicate_but_list_preserves_both(tmp_path):
    with make_client(tmp_path) as client:
        for namespace in ("projects/one", "projects/two"):
            response = client.post(
                "/api/v1/memories",
                headers=auth_headers(),
                json={
                    "type": "insight",
                    "statement": "Source indexes should remain provenance, not Memory text.",
                    "namespace": namespace,
                },
            )
            assert response.status_code in {200, 201}, response.text

        listed = client.get("/api/v1/memories", headers=auth_headers())
        graph = client.get("/api/v1/memories/graph", headers=auth_headers())

    listed_items = listed.json()["items"]
    assert len(listed_items) == 2
    assert all(
        any(
            issue["code"] == "CROSS_NAMESPACE_EXACT_DUPLICATE"
            for issue in item["quality_issues"]
        )
        for item in listed_items
    )
    assert graph.status_code == 200
    nodes = graph.json()["nodes"]
    assert len(nodes) == 1
    assert nodes[0]["review_triage"]["graph_eligible"] is True


def test_graph_node_includes_importance_and_cluster(tmp_path):
    source = tmp_path / "SOURCE.md"
    source.write_text("## Decision\n- 我决定采用结构化查询优先。\n", encoding="utf-8")
    with make_client(tmp_path) as client:
        connector_id = _create_connector(client, str(source))
        _sync_all(client, connector_id)
        graph = client.get("/api/v1/memories/graph", headers=auth_headers())

    payload = graph.json()
    node = payload["nodes"][0]
    assert node["type"] == "decision"
    assert node["importance"] == 0.85
    assert node["cluster_id"] in {cluster["id"] for cluster in payload["clusters"]}


def test_graph_endpoint_empty_database(tmp_path):
    with make_client(tmp_path) as client:
        graph = client.get("/api/v1/memories/graph", headers=auth_headers())
    assert graph.status_code == 200
    payload = graph.json()
    assert payload["nodes"] == []
    assert payload["edges"] == []
    assert payload["counts"] == {
        "nodes": 0,
        "edges": 0,
        "clusters": 0,
        "explicit_edges": 0,
        "derived_edges": 0,
    }


def test_graph_endpoint_requires_session_token(tmp_path):
    with make_client(tmp_path) as client:
        response = client.get("/api/v1/memories/graph")
    assert response.status_code == 401
