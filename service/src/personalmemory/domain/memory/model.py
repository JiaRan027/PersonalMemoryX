from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from personalmemory.domain.memory.types import (
    AdmissionAction,
    EpistemicState,
    MemoryType,
    Sensitivity,
)


class MemoryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: MemoryType
    statement: str
    reason: str | None = None
    source_revision_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    importance: float = Field(ge=0.0, le=1.0)
    namespace: str = "general"
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("statement", "source_revision_id", "namespace")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped

    @field_validator("reason")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AdmissionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: AdmissionAction
    sensitivity: Sensitivity
    epistemic_state: EpistemicState
    reason_code: str
    policy_version: str = "0.1.0"
