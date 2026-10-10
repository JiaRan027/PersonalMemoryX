import sqlite3

from sqlalchemy import inspect, text


def test_database_enables_wal_and_foreign_keys(tmp_path):
    from personalmemory.infrastructure.database.session import Database
    from personalmemory.settings import Settings

    database = Database(Settings(data_dir=tmp_path, api_token="test-token"))
    database.initialize()

    with database.engine.connect() as connection:
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()

    assert journal_mode == "wal"
    assert foreign_keys == 1


def test_database_initializes_authoritative_tables(tmp_path):
    from personalmemory.infrastructure.database.session import Database
    from personalmemory.settings import Settings

    database = Database(Settings(data_dir=tmp_path, api_token="test-token"))
    database.initialize()

    tables = set(inspect(database.engine).get_table_names())

    assert {
        "activities",
        "connector_instances",
        "loop_guard_records",
        "memory_candidates",
        "memory_objects",
        "memory_revisions",
        "memory_spaces",
        "projection_receipts",
        "source_revisions",
        "tombstones",
    } <= tables


def test_database_scopes_legacy_tombstones_when_only_one_space_exists(tmp_path):
    from personalmemory.infrastructure.database.session import Database
    from personalmemory.settings import Settings

    database_path = tmp_path / "data" / "memory.db"
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE memory_spaces (id VARCHAR(64) PRIMARY KEY, name VARCHAR(160), "
            "created_at DATETIME)"
        )
        connection.execute(
            "INSERT INTO memory_spaces VALUES ('space_legacy', 'Legacy', CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "CREATE TABLE tombstones (id VARCHAR(64) PRIMARY KEY, object_id VARCHAR(64), "
            "object_kind VARCHAR(32), semantic_fingerprint VARCHAR(64), deleted_at DATETIME)"
        )
        connection.execute(
            "INSERT INTO tombstones VALUES "
            "('tombstone_legacy', 'memory_old', 'memory', 'abc', CURRENT_TIMESTAMP)"
        )

    database = Database(Settings(data_dir=tmp_path, api_token="test-token"))
    database.initialize()

    with database.engine.connect() as connection:
        scope = connection.execute(
            text(
                "SELECT memory_space_id FROM tombstones "
                "WHERE id = 'tombstone_legacy'"
            )
        ).scalar_one()
    assert scope == "space_legacy"


def test_ambiguous_legacy_tombstone_remains_a_conservative_global_block(tmp_path):
    from personalmemory.infrastructure.database.repositories import MemoryRepository
    from personalmemory.infrastructure.database.session import Database
    from personalmemory.settings import Settings

    database_path = tmp_path / "data" / "memory.db"
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE memory_spaces (id VARCHAR(64) PRIMARY KEY, name VARCHAR(160), "
            "created_at DATETIME)"
        )
        connection.executemany(
            "INSERT INTO memory_spaces VALUES (?, ?, CURRENT_TIMESTAMP)",
            [("space_a", "A"), ("space_b", "B")],
        )
        connection.execute(
            "CREATE TABLE tombstones (id VARCHAR(64) PRIMARY KEY, object_id VARCHAR(64), "
            "object_kind VARCHAR(32), semantic_fingerprint VARCHAR(64), deleted_at DATETIME)"
        )
        connection.execute(
            "INSERT INTO tombstones VALUES "
            "('tombstone_legacy', 'memory_old', 'memory', 'abc', CURRENT_TIMESTAMP)"
        )

    database = Database(Settings(data_dir=tmp_path, api_token="test-token"))
    database.initialize()

    with database.session_factory() as session:
        repository = MemoryRepository(session)
        assert repository.is_tombstoned("space_a", "abc") is True
        assert repository.is_tombstoned("space_b", "abc") is True


def test_database_backfills_exact_content_fingerprint(tmp_path):
    from personalmemory.infrastructure.database.models import (
        MemoryObjectRecord,
        MemorySpaceRecord,
    )
    from personalmemory.infrastructure.database.session import Database
    from personalmemory.settings import Settings

    database = Database(Settings(data_dir=tmp_path, api_token="test-token"))
    database.initialize()
    with database.session_factory.begin() as session:
        session.add(MemorySpaceRecord(id="space_01", name="Test"))
        session.flush()
        session.add(
            MemoryObjectRecord(
                id="memory_01",
                memory_space_id="space_01",
                type="fact",
                statement="用户重视数据主权。",
                reason=None,
                namespace="general",
                sensitivity="personal",
                epistemic_state="asserted",
                status="active",
                semantic_fingerprint="legacy-fingerprint",
                exact_content_fingerprint=None,
                semantic_key=None,
                payload={},
                revision=1,
            )
        )

    database._migrate_exact_content_fingerprint()

    with database.session_factory() as session:
        memory = session.get(MemoryObjectRecord, "memory_01")
        indexes = {
            index["name"]
            for index in inspect(database.engine).get_indexes("memory_objects")
        }

    assert memory.exact_content_fingerprint
    assert "ix_memory_objects_exact_content_fingerprint" in indexes
