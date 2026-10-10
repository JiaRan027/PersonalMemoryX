from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select

from personalmemory.api.dependencies import require_session_token
from personalmemory.application.hook_receipts import load_hook_receipts
from personalmemory.application.ingestion import new_id
from personalmemory.domain.connector.profile import get_builtin_profile
from personalmemory.domain.recognition.filesystem_recognizer import FilesystemAgentMemoryRecognizer
from personalmemory.infrastructure.database.models import (
    ActivityRecord,
    AgentTurnObservationRecord,
    ConnectorInstanceRecord,
    ConnectorIssueRecord,
    LoopGuardRecord,
    MemoryCandidateRecord,
    ProjectionReceiptRecord,
    SourceRevisionRecord,
)
from personalmemory.infrastructure.filesystem.writer import hash_bytes

router = APIRouter(
    prefix="/connectors",
    tags=["connectors"],
    dependencies=[Depends(require_session_token)],
)


class RecognizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roots: dict[str, str] | None = None


class ConnectorPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    source_path: str | None = None
    target_path: str | None = None
    read_enabled: bool
    write_enabled: bool


class ConnectorCreateRequest(ConnectorPreviewRequest):
    name: str
    namespace: str = "general"


class ConnectorPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read_enabled: bool | None = None
    write_enabled: bool | None = None
    enabled: bool | None = None


def validate_paths(payload: ConnectorPreviewRequest) -> tuple[Path | None, Path | None]:
    profile = get_builtin_profile(payload.profile_id)
    can_read = (
        profile.capabilities.read_memory_file
        or profile.capabilities.observe_via_hook
        or profile.capabilities.accept_envelopes
    )
    if payload.read_enabled and not can_read:
        raise HTTPException(400, detail={"code": "PROFILE_CANNOT_READ"})
    if payload.write_enabled and not profile.capabilities.write_memory_file:
        raise HTTPException(400, detail={"code": "PROFILE_CANNOT_WRITE"})

    source = Path(payload.source_path).resolve() if payload.source_path else None
    target = Path(payload.target_path).resolve() if payload.target_path else None
    if payload.read_enabled:
        if profile.capabilities.accept_envelopes:
            if source is not None:
                raise HTTPException(400, detail={"code": "ENVELOPE_SOURCE_PATH_NOT_ALLOWED"})
        elif profile.capabilities.observe_via_hook:
            if source is None or not source.is_dir():
                raise HTTPException(400, detail={"code": "PROJECT_ROOT_NOT_FOUND"})
        else:
            if source is None or not source.is_file():
                raise HTTPException(400, detail={"code": "SOURCE_FILE_NOT_FOUND"})
            if source.stat().st_size > profile.max_bytes:
                raise HTTPException(400, detail={"code": "SOURCE_TOO_LARGE"})
    if payload.write_enabled:
        if target is None:
            raise HTTPException(400, detail={"code": "TARGET_FILE_REQUIRED"})
        if target.exists() and not target.is_file():
            raise HTTPException(400, detail={"code": "TARGET_NOT_FILE"})
        if not target.parent.exists():
            raise HTTPException(400, detail={"code": "TARGET_PARENT_NOT_FOUND"})
    return source, target


def connector_dict(connector: ConnectorInstanceRecord) -> dict[str, object]:
    return {
        "id": connector.id,
        "name": connector.name,
        "profile_id": connector.profile_id,
        "background_sync": get_builtin_profile(connector.profile_id).watch_source_changes,
        "source_path": connector.source_path,
        "target_path": connector.target_path,
        "namespace": connector.namespace,
        "capabilities": {"read": connector.can_read, "write": connector.can_write},
        "read_enabled": connector.read_enabled,
        "write_enabled": connector.write_enabled,
        "enabled": connector.enabled,
        "status": connector.status,
    }


@router.post("/preview")
def preview_connector(payload: ConnectorPreviewRequest) -> dict[str, object]:
    source, target = validate_paths(payload)
    profile = get_builtin_profile(payload.profile_id)
    return {
        "profile_id": profile.profile_id,
        "source_path": str(source) if source else None,
        "target_path": str(target) if target else None,
        "capabilities": {
            "read": (
                profile.capabilities.read_memory_file
                or profile.capabilities.observe_via_hook
                or profile.capabilities.accept_envelopes
            ),
            "write": profile.capabilities.write_memory_file,
        },
        "managed_write": payload.write_enabled,
    }


@router.post("/recognize")
def recognize_agent_memories(payload: RecognizeRequest) -> dict[str, object]:
    roots: dict[str, str] | None = None
    if payload.roots:
        unknown = set(payload.roots) - {"home", "appdata", "localappdata"}
        if unknown:
            raise HTTPException(400, detail={"code": "UNKNOWN_ROOT", "roots": sorted(unknown)})
        roots = payload.roots
    result = FilesystemAgentMemoryRecognizer(roots=roots).recognize()
    return result.as_dict()


@router.post("", status_code=status.HTTP_201_CREATED)
def create_connector(payload: ConnectorCreateRequest, request: Request) -> dict[str, object]:
    source, target = validate_paths(payload)
    profile = get_builtin_profile(payload.profile_id)
    connector = ConnectorInstanceRecord(
        id=new_id("connector"),
        memory_space_id="space_default",
        profile_id=profile.profile_id,
        name=payload.name.strip(),
        source_path=str(source) if source else None,
        target_path=str(target) if target else None,
        namespace=payload.namespace.strip() or "general",
        can_read=(
            profile.capabilities.read_memory_file
            or profile.capabilities.observe_via_hook
            or profile.capabilities.accept_envelopes
        ),
        can_write=profile.capabilities.write_memory_file,
        read_enabled=payload.read_enabled,
        write_enabled=payload.write_enabled,
        last_target_hash=(
            hash_bytes(target.read_bytes())
            if target is not None and target.exists() and payload.write_enabled
            else None
        ),
    )
    with request.app.state.database.session_factory.begin() as session:
        session.add(connector)
        session.flush()
        response = connector_dict(connector)
    if connector.write_enabled:
        request.app.state.sync_coordinator.request_projection_refresh()
    return response


@router.get("")
def list_connectors(request: Request) -> dict[str, object]:
    receipts = load_hook_receipts(request.app.state.settings.data_dir)
    with request.app.state.database.session_factory() as session:
        connectors = session.scalars(
            select(ConnectorInstanceRecord).order_by(ConnectorInstanceRecord.created_at)
        ).all()
        return {"items": [dict(connector_dict(item), hook_receipt=receipts.get(item.id))
                          for item in connectors]}


@router.patch("/{connector_id}")
def patch_connector(
    connector_id: str, payload: ConnectorPatchRequest, request: Request
) -> dict[str, object]:
    with request.app.state.database.session_factory.begin() as session:
        connector = session.get(ConnectorInstanceRecord, connector_id)
        if connector is None:
            raise HTTPException(404, detail={"code": "CONNECTOR_NOT_FOUND"})
        if payload.read_enabled is not None:
            if payload.read_enabled and not connector.can_read:
                raise HTTPException(400, detail={"code": "PROFILE_CANNOT_READ"})
            connector.read_enabled = payload.read_enabled
        if payload.write_enabled is not None:
            if payload.write_enabled and not connector.can_write:
                raise HTTPException(400, detail={"code": "PROFILE_CANNOT_WRITE"})
            connector.write_enabled = payload.write_enabled
        if payload.enabled is not None:
            connector.enabled = payload.enabled
        connector.status = "healthy" if connector.enabled else "paused"
        session.flush()
        response = connector_dict(connector)
    if payload.write_enabled:
        request.app.state.sync_coordinator.request_projection_refresh()
    return response


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connector(connector_id: str, request: Request) -> Response:
    """移除连接器：只删除连接器及其派生记录，Memory Core 数据保持完整。"""
    with request.app.state.database.session_factory.begin() as session:
        connector = session.get(ConnectorInstanceRecord, connector_id)
        if connector is None:
            raise HTTPException(404, detail={"code": "CONNECTOR_NOT_FOUND"})
        revision_ids = select(SourceRevisionRecord.id).where(
            SourceRevisionRecord.connector_id == connector_id
        )
        session.execute(
            delete(AgentTurnObservationRecord).where(
                AgentTurnObservationRecord.connector_id == connector_id
            )
        )
        session.execute(
            delete(MemoryCandidateRecord).where(
                MemoryCandidateRecord.source_revision_id.in_(revision_ids)
            )
        )
        for model in (
            ConnectorIssueRecord,
            SourceRevisionRecord,
            ProjectionReceiptRecord,
            LoopGuardRecord,
        ):
            session.execute(delete(model).where(model.connector_id == connector_id))
        session.execute(delete(ActivityRecord).where(ActivityRecord.connector_id == connector_id))
        session.delete(connector)
    request.app.state.sync_coordinator.forget_connector(connector_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{connector_id}/sync")
def sync_connector(connector_id: str, request: Request) -> dict[str, int]:
    with request.app.state.database.session_factory() as session:
        if session.get(ConnectorInstanceRecord, connector_id) is None:
            raise HTTPException(404, detail={"code": "CONNECTOR_NOT_FOUND"})
    try:
        result = request.app.state.sync_coordinator.sync_connector(connector_id)
    except PermissionError as error:
        raise HTTPException(409, detail={"code": "CONNECTOR_CANNOT_SYNC"}) from error
    except (OSError, UnicodeError, ValueError):
        return {"ingested": 0, "projected": 0, "failed": 1, "accepted": 0}
    return {
        "ingested": result.ingested,
        "projected": result.projected,
        "failed": result.failed,
        "accepted": result.accepted,
    }
