from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any


class AlreadyRunningError(RuntimeError):
    """Raised when another PersonalMemory Service owns the data directory."""


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: int | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(self.path, os.O_RDWR | os.O_CREAT)
        try:
            os.lseek(handle, 0, os.SEEK_SET)
            os.write(handle, b"\0")
            os.lseek(handle, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            os.close(handle)
            raise AlreadyRunningError(
                "another PersonalMemory Service is already running"
            ) from error
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        handle = self._handle
        self._handle = None
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(handle, 0, os.SEEK_SET)
                msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            os.close(handle)

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, _exc_type: Any, _exc_value: Any, _traceback: Any) -> None:
        self.release()


class ParentWatch:
    def __init__(self, server: Any, parent_pid: int, interval: float = 1.0) -> None:
        self._server = server
        self._parent_pid = parent_pid
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="personalmemory-parent-watch",
            daemon=True,
        )
        self._interval = interval

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(self._interval * 2, 0.1))

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            if not process_exists(self._parent_pid):
                self._server.should_exit = True
                return


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x1000, 0, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True
