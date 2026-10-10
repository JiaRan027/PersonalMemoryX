from __future__ import annotations

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from personalmemory.infrastructure.database.models import Base
from personalmemory.settings import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine: Engine = create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
            future=True,
        )
        event.listen(self.engine, "connect", self._configure_connection)
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
        )

    @staticmethod
    def _configure_connection(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    def initialize(self) -> None:
        self.settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.engine.begin() as connection:
            connection.execute(text("PRAGMA journal_mode=WAL"))
        Base.metadata.create_all(self.engine)
        self._migrate_tombstone_scope()
        self._migrate_exact_content_fingerprint()
        from personalmemory.infrastructure.database.search import MemorySearchIndex

        MemorySearchIndex(self).initialize()

    def _migrate_tombstone_scope(self) -> None:
        with self.engine.begin() as connection:
            columns = {
                str(row[1])
                for row in connection.execute(text("PRAGMA table_info(tombstones)"))
            }
            if "memory_space_id" not in columns:
                connection.execute(
                    text("ALTER TABLE tombstones ADD COLUMN memory_space_id VARCHAR(64)")
                )
            spaces = connection.execute(text("SELECT id FROM memory_spaces LIMIT 2")).all()
            if len(spaces) == 1:
                connection.execute(
                    text(
                        "UPDATE tombstones SET memory_space_id = :space_id "
                        "WHERE memory_space_id IS NULL"
                    ),
                    {"space_id": spaces[0][0]},
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_tombstones_memory_space_id "
                    "ON tombstones (memory_space_id)"
                )
            )

    def _migrate_exact_content_fingerprint(self) -> None:
        from personalmemory.domain.memory.fingerprint import exact_content_fingerprint

        with self.engine.begin() as connection:
            columns = {
                str(row[1])
                for row in connection.execute(text("PRAGMA table_info(memory_objects)"))
            }
            if "exact_content_fingerprint" not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE memory_objects "
                        "ADD COLUMN exact_content_fingerprint VARCHAR(64)"
                    )
                )
            rows = connection.execute(
                text(
                    "SELECT id, type, statement FROM memory_objects "
                    "WHERE exact_content_fingerprint IS NULL"
                )
            ).all()
            for memory_id, memory_type, statement in rows:
                connection.execute(
                    text(
                        "UPDATE memory_objects SET exact_content_fingerprint = :fingerprint "
                        "WHERE id = :memory_id"
                    ),
                    {
                        "fingerprint": exact_content_fingerprint(memory_type, statement),
                        "memory_id": memory_id,
                    },
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS "
                    "ix_memory_objects_exact_content_fingerprint "
                    "ON memory_objects (exact_content_fingerprint)"
                )
            )

    def is_ready(self) -> bool:
        with self.engine.connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1

    def dispose(self) -> None:
        self.engine.dispose()
