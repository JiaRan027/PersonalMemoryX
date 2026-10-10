from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_CONTINUITY_TEXT_BYTES = 32 * 1024
MAX_CONTINUITY_ENVELOPE_BYTES = 72 * 1024
MAX_EVIDENCE_REFERENCES = 16

_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,180}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def truncate_utf8(value: str, max_bytes: int = MAX_CONTINUITY_TEXT_BYTES) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


class SourceCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    has_actor_roles: bool
    has_session: bool
    has_timestamps: bool
    has_tool_calls: bool = False
    has_attachments: bool = False
    is_realtime: bool = False

    def as_dict(self) -> dict[str, bool]:
        return self.model_dump(mode="json")


class AuthorityActor(BaseModel):
    """Semantic authority for a claim, independent of its transport source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_kind: Literal["user", "agent", "system", "tool", "unknown"]
    assertion_kind: Literal[
        "explicit", "confirmed", "observed", "inferred", "unknown"
    ]
    actor_id: str | None = Field(default=None, max_length=180)

    @field_validator("actor_id")
    @classmethod
    def normalize_actor_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or not _IDENTIFIER.fullmatch(value):
            raise ValueError("invalid actor_id")
        return value

    @model_validator(mode="after")
    def validate_authority_pair(self) -> AuthorityActor:
        if self.assertion_kind in {"explicit", "confirmed"} and self.actor_kind != "user":
            raise ValueError("explicit or confirmed authority requires a user actor")
        if self.assertion_kind == "inferred" and self.actor_kind not in {"agent", "system"}:
            raise ValueError("inferred authority requires an agent or system actor")
        if self.actor_kind == "unknown" and self.assertion_kind != "unknown":
            raise ValueError("unknown actor requires unknown assertion kind")
        return self


class ContinuityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    locator: str = Field(min_length=1, max_length=2048)
    digest: str
    interaction_id: str
    message_ids: tuple[str, ...] = Field(default=(), max_length=32)
    evidence_kind: Literal[
        "interaction", "message", "transcript", "document", "native_memory", "tool_result"
    ] = "interaction"
    actor_kind: Literal["user", "agent", "system", "tool", "unknown"] = "unknown"

    @field_validator("locator")
    @classmethod
    def normalize_locator(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("evidence locator must not be blank")
        if len(value.encode("utf-8")) > 4096:
            raise ValueError("evidence locator exceeds byte limit")
        return value

    @field_validator("digest")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        value = value.casefold()
        if not _SHA256.fullmatch(value):
            raise ValueError("evidence digest must be a SHA-256 hex digest")
        return value

    @field_validator("interaction_id")
    @classmethod
    def validate_interaction_id(cls, value: str) -> str:
        value = value.strip()
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("invalid interaction_id")
        return value

    @field_validator("message_ids")
    @classmethod
    def validate_message_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not _IDENTIFIER.fullmatch(value) for value in values):
            raise ValueError("invalid message_id")
        return tuple(dict.fromkeys(values))


class ContinuityEnvelope(BaseModel):
    """Platform-neutral evidence unit accepted by the Continuity pipeline."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_kind: str
    source_family: str
    capture_mode: str
    capabilities: SourceCapabilities
    evidence: ContinuityEvidence
    evidence_references: tuple[ContinuityEvidence, ...] = Field(
        default=(), max_length=MAX_EVIDENCE_REFERENCES - 1
    )
    authority: AuthorityActor = AuthorityActor(
        actor_kind="unknown", assertion_kind="unknown"
    )
    namespace: str | None = Field(default=None, max_length=240)
    idempotency_key: str | None = None
    user_text: str | None = None
    assistant_text: str | None = None
    session_id: str | None = None
    schema_version: Literal["1"] = "1"

    @field_validator("source_kind", "source_family", "capture_mode")
    @classmethod
    def validate_source_identifier(cls, value: str) -> str:
        value = value.strip()
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("invalid source identifier")
        return value

    @field_validator("namespace")
    @classmethod
    def normalize_namespace(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or len(value.encode("utf-8")) > 480:
            raise ValueError("invalid namespace")
        return value

    @field_validator("idempotency_key", "session_id")
    @classmethod
    def validate_optional_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("invalid identifier")
        return value

    @field_validator("user_text", "assistant_text")
    @classmethod
    def normalize_visible_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if len(value.encode("utf-8")) > MAX_CONTINUITY_TEXT_BYTES:
            raise ValueError("visible text exceeds byte limit")
        return value

    @model_validator(mode="after")
    def validate_envelope(self) -> ContinuityEnvelope:
        if self.user_text is None and self.assistant_text is None:
            raise ValueError("continuity envelope requires visible text")
        if self.authority.actor_kind == "user" and self.user_text is None:
            raise ValueError("user authority requires user_text evidence")
        references = self.all_evidence()
        identities = {
            (item.locator, item.digest, item.interaction_id) for item in references
        }
        if len(identities) != len(references):
            raise ValueError("duplicate evidence reference")
        if self.capabilities.has_actor_roles:
            actors = {item.actor_kind for item in references}
            if self.user_text is not None and "user" not in actors:
                raise ValueError("user_text requires user evidence when actor roles are available")
        encoded = json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
        if len(encoded) > MAX_CONTINUITY_ENVELOPE_BYTES:
            raise ValueError("continuity envelope exceeds byte limit")
        return self

    @classmethod
    def from_interaction(
        cls,
        *,
        source_kind: str,
        source_family: str,
        capture_mode: str,
        capabilities: SourceCapabilities,
        locator: str,
        interaction_id: str,
        user_text: str | None,
        assistant_text: str | None,
        session_id: str | None = None,
        message_ids: tuple[str, ...] = (),
        namespace: str | None = None,
    ) -> ContinuityEnvelope:
        user = truncate_utf8((user_text or "").strip()) or None
        assistant = truncate_utf8((assistant_text or "").strip()) or None
        if user is None and assistant is None:
            raise ValueError("continuity envelope requires visible text")
        identity = {
            "schema_version": "1",
            "session_id": session_id,
            "interaction_id": interaction_id,
            "user_text": user,
            "assistant_text": assistant,
        }
        digest = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        actor_kind = "user" if user is not None else "agent"
        assertion_kind = "explicit" if user is not None else "inferred"
        return cls(
            source_kind=source_kind,
            source_family=source_family,
            capture_mode=capture_mode,
            capabilities=capabilities,
            evidence=ContinuityEvidence(
                locator=locator,
                digest=digest,
                interaction_id=interaction_id,
                message_ids=message_ids,
                actor_kind=actor_kind,
            ),
            authority=AuthorityActor(
                actor_kind=actor_kind,
                assertion_kind=assertion_kind,
                actor_id=source_family if actor_kind == "agent" else None,
            ),
            namespace=namespace,
            idempotency_key=interaction_id,
            user_text=user,
            assistant_text=assistant,
            session_id=session_id,
        )

    def all_evidence(self) -> tuple[ContinuityEvidence, ...]:
        return (self.evidence, *self.evidence_references)

    def normalized_content(self) -> str:
        lines: list[str] = []
        if self.user_text:
            lines.append(f"用户: {self.user_text}")
        if self.assistant_text:
            lines.append(f"助手: {self.assistant_text}")
        return "\n".join(lines)

    def content_digest(self) -> str:
        canonical = self.model_dump(mode="json", exclude={"idempotency_key"})
        return hashlib.sha256(
            json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def ingestion_metadata(self) -> dict[str, object]:
        return {
            "continuity_schema_version": self.schema_version,
            "source_kind": self.source_kind,
            "source_family": self.source_family,
            "capture_mode": self.capture_mode,
            "source_capabilities": self.capabilities.as_dict(),
            "authority": self.authority.model_dump(mode="json"),
            "namespace": self.namespace,
            "idempotency_key": self.idempotency_key,
            "evidence_locator": self.evidence.locator,
            "evidence_digest": self.evidence.digest,
            "evidence_references": [
                item.model_dump(mode="json") for item in self.all_evidence()
            ],
            "interaction_id": self.evidence.interaction_id,
            "message_ids": list(self.evidence.message_ids),
            "session_id": self.session_id,
        }


class DeliveryReceipt(BaseModel):
    """Observable truth about the retrieval path used for one delivery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_mode: Literal["lexical", "embedding", "hybrid"]
    effective_mode: Literal["lexical", "embedding", "hybrid"]
    status: Literal["ok", "degraded", "timeout"]
    cache_class: Literal["stable", "dynamic"]
    degradation_reason: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def validate_status(self) -> DeliveryReceipt:
        if self.status == "ok" and self.degradation_reason is not None:
            raise ValueError("successful delivery cannot have a degradation reason")
        if self.status != "ok" and not self.degradation_reason:
            raise ValueError("degraded delivery requires a reason")
        return self
