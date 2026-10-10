from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from personalmemory.domain.memory.types import MemoryType, Sensitivity

CAPSULE_SCHEMA_VERSION = "resume-capsule/0"


class CapsuleCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    current_task: str | None = Field(default=None, max_length=1000)
    verified_status: str | None = Field(default=None, max_length=1000)
    next_action: str | None = Field(default=None, max_length=1000)

    @field_validator("current_task", "verified_status", "next_action")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class CapsuleSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=500)
    namespaces: list[str] | None = Field(default=None, min_length=1, max_length=20)
    allowed_types: list[MemoryType] = Field(
        default_factory=lambda: list(MemoryType), min_length=1, max_length=len(MemoryType)
    )
    max_sensitivity: Sensitivity = Sensitivity.PERSONAL
    limit: int = Field(default=10, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value

    @field_validator("namespaces")
    @classmethod
    def normalize_namespaces(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = list(dict.fromkeys(namespace.strip() for namespace in value))
        if not normalized or any(not namespace for namespace in normalized):
            raise ValueError("namespaces must not contain blanks")
        return normalized

    @model_validator(mode="after")
    def reject_secret_ceiling(self) -> CapsuleSelection:
        if self.max_sensitivity == Sensitivity.SECRET:
            raise ValueError("resume capsules cannot deliver secret memories")
        return self


class CapsuleMemoryReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str = Field(min_length=1, max_length=64)
    revision: int = Field(ge=1)
    type: MemoryType
    statement: str = Field(min_length=1, max_length=2000)
    reason: str | None = Field(default=None, max_length=2000)
    namespace: str = Field(min_length=1, max_length=240)
    sensitivity: Sensitivity


class ResumeCapsule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[CAPSULE_SCHEMA_VERSION] = CAPSULE_SCHEMA_VERSION
    capsule_id: str = Field(pattern=r"^capsule_[a-f0-9]{32}$")
    generated_at: datetime
    expires_at: datetime
    ttl_seconds: int = Field(ge=60, le=86_400)
    namespace_scope: list[str]
    max_sensitivity: Sensitivity
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    selection: CapsuleSelection
    memory_references: list[CapsuleMemoryReference] = Field(max_length=10)
    checkpoint: CapsuleCheckpoint
    dropped_fields: list[str] = Field(max_length=50)
    token_budget: int = Field(ge=256, le=1600)
    estimated_tokens: int = Field(ge=0, le=1600)

    @model_validator(mode="after")
    def validate_consistency(self) -> ResumeCapsule:
        if self.generated_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("capsule timestamps must be timezone-aware")
        if self.generated_at.utcoffset().total_seconds() != 0:
            raise ValueError("generated_at must use UTC")
        if self.expires_at.utcoffset().total_seconds() != 0:
            raise ValueError("expires_at must use UTC")
        if self.expires_at <= self.generated_at:
            raise ValueError("expires_at must be after generated_at")
        if (self.expires_at - self.generated_at).total_seconds() != self.ttl_seconds:
            raise ValueError("ttl_seconds does not match capsule timestamps")
        if self.estimated_tokens > self.token_budget:
            raise ValueError("estimated_tokens exceeds token_budget")
        if self.max_sensitivity != self.selection.max_sensitivity:
            raise ValueError("max_sensitivity does not match selection")
        if self.namespace_scope != (self.selection.namespaces or []):
            raise ValueError("namespace_scope does not match selection")
        return self


class CapsuleValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    memory_id: str | None = None
    expected_revision: int | None = None
    actual_revision: int | None = None


class CapsuleValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    expired: bool
    stale: bool
    checked_at: datetime
    issues: list[CapsuleValidationIssue]
