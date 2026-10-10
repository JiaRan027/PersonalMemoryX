from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from threading import RLock

from sqlalchemy import delete, select

from personalmemory.application.envelope import ContinuityEnvelopeService
from personalmemory.application.ingestion import IngestionService, content_hash
from personalmemory.domain.connector.profile import get_builtin_profile
from personalmemory.domain.continuity import ContinuityEnvelope, SourceCapabilities
from personalmemory.domain.observation import InteractionFragment
from personalmemory.infrastructure.database.models import (
    AgentTurnObservationRecord,
    ConnectorInstanceRecord,
    utc_now,
)
from personalmemory.infrastructure.database.session import Database

PENDING_OBSERVATION_TTL = timedelta(hours=24)
_OBSERVATION_LOCK = RLock()


class ObservationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ObservationNotFoundError(ObservationError):
    pass


class ObservationConflictError(ObservationError):
    pass


class ObservationScopeError(ObservationError):
    pass


@dataclass(frozen=True)
class ObservationResult:
    observation_id: str
    status: str
    duplicate: bool
    source_revision_id: str | None
    counts: dict[str, int]


def stable_observation_id(event: InteractionFragment, *, prompt_id: str | None = None) -> str:
    correlation_id = prompt_id or event.prompt_id
    if correlation_id is None:
        raise ObservationConflictError(
            "OBSERVATION_CORRELATION_MISSING",
            "Interaction fragment has no unambiguous correlation identifier",
        )
    identity = "\0".join((event.connector_id, event.agent_family, event.session_id, correlation_id))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"observation_{digest[:56]}"


def _path_within(path_value: str, root_value: str) -> bool:
    path = Path(path_value).resolve(strict=False)
    root = Path(root_value).resolve(strict=False)
    return path == root or root in path.parents


def _same_path(left: str, right: str) -> bool:
    return os.path.normcase(str(Path(left).resolve(strict=False))) == os.path.normcase(
        str(Path(right).resolve(strict=False))
    )


class ObservationService:
    def __init__(self, database: Database, settings=None) -> None:
        self.database = database
        self.settings = settings

    def observe_agent_turn(self, event: InteractionFragment) -> ObservationResult:
        with _OBSERVATION_LOCK:
            return self._observe_agent_turn(event)

    def _observe_agent_turn(self, event: InteractionFragment) -> ObservationResult:
        incoming_text = event.user_text or event.assistant_text or ""
        incoming_hash = content_hash(incoming_text)

        with self.database.session_factory.begin() as session:
            session.execute(
                delete(AgentTurnObservationRecord).where(
                    AgentTurnObservationRecord.status == "pending",
                    AgentTurnObservationRecord.updated_at < utc_now() - PENDING_OBSERVATION_TTL,
                )
            )
            connector = session.get(ConnectorInstanceRecord, event.connector_id)
            if connector is None:
                raise ObservationNotFoundError(
                    "CONNECTOR_NOT_FOUND", "Observation connector does not exist"
                )
            profile = get_builtin_profile(connector.profile_id)
            if (
                not profile.capabilities.observe_via_hook
                or profile.agent_family != event.agent_family
            ):
                raise ObservationConflictError(
                    "CONNECTOR_PROFILE_MISMATCH",
                    "Connector does not accept Claude Code hook events",
                )
            if not connector.enabled or not connector.read_enabled:
                raise ObservationConflictError(
                    "CONNECTOR_DISABLED", "Observation connector is disabled"
                )
            if not connector.source_path or not _path_within(event.cwd, connector.source_path):
                raise ObservationScopeError(
                    "PROJECT_SCOPE_DENIED",
                    "Hook working directory is outside the authorized project root",
                )

            prompt_id = event.prompt_id
            orphan: AgentTurnObservationRecord | None = None
            if prompt_id is None:
                pending = session.scalars(
                    select(AgentTurnObservationRecord)
                    .where(
                        AgentTurnObservationRecord.connector_id == event.connector_id,
                        AgentTurnObservationRecord.agent_family == event.agent_family,
                        AgentTurnObservationRecord.session_id == event.session_id,
                        AgentTurnObservationRecord.status == "pending",
                        AgentTurnObservationRecord.assistant_hash.is_(None),
                    )
                    .order_by(AgentTurnObservationRecord.updated_at.desc())
                    .limit(2)
                ).all()
                if len(pending) > 1:
                    raise ObservationConflictError(
                        "OBSERVATION_CORRELATION_AMBIGUOUS",
                        "Fragment without correlation ID matched multiple pending interactions",
                    )
                if len(pending) == 1:
                    prompt_id = pending[0].prompt_id
                else:
                    processed = session.scalar(
                        select(AgentTurnObservationRecord).where(
                            AgentTurnObservationRecord.connector_id == event.connector_id,
                            AgentTurnObservationRecord.agent_family == event.agent_family,
                            AgentTurnObservationRecord.session_id == event.session_id,
                            AgentTurnObservationRecord.status == "processed",
                            AgentTurnObservationRecord.assistant_hash == incoming_hash,
                        )
                    )
                    if processed is not None:
                        return ObservationResult(
                            observation_id=processed.id,
                            status="processed",
                            duplicate=True,
                            source_revision_id=processed.source_revision_id,
                            counts=IngestionService._empty_counts(),
                        )
                    prompt_id = f"orphan:{incoming_hash[:32]}"
            elif event.fragment_kind == "user_message":
                orphans = session.scalars(
                    select(AgentTurnObservationRecord)
                    .where(
                        AgentTurnObservationRecord.connector_id == event.connector_id,
                        AgentTurnObservationRecord.agent_family == event.agent_family,
                        AgentTurnObservationRecord.session_id == event.session_id,
                        AgentTurnObservationRecord.status == "pending",
                        AgentTurnObservationRecord.prompt_id.like("orphan:%"),
                        AgentTurnObservationRecord.user_hash.is_(None),
                        AgentTurnObservationRecord.assistant_hash.is_not(None),
                    )
                    .order_by(AgentTurnObservationRecord.updated_at.desc())
                    .limit(2)
                ).all()
                if len(orphans) == 1:
                    orphan = orphans[0]
            observation_id = stable_observation_id(event, prompt_id=prompt_id)

            observation = session.get(AgentTurnObservationRecord, observation_id)
            if observation is None:
                observation = AgentTurnObservationRecord(
                    id=observation_id,
                    connector_id=event.connector_id,
                    agent_family=event.agent_family,
                    session_id=event.session_id,
                    prompt_id=prompt_id,
                    cwd=event.cwd,
                    transcript_path=event.transcript_path,
                    assistant_text=orphan.assistant_text if orphan is not None else None,
                    assistant_hash=orphan.assistant_hash if orphan is not None else None,
                    status="pending",
                )
                session.add(observation)
                if orphan is not None:
                    if not _same_path(orphan.cwd, event.cwd) or not _same_path(
                        orphan.transcript_path, event.transcript_path
                    ):
                        raise ObservationConflictError(
                            "OBSERVATION_CONTENT_CONFLICT",
                            "Orphan fragment provenance did not match the user interaction",
                        )
                    session.delete(orphan)
            elif not _same_path(observation.cwd, event.cwd) or not _same_path(
                observation.transcript_path, event.transcript_path
            ):
                raise ObservationConflictError(
                    "OBSERVATION_CONTENT_CONFLICT",
                    "Observation identity was reused with different provenance",
                )

            text_field = "user_text" if event.fragment_kind == "user_message" else "assistant_text"
            hash_field = "user_hash" if event.fragment_kind == "user_message" else "assistant_hash"
            existing_hash = getattr(observation, hash_field)
            if existing_hash is not None and existing_hash != incoming_hash:
                raise ObservationConflictError(
                    "OBSERVATION_CONTENT_CONFLICT",
                    "Observation identity was reused with different content",
                )
            if observation.status == "processed":
                return ObservationResult(
                    observation_id=observation.id,
                    status="processed",
                    duplicate=True,
                    source_revision_id=observation.source_revision_id,
                    counts=IngestionService._empty_counts(),
                )

            setattr(observation, hash_field, incoming_hash)
            setattr(observation, text_field, incoming_text)
            session.flush()
            complete = bool(observation.user_text and observation.assistant_text)
            if not complete:
                return ObservationResult(
                    observation_id=observation.id,
                    status="pending",
                    duplicate=existing_hash == incoming_hash,
                    source_revision_id=None,
                    counts=IngestionService._empty_counts(),
                )
            user_text = observation.user_text
            assistant_text = observation.assistant_text

        envelope = ContinuityEnvelope.from_interaction(
            source_kind="interaction",
            source_family=event.agent_family,
            capture_mode="realtime_hook",
            capabilities=SourceCapabilities(
                has_actor_roles=True,
                has_session=True,
                has_timestamps=False,
                has_tool_calls=False,
                has_attachments=False,
                is_realtime=True,
            ),
            locator=f"{event.agent_family}://{event.session_id}/{prompt_id}",
            interaction_id=prompt_id,
            user_text=user_text,
            assistant_text=assistant_text,
            session_id=event.session_id,
            message_ids=(prompt_id,),
            namespace=connector.namespace,
        )
        normalized_content = envelope.normalized_content()
        ingestion = ContinuityEnvelopeService(
            self.database, settings=self.settings
        ).ingest(
            event.connector_id,
            envelope,
            extra_metadata={
                "observation_id": observation_id,
                "cwd": event.cwd,
                "transcript_path": event.transcript_path,
            },
        )

        with self.database.session_factory.begin() as session:
            observation = session.scalar(
                select(AgentTurnObservationRecord).where(
                    AgentTurnObservationRecord.id == observation_id
                )
            )
            if observation is None:
                raise ObservationConflictError(
                    "OBSERVATION_STATE_LOST", "Observation staging record disappeared"
                )
            observation.status = "processed"
            observation.source_revision_id = ingestion.source_revision_id
            observation.content_hash = content_hash(normalized_content)
            observation.user_text = None
            observation.assistant_text = None
            observation.processed_at = utc_now()

        return ObservationResult(
            observation_id=observation_id,
            status="processed",
            duplicate=False,
            source_revision_id=ingestion.source_revision_id,
            counts=ingestion.counts,
        )
