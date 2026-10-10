from types import SimpleNamespace

import pytest


def test_instance_lock_allows_only_one_service_owner(tmp_path):
    from personalmemory.infrastructure.instance_lock import (
        AlreadyRunningError,
        InstanceLock,
    )

    first = InstanceLock(tmp_path / "runtime" / "service.lock")
    second = InstanceLock(tmp_path / "runtime" / "service.lock")

    with first, pytest.raises(AlreadyRunningError):
        second.acquire()

    with second:
        pass


def test_parent_watch_requests_shutdown_when_parent_disappears():
    from personalmemory.infrastructure.instance_lock import ParentWatch

    server = SimpleNamespace(should_exit=False)
    watch = ParentWatch(server, 2_147_483_647, interval=0.01)
    watch.start()
    watch._thread.join(timeout=1)
    watch.stop()

    assert server.should_exit is True


def test_process_exists_reports_current_process():
    import os

    from personalmemory.infrastructure.instance_lock import process_exists

    assert process_exists(os.getpid()) is True


def test_process_exists_reports_missing_process():
    from personalmemory.infrastructure.instance_lock import process_exists

    assert process_exists(2_147_483_647) is False
