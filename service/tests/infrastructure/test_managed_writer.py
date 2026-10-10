import hashlib

import pytest


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_writer_replaces_only_managed_block_and_creates_backup(tmp_path):
    from personalmemory.infrastructure.filesystem.writer import ManagedBlockWriter

    target = tmp_path / "MEMORY.md"
    original = (
        b"# User content\r\nkeep this\r\n"
        b"<!-- personalmemory:start -->\r\nold projection\r\n"
        b"<!-- personalmemory:end -->\r\ntail stays\r\n"
    )
    target.write_bytes(original)
    block = """<!-- personalmemory:start -->
## Fact
- new projection
<!-- personalmemory:end -->"""

    result = ManagedBlockWriter().write(
        target,
        block,
        expected_file_hash=sha256(original),
    )

    updated = target.read_bytes()
    assert updated.startswith(b"# User content\r\nkeep this\r\n")
    assert updated.endswith(b"\r\ntail stays\r\n")
    assert b"new projection" in updated
    assert b"old projection" not in updated
    assert result.backup_path.read_bytes() == original
    assert result.file_hash == sha256(updated)


def test_writer_rejects_concurrent_target_change(tmp_path):
    from personalmemory.infrastructure.filesystem.writer import (
        ManagedBlockWriter,
        ProjectionConflictError,
    )

    target = tmp_path / "MEMORY.md"
    original = b"<!-- personalmemory:start -->\nold\n<!-- personalmemory:end -->\n"
    target.write_bytes(original)

    with pytest.raises(ProjectionConflictError):
        ManagedBlockWriter().write(
            target,
            "<!-- personalmemory:start -->\nnew\n<!-- personalmemory:end -->",
            expected_file_hash="0" * 64,
        )

    assert target.read_bytes() == original


def test_writer_restores_backup_when_readback_verification_fails(tmp_path):
    from personalmemory.infrastructure.filesystem.writer import (
        ManagedBlockWriter,
        ProjectionVerificationError,
    )

    target = tmp_path / "MEMORY.md"
    original = b"<!-- personalmemory:start -->\nold\n<!-- personalmemory:end -->\n"
    target.write_bytes(original)
    writer = ManagedBlockWriter(verifier=lambda _actual, _expected: False)

    with pytest.raises(ProjectionVerificationError):
        writer.write(
            target,
            "<!-- personalmemory:start -->\nnew\n<!-- personalmemory:end -->",
            expected_file_hash=sha256(original),
        )

    assert target.read_bytes() == original


def test_writer_retries_transient_windows_replace_lock(tmp_path, monkeypatch):
    from personalmemory.infrastructure.filesystem import writer as writer_module
    from personalmemory.infrastructure.filesystem.writer import ManagedBlockWriter

    target = tmp_path / "MEMORY.md"
    original = b"<!-- personalmemory:start -->\nold\n<!-- personalmemory:end -->\n"
    target.write_bytes(original)
    actual_replace = writer_module.os.replace
    attempts = 0

    def intermittently_locked(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError(13, "target is temporarily locked")
        actual_replace(source, destination)

    monkeypatch.setattr(writer_module.os, "replace", intermittently_locked)

    ManagedBlockWriter().write(
        target,
        "<!-- personalmemory:start -->\nnew\n<!-- personalmemory:end -->",
        expected_file_hash=sha256(original),
    )

    assert attempts == 2
    assert b"new" in target.read_bytes()
