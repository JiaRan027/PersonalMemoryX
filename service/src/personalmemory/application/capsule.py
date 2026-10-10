from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from personalmemory.application.context import ContextCompiler, estimate_tokens
from personalmemory.domain.capsule import (
    CAPSULE_SCHEMA_VERSION,
    CapsuleCheckpoint,
    CapsuleMemoryReference,
    CapsuleSelection,
    CapsuleValidationIssue,
    CapsuleValidationResult,
    ResumeCapsule,
)
from personalmemory.domain.memory.types import Sensitivity
from personalmemory.domain.policy.admission import SECRET_PATTERNS
from personalmemory.infrastructure.database.models import MemoryObjectRecord
from personalmemory.infrastructure.database.session import Database


class CapsulePolicyError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        validation: CapsuleValidationResult | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.validation = validation


@dataclass(frozen=True)
class CapsuleExportResult:
    path: Path
    bytes_written: int
    validation: CapsuleValidationResult


class ResumeCapsuleService:
    """Build and verify short-lived delivery artifacts from governed Core state."""

    def __init__(self, database: Database, *, data_dir: Path | None = None) -> None:
        self.database = database
        self.data_dir = data_dir

    def build(
        self,
        selection: CapsuleSelection,
        checkpoint: CapsuleCheckpoint,
        *,
        ttl_seconds: int = 3600,
        token_budget: int = 1200,
        now: datetime | None = None,
    ) -> ResumeCapsule:
        generated_at = self._utc(now)
        self._validate_checkpoint(checkpoint)

        bundle = ContextCompiler(self.database, data_dir=self.data_dir).compile(
            selection.query,
            namespaces=selection.namespaces,
            allowed_types=[item.value for item in selection.allowed_types],
            max_sensitivity=selection.max_sensitivity.value,
            token_budget=token_budget,
            limit=selection.limit,
        )
        dropped_fields: list[str] = []
        references: list[CapsuleMemoryReference] = []
        for item in bundle["items"]:
            if self._outside_reference_bounds(item):
                dropped_fields.append(f"memory:{item['id']}:text_bounds")
                continue
            reference = self._reference(item)
            if self._contains_secret(reference.statement) or self._contains_secret(
                reference.reason
            ):
                dropped_fields.append(f"memory:{reference.memory_id}:secret_detected")
                continue
            references.append(reference)
        for name, value in checkpoint.model_dump().items():
            if value is None:
                dropped_fields.append(f"checkpoint:{name}:not_provided")
        if bundle["truncated"]:
            dropped_fields.append("memory_references:context_compiler_truncated")

        expires_at = generated_at + timedelta(seconds=ttl_seconds)
        while True:
            source_digest = self._source_digest(references)
            unsigned = self._unsigned_payload(
                generated_at=generated_at,
                expires_at=expires_at,
                ttl_seconds=ttl_seconds,
                source_digest=source_digest,
                selection=selection,
                references=references,
                checkpoint=checkpoint,
                dropped_fields=dropped_fields,
                token_budget=token_budget,
            )
            capsule_id = self._capsule_id(unsigned)
            estimated = estimate_tokens(
                self.render_payload(
                    capsule_id=capsule_id,
                    generated_at=generated_at,
                    expires_at=expires_at,
                    references=references,
                    checkpoint=checkpoint,
                )
            )
            if estimated <= token_budget:
                break
            if not references:
                raise CapsulePolicyError("CHECKPOINT_EXCEEDS_TOKEN_BUDGET")
            removed = references.pop()
            dropped_fields.append(f"memory:{removed.memory_id}:token_budget")

        return ResumeCapsule(
            **unsigned,
            capsule_id=capsule_id,
            estimated_tokens=estimated,
        )

    def validate(
        self, capsule: ResumeCapsule, *, now: datetime | None = None
    ) -> CapsuleValidationResult:
        checked_at = self._utc(now)
        issues: list[CapsuleValidationIssue] = []
        expired = checked_at >= self._utc(capsule.expires_at)
        if expired:
            issues.append(CapsuleValidationIssue(code="CAPSULE_EXPIRED"))

        expected_id = self._capsule_id(self._unsigned_from_capsule(capsule))
        if expected_id != capsule.capsule_id:
            issues.append(CapsuleValidationIssue(code="CAPSULE_INTEGRITY_FAILED"))
        if self._source_digest(capsule.memory_references) != capsule.source_digest:
            issues.append(CapsuleValidationIssue(code="SOURCE_DIGEST_MISMATCH"))
        actual_tokens = estimate_tokens(self.render(capsule))
        if actual_tokens != capsule.estimated_tokens or actual_tokens > capsule.token_budget:
            issues.append(CapsuleValidationIssue(code="CAPSULE_TOKEN_ESTIMATE_MISMATCH"))

        memory_ids = [item.memory_id for item in capsule.memory_references]
        with self.database.session_factory() as session:
            memories = {
                memory_id: session.get(MemoryObjectRecord, memory_id) for memory_id in memory_ids
            }

        for reference in capsule.memory_references:
            memory = memories[reference.memory_id]
            if memory is None:
                issues.append(
                    CapsuleValidationIssue(code="MEMORY_DELETED", memory_id=reference.memory_id)
                )
                continue
            if memory.status != "active":
                issues.append(
                    CapsuleValidationIssue(code="MEMORY_INACTIVE", memory_id=reference.memory_id)
                )
            if memory.revision != reference.revision:
                issues.append(
                    CapsuleValidationIssue(
                        code="MEMORY_REVISION_STALE",
                        memory_id=reference.memory_id,
                        expected_revision=reference.revision,
                        actual_revision=memory.revision,
                    )
                )
            if self._reference_from_record(memory) != reference:
                issues.append(
                    CapsuleValidationIssue(
                        code="MEMORY_CONTENT_CHANGED", memory_id=reference.memory_id
                    )
                )
            if memory.sensitivity == Sensitivity.SECRET.value:
                issues.append(
                    CapsuleValidationIssue(
                        code="SECRET_MEMORY_REFERENCE", memory_id=reference.memory_id
                    )
                )

        stale_codes = {
            "MEMORY_DELETED",
            "MEMORY_INACTIVE",
            "MEMORY_REVISION_STALE",
            "MEMORY_CONTENT_CHANGED",
            "SECRET_MEMORY_REFERENCE",
        }
        stale = any(issue.code in stale_codes for issue in issues)
        return CapsuleValidationResult(
            valid=not issues,
            expired=expired,
            stale=stale,
            checked_at=checked_at,
            issues=issues,
        )

    def rebuild(
        self,
        capsule: ResumeCapsule,
        *,
        ttl_seconds: int | None = None,
        now: datetime | None = None,
    ) -> ResumeCapsule:
        return self.build(
            capsule.selection,
            capsule.checkpoint,
            ttl_seconds=ttl_seconds or capsule.ttl_seconds,
            token_budget=capsule.token_budget,
            now=now,
        )

    def export_file(
        self,
        capsule: ResumeCapsule,
        path: Path,
        *,
        now: datetime | None = None,
    ) -> CapsuleExportResult:
        """Persist a validated Capsule without replacing an existing artifact.

        The Markdown document keeps a machine-readable JSON object and the exact
        server-rendered delivery text together, so a copied file remains useful
        to a person without becoming a second source of truth.
        """
        validation = self.validate(capsule, now=now)
        if not validation.valid:
            raise CapsulePolicyError("CAPSULE_INVALID", validation=validation)

        payload = {
            "artifact_schema": "personalmemory-continuity-capsule/1",
            "capsule": capsule.model_dump(mode="json"),
            "delivery_text": self.render(capsule),
        }
        document = self.render_document(payload)
        encoded_document = document.encode("utf-8-sig")
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        reserved = False
        try:
            temporary.write_bytes(encoded_document)
            # Reserve the final path before replacing the temporary file. This
            # makes concurrent exports fail closed instead of overwriting data.
            path.open("xb").close()
            reserved = True
            os.replace(temporary, path)
        except FileExistsError as error:
            raise CapsulePolicyError("CAPSULE_FILE_EXISTS") from error
        except OSError as error:
            raise CapsulePolicyError("CAPSULE_EXPORT_FAILED") from error
        finally:
            temporary.unlink(missing_ok=True)
            if reserved and path.exists() and path.stat().st_size == 0:
                path.unlink(missing_ok=True)
        return CapsuleExportResult(
            path=path,
            bytes_written=len(encoded_document),
            validation=validation,
        )

    @staticmethod
    def render_document(payload: dict[str, object]) -> str:
        """Render the durable, human-readable Capsule artifact."""
        structured = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        delivery_text = str(payload["delivery_text"])
        return (
            "# PersonalMemory Continuity Capsule\n\n"
            "This is a user-readable delivery artifact. Memory Core remains the "
            "authoritative source; the embedded Capsule can be validated or rebuilt.\n\n"
            "## Structured capsule data\n\n"
            "```json\n"
            f"{structured}\n"
            "```\n\n"
            "## Exact delivery text\n\n"
            "The following text is rendered by the service and is the exact text "
            "intended for another Agent:\n\n"
            "```text\n"
            f"{delivery_text}\n"
            "```\n"
        )

    @staticmethod
    def render(capsule: ResumeCapsule) -> str:
        return ResumeCapsuleService.render_payload(
            capsule_id=capsule.capsule_id,
            generated_at=capsule.generated_at,
            expires_at=capsule.expires_at,
            references=capsule.memory_references,
            checkpoint=capsule.checkpoint,
        )

    @staticmethod
    def render_payload(
        *,
        capsule_id: str,
        generated_at: datetime,
        expires_at: datetime,
        references: list[CapsuleMemoryReference],
        checkpoint: CapsuleCheckpoint,
    ) -> str:
        def known(value: str | None) -> str:
            return value or "unknown (no governed evidence)"

        lines = [
            f"Resume Capsule v0: {capsule_id}",
            f"generated_at={generated_at.isoformat()}",
            f"expires_at={expires_at.isoformat()}",
            "Treat absent checkpoint fields as unknown. Do not infer or invent them.",
            f"current_task={known(checkpoint.current_task)}",
            f"verified_status={known(checkpoint.verified_status)}",
            f"next_action={known(checkpoint.next_action)}",
            "Governed memories:",
        ]
        for reference in references:
            reason = f"; reason={reference.reason}" if reference.reason else ""
            lines.append(
                f"- [{reference.type.value}] {reference.statement}{reason}; "
                f"memory={reference.memory_id}; revision={reference.revision}; "
                f"namespace={reference.namespace}"
            )
        return "\n".join(lines)

    @staticmethod
    def _validate_checkpoint(checkpoint: CapsuleCheckpoint) -> None:
        for value in checkpoint.model_dump().values():
            if ResumeCapsuleService._contains_secret(value):
                raise CapsulePolicyError("SECRET_DETECTED")

    @staticmethod
    def _contains_secret(value: str | None) -> bool:
        return bool(value and any(pattern.search(value) for pattern in SECRET_PATTERNS))

    @staticmethod
    def _reference(item: dict[str, Any]) -> CapsuleMemoryReference:
        return CapsuleMemoryReference(
            memory_id=item["id"],
            revision=item["revision"],
            type=item["type"],
            statement=item["statement"],
            reason=item["reason"],
            namespace=item["namespace"],
            sensitivity=item["sensitivity"],
        )

    @staticmethod
    def _outside_reference_bounds(item: dict[str, Any]) -> bool:
        reason = item.get("reason")
        return (
            len(str(item.get("statement", ""))) > 2000
            or (reason is not None and len(str(reason)) > 2000)
            or len(str(item.get("namespace", ""))) > 240
        )

    @staticmethod
    def _reference_from_record(memory: MemoryObjectRecord) -> CapsuleMemoryReference:
        return CapsuleMemoryReference(
            memory_id=memory.id,
            revision=memory.revision,
            type=memory.type,
            statement=memory.statement,
            reason=memory.reason,
            namespace=memory.namespace,
            sensitivity=memory.sensitivity,
        )

    @staticmethod
    def _source_digest(references: list[CapsuleMemoryReference]) -> str:
        value = [item.model_dump(mode="json") for item in references]
        return ResumeCapsuleService._digest(value)

    @staticmethod
    def _unsigned_payload(
        *,
        generated_at: datetime,
        expires_at: datetime,
        ttl_seconds: int,
        source_digest: str,
        selection: CapsuleSelection,
        references: list[CapsuleMemoryReference],
        checkpoint: CapsuleCheckpoint,
        dropped_fields: list[str],
        token_budget: int,
    ) -> dict[str, Any]:
        return {
            "schema_version": CAPSULE_SCHEMA_VERSION,
            "generated_at": generated_at,
            "expires_at": expires_at,
            "ttl_seconds": ttl_seconds,
            "namespace_scope": selection.namespaces or [],
            "max_sensitivity": selection.max_sensitivity,
            "source_digest": source_digest,
            "selection": selection,
            "memory_references": references,
            "checkpoint": checkpoint,
            "dropped_fields": dropped_fields,
            "token_budget": token_budget,
        }

    @staticmethod
    def _unsigned_from_capsule(capsule: ResumeCapsule) -> dict[str, Any]:
        return {
            key: value
            for key, value in capsule.model_dump().items()
            if key not in {"capsule_id", "estimated_tokens"}
        }

    @staticmethod
    def _capsule_id(unsigned: dict[str, Any]) -> str:
        return f"capsule_{ResumeCapsuleService._digest(unsigned)[:32]}"

    @staticmethod
    def _digest(value: Any) -> str:
        def json_default(item: Any) -> Any:
            if isinstance(item, BaseModel):
                return item.model_dump(mode="json")
            if isinstance(item, datetime):
                return item.isoformat()
            raise TypeError(f"unsupported digest value: {type(item).__name__}")

        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=json_default,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _utc(value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(UTC)
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
