from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

HOOK_SCHEMA_VERSION = "1"
MAX_HOOK_TEXT_BYTES = 32 * 1024

_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,180}$")


def truncate_utf8(value: str, max_bytes: int = MAX_HOOK_TEXT_BYTES) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


class InteractionFragment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = HOOK_SCHEMA_VERSION
    connector_id: str
    agent_family: str
    fragment_kind: Literal["user_message", "assistant_message"]
    session_id: str
    prompt_id: str | None = None
    cwd: str
    transcript_path: str
    user_text: str | None = None
    assistant_text: str | None = None

    @field_validator("connector_id", "agent_family", "session_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("invalid identifier")
        return value

    @field_validator("prompt_id")
    @classmethod
    def validate_optional_identifier(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTIFIER.fullmatch(value):
            raise ValueError("invalid identifier")
        return value

    @field_validator("user_text", "assistant_text")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = truncate_utf8(value.strip())
        return normalized or None

    @model_validator(mode="after")
    def validate_event_text(self) -> InteractionFragment:
        if self.fragment_kind == "user_message":
            if self.prompt_id is None or self.user_text is None or self.assistant_text is not None:
                raise ValueError("user_message requires prompt_id and only user_text")
        elif self.assistant_text is None or self.user_text is not None:
            raise ValueError("assistant_message requires only assistant_text")
        return self


AgentTurnEvent = InteractionFragment
