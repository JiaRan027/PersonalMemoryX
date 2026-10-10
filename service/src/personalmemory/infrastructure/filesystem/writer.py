from __future__ import annotations

import hashlib
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


class ProjectionConflictError(RuntimeError):
    pass


class ProjectionVerificationError(RuntimeError):
    pass


class ManagedBlockMissingError(RuntimeError):
    pass


@dataclass(frozen=True)
class WriteResult:
    file_hash: str
    backup_path: Path


def hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class ManagedBlockWriter:
    def __init__(
        self,
        *,
        verifier: Callable[[bytes, bytes], bool] | None = None,
        start_marker: str = "<!-- personalmemory:start -->",
        end_marker: str = "<!-- personalmemory:end -->",
    ) -> None:
        self.verifier = verifier or (lambda actual, expected: actual == expected)
        self.start_marker = start_marker
        self.end_marker = end_marker

    def write(
        self,
        target_path: Path,
        rendered_block: str,
        *,
        expected_file_hash: str | None = None,
    ) -> WriteResult:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        existed = target_path.exists()
        original = target_path.read_bytes() if existed else b""
        original_hash = hash_bytes(original)
        if expected_file_hash is not None and original_hash != expected_file_hash:
            raise ProjectionConflictError("Target file changed after the last verified projection")

        newline = "\r\n" if b"\r\n" in original else "\n"
        original_text = original.decode("utf-8")
        block = rendered_block.replace("\r\n", "\n").replace("\n", newline)
        updated_text = self._replace_block(original_text, block, newline)
        expected = updated_text.encode("utf-8")

        backup_path = target_path.with_name(f"{target_path.name}.personalmemory.bak")
        temporary_path = target_path.with_name(f".{target_path.name}.{uuid4().hex}.tmp")
        if existed:
            shutil.copy2(target_path, backup_path)
        else:
            backup_path.write_bytes(b"")

        try:
            with temporary_path.open("wb") as temporary:
                temporary.write(expected)
                temporary.flush()
                os.fsync(temporary.fileno())
            for attempt in range(5):
                try:
                    os.replace(temporary_path, target_path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.01 * (2**attempt))
            actual = target_path.read_bytes()
            if not self.verifier(actual, expected):
                if existed:
                    shutil.copy2(backup_path, target_path)
                else:
                    target_path.unlink(missing_ok=True)
                raise ProjectionVerificationError("Projection readback verification failed")
        finally:
            temporary_path.unlink(missing_ok=True)

        return WriteResult(file_hash=hash_bytes(actual), backup_path=backup_path)

    def _replace_block(self, original: str, block: str, newline: str) -> str:
        start = original.find(self.start_marker)
        end_start = original.find(self.end_marker, max(start, 0))
        if start >= 0 and end_start >= 0:
            end = end_start + len(self.end_marker)
            return f"{original[:start]}{block}{original[end:]}"
        if original.strip():
            raise ManagedBlockMissingError(
                "Existing target does not contain a PersonalMemory managed block"
            )
        return f"{block}{newline}"
