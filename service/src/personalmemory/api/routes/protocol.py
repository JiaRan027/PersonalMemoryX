from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from personalmemory.api.dependencies import require_session_token
from personalmemory.infrastructure.protocol.package import (
    MemoryProtocol,
    ProtocolIntegrityError,
    ProtocolRestoreError,
)

router = APIRouter(
    prefix="/protocol",
    tags=["protocol"],
    dependencies=[Depends(require_session_token)],
)


class PackageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_path: str


def package_path(payload: PackageRequest, *, must_exist: bool) -> Path:
    path = Path(payload.package_path).expanduser().resolve()
    if must_exist and not path.is_file():
        raise HTTPException(404, detail={"code": "PACKAGE_NOT_FOUND"})
    if not must_exist:
        if path.is_dir():
            timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
            candidate = path / f"personalmemory-{timestamp}.pmemory"
            sequence = 2
            while candidate.exists() or candidate.with_suffix(".md").exists():
                candidate = path / f"personalmemory-{timestamp}-{sequence}.pmemory"
                sequence += 1
            path = candidate
        elif not path.parent.is_dir():
            raise HTTPException(400, detail={"code": "PACKAGE_PARENT_NOT_FOUND"})
    return path


def report_dict(report) -> dict[str, object]:
    return {
        "valid": report.valid,
        "memory_count": report.memory_count,
        "memory_space_id": report.memory_space_id,
    }


@router.post("/export")
def export_package(payload: PackageRequest, request: Request) -> dict[str, object]:
    path = package_path(payload, must_exist=False)
    readable_path = path.with_suffix(".md")
    try:
        protocol = MemoryProtocol(request.app.state.database)
        report = protocol.export_space_with_readable(
            "space_default", path, readable_path
        )
    except FileExistsError as error:
        raise HTTPException(409, detail={"code": "PACKAGE_ALREADY_EXISTS"}) from error
    except OSError as error:
        raise HTTPException(400, detail={"code": "PACKAGE_EXPORT_FAILED"}) from error
    return {
        **report_dict(report),
        "package_path": str(path),
        "readable_path": str(readable_path),
    }


@router.post("/inspect")
def inspect_package(payload: PackageRequest, request: Request) -> dict[str, object]:
    path = package_path(payload, must_exist=True)
    try:
        report = MemoryProtocol(request.app.state.database).inspect(path)
    except ProtocolIntegrityError as error:
        code = (
            "PACKAGE_VERSION_UNSUPPORTED"
            if str(error) == "Unsupported protocol version"
            else "PACKAGE_INVALID"
        )
        raise HTTPException(400, detail={"code": code}) from error
    except (OSError, ValueError) as error:
        raise HTTPException(400, detail={"code": "PACKAGE_INVALID"}) from error
    return report_dict(report)


@router.post("/import")
def import_package(payload: PackageRequest, request: Request) -> dict[str, int]:
    path = package_path(payload, must_exist=True)
    try:
        report = MemoryProtocol(request.app.state.database).import_into_space(
            path,
            memory_space_id="space_default",
        )
    except (ProtocolIntegrityError, ProtocolRestoreError, OSError, ValueError) as error:
        raise HTTPException(400, detail={"code": "PACKAGE_INVALID"}) from error
    return {
        "imported": report.imported,
        "skipped_tombstoned": report.skipped_tombstoned,
        "skipped_existing": report.skipped_existing,
    }
