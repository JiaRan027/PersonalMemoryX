"""Runtime connection file：让本机 MCP/CLI 客户端发现本地 Service 的地址与会话 Token。

Service 启动绑定端口后写入 `{data_dir}/runtime/connection.json`：

    {"endpoint": "http://127.0.0.1:<port>", "token": "<session token>"}

桌面端每次启动会生成新的会话 Token，因此 MCP 客户端不能在配置里写死
Token；通过读取该文件即可跟随当前运行的桌面会话，无需用户手工复制。
"""

from __future__ import annotations

import json
import os
import time
from contextlib import suppress
from pathlib import Path

CONNECTION_FILE_NAME = "connection.json"
CONNECTION_REPLACE_ATTEMPTS = 10
CONNECTION_REPLACE_RETRY_SECONDS = 0.01


def connection_file_path(data_dir: Path) -> Path:
    return data_dir / "runtime" / CONNECTION_FILE_NAME


def write_runtime_connection(
    data_dir: Path,
    host: str,
    port: int,
    token: str,
) -> Path:
    path = connection_file_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"endpoint": f"http://{host}:{port}", "token": token}
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        for attempt in range(CONNECTION_REPLACE_ATTEMPTS):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt + 1 == CONNECTION_REPLACE_ATTEMPTS:
                    raise
                # Windows may briefly deny replacement while the MCP reader
                # has the old connection file open. Give that short read a
                # chance to finish rather than failing Service startup.
                time.sleep(CONNECTION_REPLACE_RETRY_SECONDS)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
    return path


def read_runtime_connection(data_dir: Path) -> dict[str, str] | None:
    """读取连接信息；文件缺失、损坏或字段为空时返回 None。"""
    path = connection_file_path(data_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    endpoint = str(payload.get("endpoint") or "").strip()
    token = str(payload.get("token") or "").strip()
    if not endpoint or not token:
        return None
    return {"endpoint": endpoint, "token": token}
