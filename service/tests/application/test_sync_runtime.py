import asyncio

import pytest
from personalmemory.infrastructure.database.models import (
    ConnectorInstanceRecord,
    MemoryObjectRecord,
    MemorySpaceRecord,
    SourceRevisionRecord,
)
from personalmemory.infrastructure.database.session import Database
from personalmemory.settings import Settings
from sqlalchemy import func, select


def make_sync_database(tmp_path) -> tuple[Database, object, object]:
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


def test_sync_pipeline_forwards_settings_to_extractor(tmp_path, monkeypatch):
    """Sync 链路必须把 Settings 传给抽取器（LLM 开关才能真正生效）。"""
    import personalmemory.application.ingestion as ingestion_module
    from personalmemory.application.sync import SyncCoordinator
    from personalmemory.domain.extraction.deterministic import (
        DeterministicExtractorAdapter,
    )

    source = tmp_path / "session.txt"
    source.write_text("我的目标是进入人工智能行业。我掌握 Python。", encoding="utf-8")
    database = Database(Settings(data_dir=tmp_path / "data", api_token="test-token"))
    database.initialize()
    with database.session_factory.begin() as session:
        session.add(MemorySpaceRecord(id="space_01", name="我的记忆"))
        session.flush()
        session.add(
            ConnectorInstanceRecord(
                id="conv_01",
                memory_space_id="space_01",
                profile_id="conversation_text",
                name="会话来源",
                source_path=str(source),
                namespace="general",
                can_read=True,
                can_write=False,
                read_enabled=True,
                write_enabled=False,
            )
        )

    received: list[object] = []

    def recording_extractor(settings):
        received.append(settings)
        return DeterministicExtractorAdapter()

    monkeypatch.setattr(ingestion_module, "build_extractor", recording_extractor)

    settings = Settings(
        data_dir=tmp_path / "data",
        api_token="test-token",
        llm_enabled=True,
        llm_base_url="http://localhost:9/v1",
        llm_model="fake",
        llm_max_chars=50_000,
    )
    result = SyncCoordinator(database, settings=settings).sync_connector("conv_01")

    assert result.ingested == 1
    assert result.accepted == 2
    assert len(received) == 1
    assert received[0].llm_enabled is True
    assert received[0].llm_max_chars == 50_000
    with database.session_factory() as session:
        statements = [
            row.statement
            for row in session.scalars(select(MemoryObjectRecord)).all()
        ]
    assert any("人工智能行业" in s for s in statements)
    assert any("Python" in s for s in statements)


def test_conversation_exports_are_manual_snapshots_not_background_watchers(tmp_path):
    from personalmemory.application.sync import SyncCoordinator

    source = tmp_path / "codex-session.jsonl"
    source.write_text(
        '{"type":"response_item","payload":{"type":"message","role":"user",'
        '"content":[{"type":"input_text","text":"我决定采用本地优先架构。"}]}}\n',
        encoding="utf-8",
    )
    database = Database(Settings(data_dir=tmp_path / "data", api_token="test-token"))
    database.initialize()
    with database.session_factory.begin() as session:
        session.add(MemorySpaceRecord(id="space_01", name="我的记忆"))
        session.flush()
        session.add(
            ConnectorInstanceRecord(
                id="transcript_01",
                memory_space_id="space_01",
                profile_id="codex_jsonl",
                name="Codex 历史对话",
                source_path=str(source),
                namespace="general",
                can_read=True,
                can_write=False,
                read_enabled=True,
                write_enabled=False,
            )
        )
    coordinator = SyncCoordinator(database)

    background = coordinator.sync_all_once()
    with database.session_factory() as session:
        revisions_after_background = session.scalar(
            select(func.count()).select_from(SourceRevisionRecord)
        )

    assert background.ingested == 0
    assert revisions_after_background == 0

    manual = coordinator.sync_connector("transcript_01")
    with database.session_factory() as session:
        revisions_after_manual = session.scalar(
            select(func.count()).select_from(SourceRevisionRecord)
        )
    assert manual.ingested == 1
    assert revisions_after_manual == 1


def test_sync_once_ingests_source_and_updates_target(tmp_path):
    from personalmemory.application.sync import SyncCoordinator

    database, _source, target = make_sync_database(tmp_path)

    result = SyncCoordinator(database).sync_all_once()

    assert result.ingested == 1
    assert result.projected == 1
    assert "用户拥有自己的记忆数据。" in target.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_background_runtime_detects_later_source_change(tmp_path):
    from personalmemory.application.sync import SyncCoordinator

    database, source, target = make_sync_database(tmp_path)
    coordinator = SyncCoordinator(database, poll_interval=0.05)
    await coordinator.start()
    try:
        await wait_until(lambda: "用户拥有自己的记忆数据。" in target.read_text("utf-8"))
        source.write_text(
            "## Fact\n- 用户拥有自己的记忆数据。\n- 用户坚持开放数据格式。\n",
            encoding="utf-8",
        )
        await wait_until(lambda: "用户坚持开放数据格式。" in target.read_text("utf-8"))
    finally:
        await coordinator.stop()

    with database.session_factory() as session:
        memory_count = session.scalar(select(func.count()).select_from(MemoryObjectRecord))
    assert memory_count == 2


def test_paused_directions_do_not_read_or_write(tmp_path):
    from personalmemory.application.sync import SyncCoordinator

    database, _source, target = make_sync_database(tmp_path)
    with database.session_factory.begin() as session:
        source_connector = session.get(ConnectorInstanceRecord, "source_01")
        target_connector = session.get(ConnectorInstanceRecord, "target_01")
        source_connector.read_enabled = False
        target_connector.write_enabled = False
    original_target = target.read_text(encoding="utf-8")

    result = SyncCoordinator(database).sync_all_once()

    assert result.ingested == 0
    assert result.projected == 0
    assert target.read_text(encoding="utf-8") == original_target


def test_loop_guard_skips_its_own_managed_projection(tmp_path):
    from personalmemory.application.sync import SyncCoordinator

    path = tmp_path / "BIDIRECTIONAL.md"
    path.write_text(
        "## Fact\n- 用户正在测试双向文件。\n\n"
        "<!-- personalmemory:start -->\nold\n<!-- personalmemory:end -->\n",
        encoding="utf-8",
    )
    database = Database(Settings(data_dir=tmp_path / "data", api_token="test-token"))
    database.initialize()
    with database.session_factory.begin() as session:
        session.add(MemorySpaceRecord(id="space_01", name="我的记忆"))
        session.flush()
        session.add(
            ConnectorInstanceRecord(
                id="loop_01",
                memory_space_id="space_01",
                profile_id="generic_markdown_managed",
                name="双向 Agent",
                source_path=str(path),
                target_path=str(path),
                namespace="general",
                can_read=True,
                can_write=True,
                read_enabled=True,
                write_enabled=True,
            )
        )
    coordinator = SyncCoordinator(database)

    first = coordinator.sync_all_once()
    second = coordinator.sync_all_once()

    with database.session_factory() as session:
        source_revisions = session.scalar(
            select(func.count()).select_from(SourceRevisionRecord)
        )
    assert first.ingested == 1
    assert first.projected == 1
    assert second.ingested == 0
    assert source_revisions == 1


async def wait_until(predicate, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            if predicate():
                return
        except PermissionError:
            pass
        await asyncio.sleep(0.02)
    raise AssertionError("condition was not met before timeout")
