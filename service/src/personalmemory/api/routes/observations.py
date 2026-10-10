from __future__ import annotations

from contextlib import suppress
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select

from personalmemory.api.dependencies import require_session_token
from personalmemory.application.context import ContextCompiler, PreflightContextError
from personalmemory.application.hook_receipts import record_hook_receipt
from personalmemory.application.observation import (
    ObservationConflictError,
    ObservationNotFoundError,
    ObservationScopeError,
    ObservationService,
)
from personalmemory.domain.connector.profile import get_builtin_profile
from personalmemory.domain.extraction.deterministic import DeterministicExtractorAdapter
from personalmemory.domain.memory.types import MemoryType
from personalmemory.domain.observation import InteractionFragment
from personalmemory.infrastructure.database.models import (
    AgentTurnObservationRecord,
    ConnectorInstanceRecord,
)

router = APIRouter(
    prefix="/observations",
    tags=["observations"],
    dependencies=[Depends(require_session_token)],
)


_EMPTY_COUNTS = {
    "accepted": 0,
    "provisional": 0,
    "quarantined": 0,
    "restricted": 0,
    "discarded": 0,
}

# Global Claude delivery is deliberately selective.  A user-facing identity,
# preference, decision, or goal has durable value; transient events, inferred
# insights, and skills are too easy to misclassify in an unrelated workspace.
_GLOBAL_CAPTURE_TYPES = frozenset(
    {
        MemoryType.FACT,
        MemoryType.GOAL,
        MemoryType.DECISION,
        MemoryType.PREFERENCE,
    }
)


def _is_context_only_profile(payload: InteractionFragment, request: Request) -> bool:
    """Identify a hook connector that is authorized for delivery only."""
    with request.app.state.database.session_factory() as session:
        connector = session.get(ConnectorInstanceRecord, payload.connector_id)
        if connector is None:
            return False
        try:
            profile = get_builtin_profile(connector.profile_id)
        except ValueError:
            return False
    return (
        profile.agent_family == payload.agent_family
        and profile.capabilities.observe_via_hook
        and not profile.capabilities.capture_agent_turns
    )


def _compile_context_only_preflight(
    payload: InteractionFragment, request: Request
) -> dict[str, object]:
    """Compile context and retain the connector authorization boundary."""
    try:
        return ContextCompiler(
            request.app.state.database,
            data_dir=request.app.state.settings.data_dir,
        ).compile_preflight(
            payload.user_text or "",
            connector_id=payload.connector_id,
            cwd=payload.cwd,
        )
    except PreflightContextError as error:
        error_status = {
            "CONNECTOR_NOT_FOUND": status.HTTP_404_NOT_FOUND,
            "CONNECTOR_DISABLED": status.HTTP_409_CONFLICT,
        }.get(error.code, status.HTTP_403_FORBIDDEN)
        raise HTTPException(error_status, detail={"code": error.code}) from error


def _project_hook_covers_cwd(payload: InteractionFragment, request: Request) -> bool:
    """Avoid duplicate global delivery when a project hook owns this cwd."""
    if payload.agent_family != "claude_code":
        return False
    with request.app.state.database.session_factory() as session:
        connectors = session.scalars(
            select(ConnectorInstanceRecord).where(
                ConnectorInstanceRecord.id != payload.connector_id,
                ConnectorInstanceRecord.profile_id == "claude_code_hook",
                ConnectorInstanceRecord.enabled.is_(True),
                ConnectorInstanceRecord.can_read.is_(True),
                ConnectorInstanceRecord.read_enabled.is_(True),
            )
        ).all()
    cwd = Path(payload.cwd).resolve(strict=False)
    for connector in connectors:
        if not connector.source_path:
            continue
        root = Path(connector.source_path).resolve(strict=False)
        if cwd == root or root in cwd.parents:
            return True
    return False


def _context_only_response(
    payload: InteractionFragment,
    request: Request,
) -> dict[str, object]:
    preflight = _compile_context_only_preflight(payload, request)
    is_prompt = payload.fragment_kind == "user_message"
    # Check coverage for both fragments.  A project connector can be enabled
    # between UserPromptSubmit and Stop; the global connector must still yield
    # rather than completing a stale pending turn.
    project_hook_covers_cwd = _project_hook_covers_cwd(payload, request)
    delivery_status = "context_only"
    if not is_prompt or project_hook_covers_cwd:
        delivery_status = "ignored"
    body: dict[str, object] = {
        "observation_id": None,
        "status": delivery_status,
        "duplicate": False,
        "source_revision_id": None,
        "counts": dict(_EMPTY_COUNTS),
        "capture_agent_turns": False,
        "mode": "context_only",
    }
    if is_prompt and not project_hook_covers_cwd:
        body["preflight"] = preflight
    else:
        body["ignored"] = True
        body["reason"] = (
            "project_connector_context_delivery"
            if project_hook_covers_cwd
            else "capture_agent_turns_disabled"
        )
    return body


def _global_prompt_is_capture_candidate(payload: InteractionFragment) -> bool:
    """Return whether a global prompt contains an explicit durable statement.

    This is a non-mutating preview.  The actual extraction and admission still
    happen only after the matching assistant Stop through ObservationService.
    """
    if payload.fragment_kind != "user_message" or not payload.user_text:
        return False
    extracted = DeterministicExtractorAdapter().extract(
        payload.user_text,
        source_revision_id="global-preview",
        namespace="general",
    )
    return any(item.type in _GLOBAL_CAPTURE_TYPES for item in extracted.items)


def _has_pending_global_observation(payload: InteractionFragment, request: Request) -> bool:
    """Only route Stop to ingestion when a selective prompt opened a turn."""
    with request.app.state.database.session_factory() as session:
        query = select(AgentTurnObservationRecord).where(
            AgentTurnObservationRecord.connector_id == payload.connector_id,
            AgentTurnObservationRecord.agent_family == payload.agent_family,
            AgentTurnObservationRecord.session_id == payload.session_id,
            AgentTurnObservationRecord.status == "pending",
        )
        if payload.prompt_id is not None:
            query = query.where(AgentTurnObservationRecord.prompt_id == payload.prompt_id)
        pending = session.scalars(query.limit(2)).all()
    # A missing prompt id is acceptable only when there is one unambiguous
    # pending turn; ObservationService applies the same ambiguity protection.
    return len(pending) == 1


def _global_selective_response(
    payload: InteractionFragment,
    request: Request,
    response: Response,
) -> dict[str, object]:
    """Deliver preflight and selectively capture explicit global declarations."""
    is_prompt = payload.fragment_kind == "user_message"
    project_hook_covers_cwd = _project_hook_covers_cwd(payload, request)
    if project_hook_covers_cwd:
        return _context_only_response(payload, request)

    if is_prompt and not _global_prompt_is_capture_candidate(payload):
        return _context_only_response(payload, request)
    if not is_prompt and not _has_pending_global_observation(payload, request):
        return _context_only_response(payload, request)

    try:
        result = ObservationService(
            request.app.state.database, settings=request.app.state.settings
        ).observe_agent_turn(payload)
    except ObservationNotFoundError as error:
        raise HTTPException(404, detail={"code": error.code}) from error
    except ObservationScopeError as error:
        raise HTTPException(403, detail={"code": error.code}) from error
    except ObservationConflictError as error:
        raise HTTPException(409, detail={"code": error.code}) from error

    if result.status == "pending":
        response.status_code = status.HTTP_202_ACCEPTED
    elif result.duplicate:
        response.status_code = status.HTTP_200_OK
    else:
        response.status_code = status.HTTP_201_CREATED
    body: dict[str, object] = {
        "observation_id": result.observation_id,
        "status": result.status,
        "duplicate": result.duplicate,
        "source_revision_id": result.source_revision_id,
        "counts": result.counts,
        # False means full-turn capture is disabled; this path is a narrow
        # opt-in for explicit durable declarations only.
        "capture_agent_turns": False,
        "selective_capture": True,
        "mode": "selective_capture",
    }
    if is_prompt and payload.user_text:
        with suppress(PreflightContextError):
            body["preflight"] = ContextCompiler(
                request.app.state.database,
                data_dir=request.app.state.settings.data_dir,
            ).compile_preflight(
                payload.user_text,
                connector_id=payload.connector_id,
                cwd=payload.cwd,
            )
    return body


@router.post("/agent-turns")
def observe_agent_turn(
    payload: InteractionFragment, request: Request, response: Response
) -> dict[str, object]:
    body = _observe_agent_turn(payload, request, response)
    with suppress(OSError):
        record_hook_receipt(request.app.state.settings.data_dir,
                            payload.connector_id, payload.fragment_kind)
    return body


def _observe_agent_turn(
    payload: InteractionFragment, request: Request, response: Response
) -> dict[str, object]:
    if _is_context_only_profile(payload, request):
        selective_families = {
            get_builtin_profile("claude_code_global_context").agent_family,
            get_builtin_profile("codex_global_context").agent_family,
        }
        if payload.agent_family in selective_families:
            return _global_selective_response(payload, request, response)
        return _context_only_response(payload, request)

    try:
        result = ObservationService(
            request.app.state.database, settings=request.app.state.settings
        ).observe_agent_turn(payload)
    except ObservationNotFoundError as error:
        raise HTTPException(404, detail={"code": error.code}) from error
    except ObservationScopeError as error:
        raise HTTPException(403, detail={"code": error.code}) from error
    except ObservationConflictError as error:
        raise HTTPException(409, detail={"code": error.code}) from error

    if result.status == "pending":
        response.status_code = status.HTTP_202_ACCEPTED
    elif result.duplicate:
        response.status_code = status.HTTP_200_OK
    else:
        response.status_code = status.HTTP_201_CREATED
    body: dict[str, object] = {
        "observation_id": result.observation_id,
        "status": result.status,
        "duplicate": result.duplicate,
        "source_revision_id": result.source_revision_id,
        "counts": result.counts,
    }
    if payload.fragment_kind == "user_message" and payload.user_text:
        with suppress(PreflightContextError):
            body["preflight"] = ContextCompiler(
                request.app.state.database,
                data_dir=request.app.state.settings.data_dir,
            ).compile_preflight(
                payload.user_text,
                connector_id=payload.connector_id,
                cwd=payload.cwd,
            )
    return body
