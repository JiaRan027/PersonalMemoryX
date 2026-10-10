from fastapi import APIRouter, Depends, Request

from personalmemory import __build_revision__, __version__
from personalmemory.api.dependencies import require_session_token

router = APIRouter(tags=["health"], dependencies=[Depends(require_session_token)])


@router.get("/health")
def get_health(request: Request) -> dict[str, str]:
    database = request.app.state.database
    status = "ready" if database.is_ready() else "protected"
    return {
        "status": status,
        "database": status,
        "version": __version__,
        "build_revision": __build_revision__,
    }
