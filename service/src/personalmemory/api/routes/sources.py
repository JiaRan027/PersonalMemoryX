from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from personalmemory.api.dependencies import require_session_token
from personalmemory.application.candidate_governance import (
    draft_from_candidate,
    govern_source_draft,
)
from personalmemory.application.import_service import MAX_FILES_PER_IMPORT, ImportService
from personalmemory.domain.connector.profile import get_builtin_profile
from personalmemory.domain.extraction.deterministic import DeterministicExtractorAdapter
from personalmemory.domain.extraction.extractor import CandidateItem
from personalmemory.domain.memory.fingerprint import exact_content_fingerprint
from personalmemory.domain.memory.model import MemoryDraft
from personalmemory.domain.policy.admission import AutoAdmissionPolicy
from personalmemory.infrastructure.database.repositories import MemoryRepository
from personalmemory.infrastructure.filesystem.adapters import ADAPTER_FORMATS, decode_agent_memory
from personalmemory.infrastructure.filesystem.markdown import MarkdownMemoryParser

router = APIRouter(
    prefix="/sources",
    tags=["sources"],
    dependencies=[Depends(require_session_token)],
)

EXTRACTABLE_FORMATS = {"conversation", "markdown", *ADAPTER_FORMATS}


class ExtractPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    source_path: str
    namespace: str = "general"


def _candidate_dict(
    item: CandidateItem,
    *,
    namespace: str,
    database,
) -> dict[str, object]:
    start, end = item.evidence_span
    draft = MemoryDraft(
        type=item.type,
        statement=item.statement,
        reason=item.reason,
        source_revision_id="preview",
        confidence=item.confidence,
        importance=item.importance,
        namespace=namespace,
        payload=item.extra,
    )
    exact_fingerprint = exact_content_fingerprint(draft.type, draft.statement)
    with database.session_factory() as session:
        duplicate = MemoryRepository(session).cross_namespace_exact_match(
            "space_default", exact_fingerprint, namespace
        )
    decision = AutoAdmissionPolicy().evaluate(
        draft,
        has_cross_namespace_duplicate=duplicate is not None,
    )
    extra = dict(item.extra)
    if duplicate is not None:
        extra["duplicate_of_memory_id"] = duplicate.id
        extra["duplicate_namespace"] = duplicate.namespace
    return {
        "type": item.type.value,
        "statement": item.statement,
        "reason": item.reason,
        "confidence": item.confidence,
        "importance": item.importance,
        "evidence": {"start": start, "end": end},
        "source_signal": item.source_signal,
        "extra": extra,
        "admission_action": decision.action.value,
        "admission_reason_code": decision.reason_code,
    }


def _preview_file(
    source: Path,
    *,
    profile,
    namespace: str,
    source_label: str | None = None,
) -> tuple[list[CandidateItem], list[str], int]:
    if source.stat().st_size > profile.max_bytes:
        warning = "source exceeds profile size limit"
        return [], [f"{source_label}: {warning}" if source_label else warning], 0

    if profile.format in ADAPTER_FORMATS:
        content = decode_agent_memory(source, profile.format)
    else:
        content = source.read_bytes().decode(profile.encoding, errors="replace")
    if len(content.encode(profile.encoding)) > profile.max_bytes:
        warning = "decoded content exceeds profile size limit"
        return [], [f"{source_label}: {warning}" if source_label else warning], 0

    if profile.format == "markdown":
        parsed_drafts = MarkdownMemoryParser(profile).parse(
            content,
            source_revision_id="preview",
            namespace=namespace,
        )
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        drafts = [
            govern_source_draft(
                draft,
                profile_id=profile.profile_id,
                source_locator=source_label or str(source),
                source_digest=digest,
            )
            for draft in parsed_drafts
        ]
        items = [
            CandidateItem(
                type=draft.type,
                statement=draft.statement,
                reason=draft.reason,
                confidence=draft.confidence,
                importance=draft.importance,
                evidence_span=(
                    int(draft.payload.get("source_line_start", 0)),
                    int(draft.payload.get("source_line_end", 0)),
                ),
                source_signal="agent_memory",
                extra=dict(draft.payload),
            )
            for draft in drafts
        ]
        warnings: list[str] = []
    else:
        result = DeterministicExtractorAdapter().extract(
            content,
            source_revision_id="preview",
            namespace=namespace,
        )
        payload_metadata = {
            "source_format": profile.format,
            "source_family": profile.agent_family,
            "source_locator": source_label or str(source),
            "source_digest": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        items = []
        for item in result.items:
            draft = draft_from_candidate(
                item,
                content,
                source_revision_id="preview",
                namespace=namespace,
                payload_metadata=payload_metadata,
            )
            items.append(replace(item, extra=draft.payload))
        warnings = result.warnings
    if source_label is None:
        return items, warnings, len(content)
    items = [
        replace(item, extra={**item.extra, "source_path": source_label})
        for item in items
    ]
    return items, [f"{source_label}: {warning}" for warning in warnings], len(content)


def _markdown_preview_files(directory: Path) -> tuple[list[Path], list[str]]:
    files = sorted(
        (path for path in directory.rglob("*.md") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if len(files) <= MAX_FILES_PER_IMPORT:
        return files, []
    return files[:MAX_FILES_PER_IMPORT], [
        f"preview limited to {MAX_FILES_PER_IMPORT} markdown files"
    ]


class ImportSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    source_path: str
    namespace: str = "general"


@router.post("/import")
def import_source(payload: ImportSourceRequest, request: Request) -> dict[str, object]:
    """One-click import: file or directory -> connectors + memories."""
    namespace = payload.namespace.strip() or "general"
    try:
        result = ImportService(
            request.app.state.database,
            request.app.state.sync_coordinator,
        ).import_source(payload.profile_id, payload.source_path, namespace)
    except FileNotFoundError as error:
        raise HTTPException(400, detail={"code": "SOURCE_PATH_NOT_FOUND"}) from error
    except PermissionError as error:
        raise HTTPException(400, detail={"code": "PROFILE_CANNOT_READ"}) from error
    except ValueError as error:
        raise HTTPException(400, detail={"code": "UNKNOWN_PROFILE"}) from error
    return result.as_dict()


@router.post("/extract")
def extract_preview(payload: ExtractPreviewRequest, request: Request) -> dict[str, object]:
    """Dry-run extraction preview: returns candidates, never writes the DB."""
    try:
        profile = get_builtin_profile(payload.profile_id)
    except ValueError as error:
        raise HTTPException(400, detail={"code": "UNKNOWN_PROFILE"}) from error
    if not profile.capabilities.read_memory_file:
        raise HTTPException(400, detail={"code": "PROFILE_CANNOT_READ"})
    if profile.format not in EXTRACTABLE_FORMATS:
        raise HTTPException(400, detail={"code": "FORMAT_NOT_SUPPORTED", "format": profile.format})

    source = Path(payload.source_path).resolve()
    if not source.exists():
        raise HTTPException(400, detail={"code": "SOURCE_FILE_NOT_FOUND"})
    namespace = payload.namespace.strip() or "general"
    if source.is_file():
        if source.stat().st_size > profile.max_bytes:
            raise HTTPException(400, detail={"code": "SOURCE_TOO_LARGE"})
        items, warnings, char_count = _preview_file(
            source, profile=profile, namespace=namespace
        )
        byte_size = source.stat().st_size
    elif source.is_dir() and profile.format == "markdown":
        files, warnings = _markdown_preview_files(source)
        items: list[CandidateItem] = []
        char_count = byte_size = 0
        for file_path in files:
            label = str(file_path.relative_to(source))
            file_items, file_warnings, file_char_count = _preview_file(
                file_path,
                profile=profile,
                namespace=namespace,
                source_label=label,
            )
            items.extend(file_items)
            warnings.extend(file_warnings)
            char_count += file_char_count
            byte_size += file_path.stat().st_size
    else:
        raise HTTPException(400, detail={"code": "SOURCE_FILE_NOT_FOUND"})
    return {
        "dry_run": True,
        "profile_id": profile.profile_id,
        "format": profile.format,
        "source_path": str(source),
        "byte_size": byte_size,
        "char_count": char_count,
        "items": [
            _candidate_dict(
                item,
                namespace=namespace,
                database=request.app.state.database,
            )
            for item in items
        ],
        "warnings": warnings,
    }
