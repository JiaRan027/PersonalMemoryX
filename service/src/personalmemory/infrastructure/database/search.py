from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from personalmemory.infrastructure.database.session import Database


class MemorySearchIndex:
    """Own the rebuildable FTS5 projection for active Memory Objects."""

    _table_sql = """
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_objects_fts
        USING fts5(memory_id UNINDEXED, statement, reason, namespace)
    """
    _insert_trigger_sql = """
        CREATE TRIGGER IF NOT EXISTS memory_objects_fts_insert
        AFTER INSERT ON memory_objects
        BEGIN
            INSERT INTO memory_objects_fts(memory_id, statement, reason, namespace)
            VALUES (new.id, new.statement, coalesce(new.reason, ''), new.namespace);
        END
    """
    _update_trigger_sql = """
        CREATE TRIGGER IF NOT EXISTS memory_objects_fts_update
        AFTER UPDATE OF statement, reason, namespace, status ON memory_objects
        BEGIN
            DELETE FROM memory_objects_fts WHERE memory_id = old.id;
            INSERT INTO memory_objects_fts(memory_id, statement, reason, namespace)
            VALUES (new.id, new.statement, coalesce(new.reason, ''), new.namespace);
        END
    """
    _delete_trigger_sql = """
        CREATE TRIGGER IF NOT EXISTS memory_objects_fts_delete
        AFTER DELETE ON memory_objects
        BEGIN
            DELETE FROM memory_objects_fts WHERE memory_id = old.id;
        END
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def initialize(self) -> None:
        with self.database.engine.begin() as connection:
            connection.exec_driver_sql(self._table_sql)
            connection.exec_driver_sql(self._insert_trigger_sql)
            connection.exec_driver_sql(self._update_trigger_sql)
            connection.exec_driver_sql(self._delete_trigger_sql)
        self.rebuild()

    def rebuild(self) -> None:
        with self.database.engine.begin() as connection:
            connection.execute(text("DELETE FROM memory_objects_fts"))
            connection.execute(
                text(
                    """
                    INSERT INTO memory_objects_fts(memory_id, statement, reason, namespace)
                    SELECT id, statement, coalesce(reason, ''), namespace
                    FROM memory_objects
                    """
                )
            )

    def search_ids(self, query: str, *, limit: int | None = None) -> list[str]:
        tokens = list(
            dict.fromkeys(
                re.findall(r"[\w\u4e00-\u9fff]+", query, flags=re.UNICODE)
            )
        )
        if not tokens:
            return []
        if limit is not None and limit < 1:
            return []
        if any(re.search(r"[\u4e00-\u9fff]", token) for token in tokens):
            return self._search_substring_ids(tokens, limit=limit)
        expression = " AND ".join(
            '"' + token.replace('"', '""') + '"*' for token in tokens
        )
        with self.database.engine.connect() as connection:
            limit_clause = " LIMIT :limit" if limit is not None else ""
            parameters: dict[str, str | int] = {"expression": expression}
            if limit is not None:
                parameters["limit"] = limit
            rows = connection.execute(
                text(
                    f"""
                    SELECT memory_objects_fts.memory_id
                    FROM memory_objects_fts
                    JOIN memory_objects
                      ON memory_objects.id = memory_objects_fts.memory_id
                    WHERE memory_objects.status = 'active'
                      AND memory_objects_fts MATCH :expression
                    ORDER BY memory_objects.updated_at DESC
                    {limit_clause}
                    """
                ),
                parameters,
            ).all()
        return [row[0] for row in rows]

    def _search_substring_ids(
        self, tokens: list[str], *, limit: int | None = None
    ) -> list[str]:
        """Match CJK queries without relying on unicode61 word boundaries."""
        predicates: list[str] = []
        parameters: dict[str, str] = {}
        for index, token in enumerate(tokens):
            parameter = f"token_{index}"
            predicates.append(
                f"""(
                    instr(lower(memory_objects.statement), lower(:{parameter})) > 0
                    OR instr(
                        lower(coalesce(memory_objects.reason, '')),
                        lower(:{parameter})
                    ) > 0
                    OR instr(
                        lower(memory_objects.namespace),
                        lower(:{parameter})
                    ) > 0
                )"""
            )
            parameters[parameter] = token
        limit_clause = " LIMIT :limit" if limit is not None else ""
        if limit is not None:
            parameters["limit"] = limit
        with self.database.engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT memory_objects.id
                    FROM memory_objects
                    WHERE memory_objects.status = 'active'
                      AND {' AND '.join(predicates)}
                    ORDER BY memory_objects.updated_at DESC
                    {limit_clause}
                    """
                ),
                parameters,
            ).all()
        return [row[0] for row in rows]
