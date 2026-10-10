from personalmemory.infrastructure.database.models import (
    ConnectorInstanceRecord,
    MemorySpaceRecord,
)
from personalmemory.infrastructure.database.session import Database
from personalmemory.settings import Settings


def make_runtime(tmp_path):
    source = tmp_path / "SOURCE.md"
    target = tmp_path / "TARGET.md"
    source.write_text("## Fact\n- 用户拥有自己的记忆数据。\n", encoding="utf-8")
    target.write_text(
        "# Agent\n\n<!-- personalmemory:start -->\nold\n<!-- personalmemory:end -->\n",
        encoding="utf-8",
    )
    database = Database(Settings(data_dir=tmp_path / "data", api_token="test-token"))
    database.initialize()
    with database.session_factory.begin() as session:
        session.add(MemorySpaceRecord(id="space_01", name="我的记忆"))
        session.flush()
        session.add_all(
            [
                ConnectorInstanceRecord(
                    id="source_01",
                    memory_space_id="space_01",
                    profile_id="generic_markdown_readonly",
                    name="来源 Agent",
                    source_path=str(source),
                    namespace="general",
                    can_read=True,
                    can_write=False,
                    read_enabled=True,
                    write_enabled=False,
                ),
                ConnectorInstanceRecord(
                    id="target_01",
                    memory_space_id="space_01",
                    profile_id="generic_markdown_managed",
                    name="目标 Agent",
                    target_path=str(target),
                    namespace="general",
                    can_read=False,
                    can_write=True,
                    read_enabled=False,
                    write_enabled=True,
                ),
            ]
        )
    return database, source, target


def test_pause_skips_background_sync_but_explicit_sync_runs_once(tmp_path):
    from personalmemory.application.sync import SyncCoordinator, SyncResult

    database, _source, target = make_runtime(tmp_path)
    original_target = target.read_text(encoding="utf-8")
    coordinator = SyncCoordinator(database)
    control = coordinator.update_runtime_control(sync_paused=True)

    assert control.sync_paused is True
    assert coordinator.sync_all_once() == SyncResult()
    assert target.read_text(encoding="utf-8") == original_target

    forced = coordinator.sync_all_once(force=True)

    assert forced.ingested == 1
    assert forced.projected == 1
    assert "用户拥有自己的记忆数据。" in target.read_text(encoding="utf-8")


def test_privacy_mode_ingests_locally_and_projects_after_resume(tmp_path):
    from personalmemory.application.sync import SyncCoordinator

    database, _source, target = make_runtime(tmp_path)
    original_target = target.read_text(encoding="utf-8")
    coordinator = SyncCoordinator(database)
    coordinator.update_runtime_control(privacy_mode=True)

    private_sync = coordinator.sync_all_once()

    assert private_sync.ingested == 1
    assert private_sync.projected == 0
    assert target.read_text(encoding="utf-8") == original_target

    coordinator.update_runtime_control(privacy_mode=False)
    resumed = coordinator.sync_all_once()

    assert resumed.ingested == 0
    assert resumed.projected == 1
    assert "用户拥有自己的记忆数据。" in target.read_text(encoding="utf-8")
