from service.tests.api.test_api_contract import auth_headers, make_client


def test_deleted_memory_is_not_restored_by_source_or_stale_package(tmp_path):
    source = tmp_path / "SOURCE.md"
    package_path = tmp_path / "before-delete.pmemory"
    source.write_text("## Fact\n- 用户拥有自己的记忆数据。\n", encoding="utf-8")

    with make_client(tmp_path) as client:
        connector = client.post(
            "/api/v1/connectors",
            headers=auth_headers(),
            json={
                "name": "测试 AI",
                "profile_id": "generic_markdown_readonly",
                "source_path": str(source),
                "target_path": None,
                "namespace": "general",
                "read_enabled": True,
                "write_enabled": False,
            },
        ).json()
        client.post(
            f"/api/v1/connectors/{connector['id']}/sync", headers=auth_headers()
        )
        memory = client.get("/api/v1/memories", headers=auth_headers()).json()["items"][0]
        client.post(
            "/api/v1/protocol/export",
            headers=auth_headers(),
            json={"package_path": str(package_path)},
        )
        client.delete(f"/api/v1/memories/{memory['id']}", headers=auth_headers())

        source.write_text(
            "## Fact\n- 用户拥有自己的记忆数据。\n\n<!-- changed -->\n",
            encoding="utf-8",
        )
        client.post(
            f"/api/v1/connectors/{connector['id']}/sync", headers=auth_headers()
        )
        imported = client.post(
            "/api/v1/protocol/import",
            headers=auth_headers(),
            json={"package_path": str(package_path)},
        )
        memories = client.get("/api/v1/memories", headers=auth_headers()).json()

    assert imported.status_code == 200
    assert imported.json()["imported"] == 0
    assert imported.json()["skipped_tombstoned"] == 1
    assert memories["total"] == 0
