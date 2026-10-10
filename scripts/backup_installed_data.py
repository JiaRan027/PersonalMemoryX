"""Create and verify a consistent backup of an installed PersonalMemory database."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import tomllib
from datetime import UTC, datetime
from pathlib import Path

COUNT_TABLES = (
    "memory_objects",
    "connector_instances",
    "activities",
    "tombstones",
    "source_revisions",
)


def project_version() -> str:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as stream:
        version = str(tomllib.load(stream)["project"]["version"])
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]*", version):
        raise SystemExit(f"unsafe project version for backup filename: {version!r}")
    return version


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    available = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    return {
        name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        for name in COUNT_TABLES
        if name in available
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("backup_dir", type=Path)
    args = parser.parse_args()

    source = args.source.resolve(strict=True)
    if not source.is_file():
        raise SystemExit(f"database is not a file: {source}")
    backup_dir = args.backup_dir.resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"memory-before-{project_version()}-{timestamp}.db"
    if destination.exists():
        raise SystemExit(f"backup already exists: {destination}")

    with (
        sqlite3.connect(source) as source_connection,
        sqlite3.connect(destination) as destination_connection,
    ):
        source_connection.backup(destination_connection)

    with sqlite3.connect(f"file:{destination.as_posix()}?mode=ro", uri=True) as backup:
        integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SystemExit(f"backup integrity check failed: {integrity}")
        table_total = backup.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0]
        counts = table_counts(backup)

    result = {
        "created_at": datetime.now(UTC).isoformat(),
        "source": str(source),
        "backup": str(destination),
        "sha256": sha256(destination),
        "bytes": destination.stat().st_size,
        "integrity_check": integrity,
        "table_count": table_total,
        "counts": counts,
    }
    metadata = destination.with_suffix(".json")
    metadata.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({**result, "metadata": str(metadata)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
