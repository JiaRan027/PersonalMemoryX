from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from threading import RLock

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from personalmemory.application.ingestion import IngestionService
from personalmemory.domain.connector.profile import get_builtin_profile
from personalmemory.domain.continuity import AuthorityActor, ContinuityEnvelope
from personalmemory.domain.extraction import DeterministicExtractorAdapter
from personalmemory.domain.policy.admission import contains_secret
from personalmemory.infrastructure.database.models import (
    ConnectorInstanceRecord,
    EnvelopeIdempotencyRecord,
    SourceRevisionRecord,
)

_ENVELOPE_LOCK = RLock()


class EnvelopeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class EnvelopeNotFoundError(EnvelopeError):
    pass


class EnvelopeConflictError(EnvelopeError):
    pass


class EnvelopeScopeError(EnvelopeError):
    pass


class EnvelopeValidationError(EnvelopeError):
    pass


@dataclass(frozen=True)
class EnvelopeIngestionResult:
    envelope_id: str
    source_revision_id: str
    operation_id: str
    duplicate: bool
    counts: dict[str, int]


class ContinuityEnvelopeService:
    """One governed entry point for API, Hook and Transcript envelopes."""

    def __init__(self, database, settings=None) -> None:
        self.database = database
        self.settings = settings

    def ingest(
        self,
        connector_id: str,
        envelope: ContinuityEnvelope,
        *,
        require_api_capability: bool = False,
        reject_secret: bool = False,
        authority_verified: bool = True,
        source_format: str | None = None,
        extra_metadata: dict[str, object] | None = None,
    ) -> EnvelopeIngestionResult:
        with _ENVELOPE_LOCK:
            return self._ingest(
                connector_id,
                envelope,
                require_api_capability=require_api_capability,
                reject_secret=reject_secret,
                authority_verified=authority_verified,
                source_format=source_format,
                extra_metadata=extra_metadata,
            )

    def _ingest(
        self,
        connector_id: str,
        envelope: ContinuityEnvelope,
        *,
        require_api_capability: bool,
        reject_secret: bool,
        authority_verified: bool,
        source_format: str | None,
        extra_metadata: dict[str, object] | None,
    ) -> EnvelopeIngestionResult:
        with self.database.session_factory() as session:
            connector = session.get(ConnectorInstanceRecord, connector_id)
            if connector is None:
                raise EnvelopeNotFoundError(
                    "CONNECTOR_NOT_FOUND", "Envelope connector does not exist"
                )
            profile = get_builtin_profile(connector.profile_id)
            if require_api_capability and not profile.capabilities.accept_envelopes:
                raise EnvelopeConflictError(
                    "CONNECTOR_PROFILE_MISMATCH",
                    "Connector does not accept generic Continuity Envelopes",
                )
            if not connector.enabled or not connector.can_read or not connector.read_enabled:
                raise EnvelopeConflictError(
                    "CONNECTOR_DISABLED", "Envelope connector is disabled"
                )
            if envelope.namespace is not None and envelope.namespace != connector.namespace:
                raise EnvelopeScopeError(
                    "NAMESPACE_SCOPE_DENIED",
                    "Envelope namespace is outside the connector authorization scope",
                )
            namespace = connector.namespace

        governed = envelope.model_copy(update={"namespace": namespace})
        if not authority_verified and governed.authority.assertion_kind in {
            "explicit",
            "confirmed",
        }:
            governed = governed.model_copy(
                update={
                    "authority": AuthorityActor(
                        actor_kind="user",
                        assertion_kind="observed",
                        actor_id=governed.authority.actor_id,
                    )
                }
            )
        metadata = governed.ingestion_metadata()
        metadata["authority_verification"] = (
            "verified_source" if authority_verified else "self_attested"
        )
        secret_surface = "\n".join(
            [
                *(value for value in (governed.user_text, governed.assistant_text) if value),
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            ]
        )
        if reject_secret and contains_secret(secret_surface):
            raise EnvelopeValidationError(
                "SECRET_DETECTED", "Envelope content or metadata contains secret-like material"
            )

        idempotency_key = governed.idempotency_key or governed.evidence.interaction_id
        digest = governed.content_digest()
        if require_api_capability:
            source_locator = (
                f"continuity-envelope://{connector_id}/{idempotency_key}"
            )
        else:
            source_locator = governed.evidence.locator

        with self.database.session_factory() as session:
            existing = session.scalar(
                select(SourceRevisionRecord).where(
                    SourceRevisionRecord.connector_id == connector_id,
                    SourceRevisionRecord.source_path == source_locator,
                )
            )
            if existing is not None and existing.content_hash != digest:
                raise EnvelopeConflictError(
                    "IDEMPOTENCY_CONTENT_CONFLICT",
                    "Idempotency key was reused with different governed content",
                )

        binding_id: str | None = None
        inserted = 0
        replay_source_revision_id: str | None = None
        replay_operation_id: str | None = None
        if require_api_capability:
            binding_digest = hashlib.sha256(
                f"{connector_id}\0{idempotency_key}".encode()
            ).hexdigest()
            binding_id = f"envelope_key_{binding_digest[:51]}"
            with self.database.session_factory.begin() as session:
                inserted = session.execute(
                    sqlite_insert(EnvelopeIdempotencyRecord)
                    .values(
                        id=binding_id,
                        connector_id=connector_id,
                        idempotency_key=idempotency_key,
                        content_hash=digest,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["connector_id", "idempotency_key"]
                    )
                ).rowcount
                binding = session.scalar(
                    select(EnvelopeIdempotencyRecord).where(
                        EnvelopeIdempotencyRecord.connector_id == connector_id,
                        EnvelopeIdempotencyRecord.idempotency_key == idempotency_key,
                    )
                )
                if binding is None or binding.content_hash != digest:
                    raise EnvelopeConflictError(
                        "IDEMPOTENCY_CONTENT_CONFLICT",
                        "Idempotency key was reused with different governed content",
                    )
                if binding.source_revision_id and binding.operation_id:
                    replay_source_revision_id = binding.source_revision_id
                    replay_operation_id = binding.operation_id
                elif inserted:
                    content_binding = session.scalar(
                        select(EnvelopeIdempotencyRecord)
                        .where(
                            EnvelopeIdempotencyRecord.connector_id == connector_id,
                            EnvelopeIdempotencyRecord.content_hash == digest,
                            EnvelopeIdempotencyRecord.id != binding_id,
                            EnvelopeIdempotencyRecord.source_revision_id.is_not(None),
                            EnvelopeIdempotencyRecord.operation_id.is_not(None),
                        )
                        .order_by(EnvelopeIdempotencyRecord.created_at)
                    )
                    if content_binding is not None:
                        binding.source_revision_id = content_binding.source_revision_id
                        binding.operation_id = content_binding.operation_id
                        replay_source_revision_id = content_binding.source_revision_id
                        replay_operation_id = content_binding.operation_id

        envelope_identity = hashlib.sha256(
            f"{connector_id}\0{idempotency_key}".encode()
        ).hexdigest()
        if replay_source_revision_id and replay_operation_id:
            return EnvelopeIngestionResult(
                envelope_id=f"envelope_{envelope_identity[:55]}",
                source_revision_id=replay_source_revision_id,
                operation_id=replay_operation_id,
                duplicate=True,
                counts={
                    "accepted": 0,
                    "provisional": 0,
                    "quarantined": 0,
                    "restricted": 0,
                    "discarded": 0,
                },
            )

        if source_format is not None:
            metadata["source_format"] = source_format
        if extra_metadata:
            metadata.update(extra_metadata)
        try:
            result = IngestionService(
                self.database, settings=self.settings
            ).ingest_content(
                connector_id,
                source_locator=source_locator,
                content=governed.normalized_content(),
                source_digest=digest,
                extractor=DeterministicExtractorAdapter(),
                payload_metadata=metadata,
            )
        except Exception:
            if inserted and binding_id is not None:
                with self.database.session_factory.begin() as session:
                    binding = session.get(EnvelopeIdempotencyRecord, binding_id)
                    if binding is not None and binding.source_revision_id is None:
                        session.delete(binding)
            raise
        if binding_id is not None:
            with self.database.session_factory.begin() as session:
                binding = session.get(EnvelopeIdempotencyRecord, binding_id)
                if binding is not None:
                    binding.source_revision_id = result.source_revision_id
                    binding.operation_id = result.operation_id
        return EnvelopeIngestionResult(
            envelope_id=f"envelope_{envelope_identity[:55]}",
            source_revision_id=result.source_revision_id,
            operation_id=result.operation_id,
            duplicate=result.duplicate,
            counts=result.counts,
        )
