import re
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from personalmemory import __build_revision__, __version__


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


def test_management_api_requires_session_token(tmp_path):
    with make_client(tmp_path) as client:
        response = client.get("/api/v1/memories")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


def test_connector_to_memory_desktop_flow(tmp_path):
    source = tmp_path / "SOURCE.md"
    source.write_text("## Fact\n- 用户拥有自己的记忆数据。\n", encoding="utf-8")

    with make_client(tmp_path) as client:
        preview = client.post(
            "/api/v1/connectors/preview",
            headers=auth_headers(),
            json={
                "profile_id": "generic_markdown_readonly",
                "source_path": str(source),
                "read_enabled": True,
                "write_enabled": False,
            },
        )
        created = client.post(
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
        )
        connector_id = created.json()["id"]
        synced = client.post(
            f"/api/v1/connectors/{connector_id}/sync",
            headers=auth_headers(),
        )
        memories = client.get("/api/v1/memories", headers=auth_headers())
        activities = client.get("/api/v1/activities", headers=auth_headers())
        paused = client.patch(
            f"/api/v1/connectors/{connector_id}",
            headers=auth_headers(),
            json={"read_enabled": False},
        )

    assert preview.status_code == 200
    assert preview.json()["capabilities"] == {"read": True, "write": False}
    assert created.status_code == 201
    assert synced.status_code == 200
    assert synced.json()["ingested"] == 1
    assert memories.json()["total"] == 1
    memory = memories.json()["items"][0]
    assert memory["statement"] == "用户拥有自己的记忆数据。"
    assert memory["provenance"]["locator"] == str(source.resolve())
    assert memory["provenance"]["source_revision_id"].startswith("source_")
    assert memory["provenance"]["source_format"] == "markdown"
    assert memory["provenance"]["line_start"] == 2
    assert memory["payload"]["source_locator"] == str(source.resolve())
    assert activities.json()["items"][0]["kind"] == "source_ingested"
    assert activities.json()["items"][0]["created_at"].endswith("+00:00")
    assert paused.json()["read_enabled"] is False


def test_continuity_manifest_connector_creates_a_write_only_l0_cache(tmp_path):
    target = tmp_path / "AGENT_CONTEXT.md"
    with make_client(tmp_path) as client:
        for memory_type, statement, namespace in (
            ("fact", "用户正在开发 PersonalMemory。", "general"),
            ("goal", "当前目标是验证第二个 Agent。", "projects/personalmemory"),
            ("preference", "用户要求没有证据就明确说不知道。", "general"),
            ("decision", "Continuity Core 不包含平台分支。", "projects/personalmemory"),
        ):
            created_memory = client.post(
                "/api/v1/memories",
                headers=auth_headers(),
                json={
                    "type": memory_type,
                    "statement": statement,
                    "namespace": namespace,
                },
            )
            assert created_memory.status_code == 201

        # This test isolates L0 projection. Agent-submitted memories are provisional
        # by default, so emulate the separate user-confirmation governance step.
        from personalmemory.infrastructure.database.models import MemoryObjectRecord
        from sqlalchemy import update

        with client.app.state.database.session_factory.begin() as session:
            session.execute(
                update(MemoryObjectRecord).values(epistemic_state="asserted")
            )

        preview = client.post(
            "/api/v1/connectors/preview",
            headers=auth_headers(),
            json={
                "profile_id": "continuity_manifest_file",
                "source_path": None,
                "target_path": str(target),
                "read_enabled": False,
                "write_enabled": True,
            },
        )
        created = client.post(
            "/api/v1/connectors",
            headers=auth_headers(),
            json={
                "name": "Agent L0 cache",
                "profile_id": "continuity_manifest_file",
                "source_path": None,
                "target_path": str(target),
                "namespace": "projects/personalmemory",
                "read_enabled": False,
                "write_enabled": True,
            },
        )
        synced = client.post(
            f"/api/v1/connectors/{created.json()['id']}/sync",
            headers=auth_headers(),
        )

    assert preview.status_code == 200
    assert preview.json()["capabilities"] == {"read": False, "write": True}
    assert created.status_code == 201
    assert created.json()["read_enabled"] is False
    assert synced.status_code == 200
    assert synced.json()["projected"] == 1
    rendered = target.read_text(encoding="utf-8")
    assert "manifest_version: 2" in rendered
    assert "用户正在开发 PersonalMemory" in rendered
    assert "当前目标是验证第二个 Agent" in rendered
    assert "没有证据就明确说不知道" in rendered
    assert "Continuity Core 不包含平台分支" not in rendered


def test_memory_revision_and_delete_prevent_source_resurrection(tmp_path):
    source = tmp_path / "SOURCE.md"
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

        revised = client.patch(
            f"/api/v1/memories/{memory['id']}",
            headers=auth_headers(),
            json={
                "expected_revision": 1,
                "statement": "用户坚持拥有自己的记忆数据。",
                "reason": "用户纠正",
            },
        )

        deleted = client.delete(
            f"/api/v1/memories/{memory['id']}", headers=auth_headers()
        )
        source.write_text(
            "## Fact\n- 用户拥有自己的记忆数据。\n\n<!-- source changed -->\n",
            encoding="utf-8",
        )
        client.post(
            f"/api/v1/connectors/{connector['id']}/sync", headers=auth_headers()
        )
        after_rescan = client.get("/api/v1/memories", headers=auth_headers()).json()

    assert revised.status_code == 200
    assert revised.json()["revision"] == 2
    assert deleted.status_code == 204
    assert after_rescan["total"] == 0


def test_user_can_undo_an_automatic_ingestion_operation(tmp_path):
    source = tmp_path / "SOURCE.md"
    source.write_text("## Goal\n- 发布 Windows 首版。\n", encoding="utf-8")

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
        activity = next(
            item
            for item in client.get("/api/v1/activities", headers=auth_headers()).json()["items"]
            if item["kind"] == "source_ingested"
        )

        undone = client.post(
            f"/api/v1/activities/{activity['operation_id']}/undo",
            headers=auth_headers(),
        )
        memories = client.get("/api/v1/memories", headers=auth_headers()).json()

    assert undone.status_code == 200
    assert undone.json()["undone"] == 1
    assert memories["total"] == 0

def test_undo_still_succeeds_when_projection_target_is_in_conflict(tmp_path):
    source = tmp_path / "SOURCE.md"
    target = tmp_path / "TARGET.md"
    source.write_text("## Fact\n- User owns their memory.\n", encoding="utf-8")
    target.write_text(
        "# User notes\n\n<!-- personalmemory:start -->\nold\n"
        "<!-- personalmemory:end -->\n",
        encoding="utf-8",
    )

    with make_client(tmp_path) as client:
        connector = client.post(
            "/api/v1/connectors",
            headers=auth_headers(),
            json={
                "name": "Windows acceptance connector",
                "profile_id": "generic_markdown_managed",
                "source_path": str(source),
                "target_path": str(target),
                "namespace": "general",
                "read_enabled": True,
                "write_enabled": True,
            },
        ).json()
        client.post(
            f"/api/v1/connectors/{connector['id']}/sync", headers=auth_headers()
        )
        activity = next(
            item
            for item in client.get("/api/v1/activities", headers=auth_headers()).json()["items"]
            if item["kind"] == "source_ingested"
        )

        target.write_text(
            target.read_text(encoding="utf-8").replace(
                "# User notes", "# User notes changed outside the managed block"
            ),
            encoding="utf-8",
        )

        undone = client.post(
            f"/api/v1/activities/{activity['operation_id']}/undo",
            headers=auth_headers(),
        )
        memories = client.get("/api/v1/memories", headers=auth_headers()).json()

    assert undone.status_code == 200
    assert undone.json()["undone"] == 1
    assert memories["total"] == 0



def test_exceptions_list_separates_quarantined_candidates(tmp_path):
    source = tmp_path / "SOURCE.md"
    source.write_text("## Preference\n- Editor: VS Code\n", encoding="utf-8")

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
        source.write_text("## Preference\n- Editor: Zed\n", encoding="utf-8")
        client.post(
            f"/api/v1/connectors/{connector['id']}/sync", headers=auth_headers()
        )

        response = client.get("/api/v1/exceptions", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["kind"] == "quarantine"
    assert response.json()["items"][0]["statement"] == "Editor: Zed"


def test_memory_search_uses_rebuildable_fts_index(tmp_path):
    source = tmp_path / "SOURCE.md"
    source.write_text(
        "## Insight\n- Durable experience layer survives model changes.\n"
        "  reason: Provider independence keeps context portable.\n",
        encoding="utf-8",
    )

    with make_client(tmp_path) as client:
        connector = client.post(
            "/api/v1/connectors",
            headers=auth_headers(),
            json={
                "name": "Search verification connector",
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

        statement_match = client.get(
            "/api/v1/memories",
            headers=auth_headers(),
            params={"search": "durable surviv"},
        )
        reason_match = client.get(
            "/api/v1/memories",
            headers=auth_headers(),
            params={"search": "provider port"},
        )
        missing_match = client.get(
            "/api/v1/memories",
            headers=auth_headers(),
            params={"search": "nonexistent token"},
        )

    assert statement_match.status_code == 200
    assert statement_match.json()["total"] == 1
    assert statement_match.json()["items"][0]["statement"] == (
        "Durable experience layer survives model changes."
    )
    assert reason_match.status_code == 200
    assert reason_match.json()["total"] == 1
    assert missing_match.status_code == 200
    assert missing_match.json()["total"] == 0


def test_projection_conflict_is_exposed_as_actionable_connector_exception(tmp_path):
    source = tmp_path / "SOURCE.md"
    target = tmp_path / "TARGET.md"
    source.write_text("## Fact\n- User owns their memory.\n", encoding="utf-8")
    target.write_text(
        "# User notes\n\n<!-- personalmemory:start -->\nold\n"
        "<!-- personalmemory:end -->\n",
        encoding="utf-8",
    )

    with make_client(tmp_path) as client:
        connector = client.post(
            "/api/v1/connectors",
            headers=auth_headers(),
            json={
                "name": "Windows acceptance connector",
                "profile_id": "generic_markdown_managed",
                "source_path": str(source),
                "target_path": str(target),
                "namespace": "general",
                "read_enabled": True,
                "write_enabled": True,
            },
        ).json()
        client.post(
            f"/api/v1/connectors/{connector['id']}/sync", headers=auth_headers()
        )
        last_verified_target = target.read_text(encoding="utf-8")

        target.write_text(
            target.read_text(encoding="utf-8").replace(
                "# User notes", "# User notes changed outside the managed block"
            ),
            encoding="utf-8",
        )
        source.write_text(
            "## Fact\n- User owns their memory.\n- A second fact.\n",
            encoding="utf-8",
        )
        sync = client.post(
            f"/api/v1/connectors/{connector['id']}/sync", headers=auth_headers()
        )
        response = client.get("/api/v1/exceptions", headers=auth_headers())

    assert sync.status_code == 200
    assert sync.json()["failed"] == 1
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0] == {
        "id": response.json()["items"][0]["id"],
        "kind": "connector",
        "reason_code": "PROJECTION_CONFLICT",
        "statement": "Windows acceptance connector",
        "memory_type": None,
        "connector_id": connector["id"],
        "created_at": response.json()["items"][0]["created_at"],
    }

    with make_client(tmp_path) as client:
        target.write_text(last_verified_target, encoding="utf-8")
        recovered = client.post(
            f"/api/v1/connectors/{connector['id']}/sync", headers=auth_headers()
        )
        after_recovery = client.get("/api/v1/exceptions", headers=auth_headers())

        target.write_text(
            target.read_text(encoding="utf-8").replace(
                "# User notes", "# User notes changed a second time"
            ),
            encoding="utf-8",
        )
        source.write_text(
            "## Fact\n- User owns their memory.\n- A second fact.\n- A third fact.\n",
            encoding="utf-8",
        )
        repeated = client.post(
            f"/api/v1/connectors/{connector['id']}/sync", headers=auth_headers()
        )
        after_repeated_conflict = client.get(
            "/api/v1/exceptions", headers=auth_headers()
        )

    assert recovered.status_code == 200
    assert after_recovery.json()["total"] == 0
    assert repeated.status_code == 200
    assert repeated.json()["failed"] == 1
    assert after_repeated_conflict.json()["total"] == 1
    assert after_repeated_conflict.json()["items"][0]["reason_code"] == "PROJECTION_CONFLICT"


def test_protocol_api_exports_and_inspects_without_mutation(tmp_path):
    source = tmp_path / "SOURCE.md"
    package_path = tmp_path / "memory.pmemory"
    source.write_text("## Insight\n- 经验比聊天记录更有价值。\n", encoding="utf-8")

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

        exported = client.post(
            "/api/v1/protocol/export",
            headers=auth_headers(),
            json={"package_path": str(package_path)},
        )
        inspected = client.post(
            "/api/v1/protocol/inspect",
            headers=auth_headers(),
            json={"package_path": str(package_path)},
        )
        memories = client.get("/api/v1/memories", headers=auth_headers())

    assert exported.status_code == 200
    assert exported.json()["memory_count"] == 1
    assert Path(exported.json()["readable_path"]).read_text(
        encoding="utf-8-sig"
    ).startswith("# PersonalMemory 可阅读导出")
    assert inspected.status_code == 200
    assert inspected.json() == {
        "valid": True,
        "memory_count": 1,
        "memory_space_id": "space_default",
    }
    assert memories.json()["total"] == 1


def test_protocol_export_directory_generates_a_non_overwriting_filename(tmp_path):
    with make_client(tmp_path) as client:
        first = client.post(
            "/api/v1/protocol/export",
            headers=auth_headers(),
            json={"package_path": str(tmp_path)},
        )
        second = client.post(
            "/api/v1/protocol/export",
            headers=auth_headers(),
            json={"package_path": str(tmp_path)},
        )

    assert first.status_code == second.status_code == 200
    first_path = Path(first.json()["package_path"])
    second_path = Path(second.json()["package_path"])
    first_readable = Path(first.json()["readable_path"])
    second_readable = Path(second.json()["readable_path"])
    assert first_path.parent == second_path.parent == tmp_path.resolve()
    assert first_path.name.startswith("personalmemory-")
    assert first_path.suffix == second_path.suffix == ".pmemory"
    assert first_path != second_path
    assert first_path.is_file() and second_path.is_file()
    assert first_readable.is_file() and second_readable.is_file()
    readable_text = first_readable.read_text(encoding="utf-8-sig")
    assert f"产品版本：{__version__}" in readable_text
    assert f"构建修订：{__build_revision__}" in readable_text
    assert "导出时间：" in readable_text
    assert re.search(r"导出时区：系统本地时间（UTC[+-]\d{2}:\d{2}）", readable_text)
    with zipfile.ZipFile(first_path) as archive:
        packaged_readable = archive.read("views/memories.md").decode("utf-8")
    assert f"构建修订：{__build_revision__}" in packaged_readable
    assert re.search(
        r"导出时区：系统本地时间（UTC[+-]\d{2}:\d{2}）", packaged_readable
    )
