from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from personalmemory.api.dependencies import require_session_token
from personalmemory.application.capsule import CapsulePolicyError, ResumeCapsuleService
from personalmemory.domain.capsule import (
    CapsuleCheckpoint,
    CapsuleSelection,
    ResumeCapsule,
)

router = APIRouter(
    prefix="/capsules",
    tags=["capsules"],
    dependencies=[Depends(require_session_token)],
)


class CapsuleBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection: CapsuleSelection
    checkpoint: CapsuleCheckpoint = Field(default_factory=CapsuleCheckpoint)
    ttl_seconds: int = Field(default=3600, ge=60, le=86_400)
    token_budget: int = Field(default=1200, ge=256, le=1600)


class CapsuleValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capsule: ResumeCapsule


class CapsuleRebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capsule: ResumeCapsule
    ttl_seconds: int | None = Field(default=None, ge=60, le=86_400)


class CapsuleExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    capsule: ResumeCapsule
    path: str = Field(
        min_length=1,
        validation_alias=AliasChoices(
            "path", "output_path", "file_path", "capsule_path", "package_path"
        ),
    )


def capsule_response(capsule: ResumeCapsule) -> dict[str, object]:
    return {
        "capsule": capsule.model_dump(mode="json"),
        "delivery_text": ResumeCapsuleService.render(capsule),
    }


@router.post("")
def build_capsule(payload: CapsuleBuildRequest, request: Request) -> dict[str, object]:
    try:
        capsule = ResumeCapsuleService(
            request.app.state.database, data_dir=request.app.state.settings.data_dir
        ).build(
            payload.selection,
            payload.checkpoint,
            ttl_seconds=payload.ttl_seconds,
            token_budget=payload.token_budget,
        )
    except CapsulePolicyError as error:
        raise HTTPException(400, detail={"code": error.code}) from error
    return capsule_response(capsule)


@router.post("/validate")
def validate_capsule(payload: CapsuleValidateRequest, request: Request) -> dict[str, object]:
    result = ResumeCapsuleService(
        request.app.state.database, data_dir=request.app.state.settings.data_dir
    ).validate(payload.capsule)
    return result.model_dump(mode="json")


@router.post("/rebuild")
def rebuild_capsule(payload: CapsuleRebuildRequest, request: Request) -> dict[str, object]:
    try:
        capsule = ResumeCapsuleService(
            request.app.state.database, data_dir=request.app.state.settings.data_dir
        ).rebuild(
            payload.capsule, ttl_seconds=payload.ttl_seconds
        )
    except CapsulePolicyError as error:
        raise HTTPException(400, detail={"code": error.code}) from error
    return capsule_response(capsule)


def capsule_file_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_dir():
        timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        candidate = path / f"personalmemory-capsule-{timestamp}.md"
        sequence = 2
        while candidate.exists():
            candidate = path / f"personalmemory-capsule-{timestamp}-{sequence}.md"
            sequence += 1
        return candidate
    if not path.parent.is_dir():
        raise HTTPException(400, detail={"code": "CAPSULE_PARENT_NOT_FOUND"})
    return path


@router.post("/export")
def export_capsule(
    payload: CapsuleExportRequest, request: Request
) -> dict[str, object]:
    path = capsule_file_path(payload.path)
    try:
        result = ResumeCapsuleService(
            request.app.state.database, data_dir=request.app.state.settings.data_dir
        ).export_file(
            payload.capsule, path
        )
    except CapsulePolicyError as error:
        if error.code == "CAPSULE_INVALID" and error.validation is not None:
            raise HTTPException(
                409,
                detail={
                    "code": error.code,
                    "validation": error.validation.model_dump(mode="json"),
                },
            ) from error
        if error.code == "CAPSULE_FILE_EXISTS":
            raise HTTPException(409, detail={"code": error.code}) from error
        raise HTTPException(400, detail={"code": error.code}) from error
    validation = result.validation.model_dump(mode="json")
    return {
        "path": str(result.path),
        "output_path": str(result.path),
        "file_path": str(result.path),
        "capsule_path": str(result.path),
        "bytes_written": result.bytes_written,
        "capsule_id": payload.capsule.capsule_id,
        "delivery_text": ResumeCapsuleService.render(payload.capsule),
        "validation": validation,
        **validation,
    }
