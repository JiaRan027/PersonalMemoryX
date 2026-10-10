"""runtime/connection.json 读写与容错测试。"""

from __future__ import annotations

import pytest
from personalmemory.infrastructure import runtime_connection
from personalmemory.infrastructure.runtime_connection import (
    connection_file_path,
    read_runtime_connection,
    write_runtime_connection,
)


def test_write_and_read_roundtrip(tmp_path) -> None:
    path = write_runtime_connection(tmp_path, "127.0.0.1", 19421, "session-secret")

    assert path == connection_file_path(tmp_path)
    assert read_runtime_connection(tmp_path) == {
        "endpoint": "http://127.0.0.1:19421",
        "token": "session-secret",
    }


def test_failed_atomic_connection_update_preserves_previous_runtime_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_runtime_connection(tmp_path, "127.0.0.1", 19421, "old-token")

    def fail_replace(_source, _destination) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(runtime_connection.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        write_runtime_connection(tmp_path, "127.0.0.1", 19422, "new-token")

    assert read_runtime_connection(tmp_path) == {
        "endpoint": "http://127.0.0.1:19421",
        "token": "old-token",
    }
    assert not path.with_name(
        f"{path.name}.{runtime_connection.os.getpid()}.tmp"
    ).exists()


def test_atomic_connection_update_retries_a_transient_windows_file_lock(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_runtime_connection(tmp_path, "127.0.0.1", 19421, "old-token")
    replace = runtime_connection.os.replace
    attempts = 0

    def fail_twice(source, destination) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("simulated temporary sharing violation")
        replace(source, destination)

    monkeypatch.setattr(runtime_connection.os, "replace", fail_twice)

    write_runtime_connection(tmp_path, "127.0.0.1", 19422, "new-token")

    assert attempts == 3
    assert read_runtime_connection(tmp_path) == {
        "endpoint": "http://127.0.0.1:19422",
        "token": "new-token",
    }


def test_read_missing_file_returns_none(tmp_path) -> None:
    assert read_runtime_connection(tmp_path) is None


def test_read_corrupted_file_returns_none(tmp_path) -> None:
    path = connection_file_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert read_runtime_connection(tmp_path) is None


def test_read_empty_fields_returns_none(tmp_path) -> None:
    path = connection_file_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"endpoint": "http://127.0.0.1:19421", "token": ""}', encoding="utf-8")

    assert read_runtime_connection(tmp_path) is None
