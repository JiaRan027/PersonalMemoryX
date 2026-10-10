from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from personalmemory.api.dependencies import require_session_token
from personalmemory.application.undo import MemoryOperations
from personalmemory.infrastructure.database.models import ActivityRecord

router = APIRouter(
    prefix="/activities",
    tags=["activities"],
    dependencies=[Depends(require_session_token)],
)


def utc_isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def activity_dict(activity: ActivityRecord) -> dict[str, object]:
    return {
        "id": activity.id,
        "operation_id": activity.operation_id,
        "connector_id": activity.connector_id,
        "kind": activity.kind,
        "status": activity.status,
        "summary": activity.summary,
        "details": activity.details,
        "created_at": utc_isoformat(activity.created_at),
    }


@router.get("")
def list_activities(request: Request) -> dict[str, object]:
    with request.app.state.database.session_factory() as session:
        activities = session.scalars(
            select(ActivityRecord).order_by(ActivityRecord.created_at.desc())
        ).all()
        return {"items": [activity_dict(item) for item in activities]}


@router.post("/{operation_id}/undo")
def undo_activity(operation_id: str, request: Request) -> dict[str, object]:
    result = MemoryOperations(request.app.state.database).undo(operation_id)
    return {"undone": result.undone, "operation_id": result.operation_id}
