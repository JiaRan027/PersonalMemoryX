from __future__ import annotations

import sys

import uvicorn

from personalmemory.api.app import create_app
from personalmemory.infrastructure.instance_lock import InstanceLock, ParentWatch
from personalmemory.infrastructure.runtime_connection import write_runtime_connection
from personalmemory.settings import Settings


class ConnectionAwareServer(uvicorn.Server):
    """启动绑定端口后写入 runtime/connection.json，供本机 MCP/CLI 客户端发现。"""

    def __init__(self, settings: Settings, config: uvicorn.Config) -> None:
        super().__init__(config)
        self._settings = settings

    async def startup(self, sockets=None) -> None:  # noqa: ANN001 - uvicorn 签名
        await super().startup(sockets=sockets)
        if not self._settings.api_token:
            return
        port = self.config.port
        if port == 0:
            try:
                port = self.servers[0].sockets[0].getsockname()[1]
            except (IndexError, OSError):
                return
        write_runtime_connection(
            self._settings.data_dir,
            self._settings.host,
            port,
            self._settings.api_token,
        )


def run() -> None:
    settings = Settings()
    lock_path = settings.data_dir / "runtime" / "service.lock"
    with InstanceLock(lock_path):
        app = create_app(settings)
        config = uvicorn.Config(
            app,
            host=settings.host,
            port=settings.port,
            access_log=False,
            log_level="warning",
            log_config=None,
        )
        server = ConnectionAwareServer(settings, config)
        app.state.request_shutdown = lambda: setattr(server, "should_exit", True)
        parent_watch = None
        raw_parent_pid = settings.parent_pid
        if raw_parent_pid is not None:
            parent_watch = ParentWatch(server, raw_parent_pid)
            parent_watch.start()
        try:
            server.run()
        finally:
            if parent_watch is not None:
                parent_watch.stop()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "backup":
        from personalmemory.infrastructure.database.backup_cli import run_backup_cli

        raise SystemExit(run_backup_cli())
    run()
