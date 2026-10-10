"""Create a consistent, integrity-checked SQLite backup for desktop updates."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

COUNT_TABLES = (
    "memory_objects",
    "connector_instances",
    "activities",
    "tombstones",
    "source_revisions",
)


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    return {
        name: int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
        for name in COUNT_TABLES
        if name in tables
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(data_dir: Path, backup_dir: Path) -> dict[str, object]:
    source = (data_dir / "data" / "memory.db").resolve()
    if not source.is_file():
        return {"status": "skipped", "reason": "database-not-created"}

    resolved_data = data_dir.resolve()
    resolved_backup = backup_dir.resolve()
    if resolved_backup == resolved_data or resolved_data in resolved_backup.parents:
        raise ValueError("update backup directory must be outside the live data directory")

    resolved_backup.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = resolved_backup / f"memory-before-update-{timestamp}.db"
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite existing backup: {destination.name}")

    with (
        sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True) as source_db,
        sqlite3.connect(destination) as backup_db,
    ):
        source_db.backup(backup_db)

    with sqlite3.connect(f"file:{destination.as_posix()}?mode=ro", uri=True) as backup_db:
        integrity = str(backup_db.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            destination.unlink(missing_ok=True)
            raise RuntimeError(f"backup integrity check failed: {integrity}")
        counts = _counts(backup_db)

    metadata: dict[str, object] = {
        "created_at": datetime.now(UTC).isoformat(),
        "source": str(source),
        "backup": str(destination),
        "sha256": _sha256(destination),
        "bytes": destination.stat().st_size,
        "integrity_check": integrity,
        "counts": counts,
    }
    destination.with_suffix(".json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"status": "created", **metadata}


def run_backup_cli() -> int:
    data_value = os.environ.get("PERSONALMEMORY_DATA_DIR", "").strip()
    backup_value = os.environ.get("PERSONALMEMORY_BACKUP_DIR", "").strip()
    if not data_value or not backup_value:
        raise SystemExit("PERSONALMEMORY_DATA_DIR and PERSONALMEMORY_BACKUP_DIR are required")
    result = create_backup(Path(data_value), Path(backup_value))
    print(json.dumps(result, ensure_ascii=False))
    return 0
