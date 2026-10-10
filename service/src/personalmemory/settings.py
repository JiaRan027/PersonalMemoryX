from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "PersonalMemoryData"
    return Path.home() / ".personalmemory"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PERSONALMEMORY_", extra="ignore")

    data_dir: Path = Field(default_factory=default_data_dir)
    api_token: str = ""
    parent_pid: int | None = None
    host: str = "127.0.0.1"
    port: int = 0
    enable_background_sync: bool = True
    sync_interval: float = 1.0
    llm_enabled: bool = False
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 30.0
    llm_max_chars: int = 120_000

    @property
    def database_path(self) -> Path:
        return self.data_dir / "data" / "memory.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"
