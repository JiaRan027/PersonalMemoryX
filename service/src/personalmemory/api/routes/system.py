from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict

from personalmemory.api.dependencies import require_session_token

router = APIRouter(
    prefix="/system",
    tags=["system"],
    dependencies=[Depends(require_session_token)],
)


class RuntimeControlPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sync_paused: bool | None = None
    privacy_mode: bool | None = None


def runtime_control_dict(control) -> dict[str, bool]:
    return {
        "sync_paused": control.sync_paused,
        "privacy_mode": control.privacy_mode,
    }


@router.post("/shutdown", status_code=status.HTTP_202_ACCEPTED)
def shutdown(request: Request) -> None:
    callback: Callable[[], None] = request.app.state.request_shutdown
    callback()


@router.get("/runtime")
def get_runtime_control(request: Request) -> dict[str, bool]:
    control = request.app.state.sync_coordinator.runtime_control()
    return runtime_control_dict(control)


@router.patch("/runtime")
def update_runtime_control(
    payload: RuntimeControlPatch, request: Request
) -> dict[str, bool]:
    control = request.app.state.sync_coordinator.update_runtime_control(
        sync_paused=payload.sync_paused,
        privacy_mode=payload.privacy_mode,
    )
    return runtime_control_dict(control)


@router.post("/sync")
def sync_now(request: Request) -> dict[str, int]:
    result = request.app.state.sync_coordinator.sync_all_once(force=True)
    return {
        "ingested": result.ingested,
        "projected": result.projected,
        "failed": result.failed,
    }
