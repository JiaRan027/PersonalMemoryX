from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from personalmemory.api.dependencies import require_session_token
from personalmemory.application.envelope import (
    ContinuityEnvelopeService,
    EnvelopeConflictError,
    EnvelopeNotFoundError,
    EnvelopeScopeError,
    EnvelopeValidationError,
)
from personalmemory.domain.continuity import ContinuityEnvelope

router = APIRouter(
    prefix="/envelopes",
    tags=["continuity-envelopes"],
    dependencies=[Depends(require_session_token)],
)


class EnvelopeSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector_id: str = Field(min_length=1, max_length=180)
    envelope: ContinuityEnvelope

    @model_validator(mode="after")
    def require_api_identity(self) -> EnvelopeSubmitRequest:
        if self.envelope.namespace is None:
            raise ValueError("generic envelope requires namespace")
        if self.envelope.idempotency_key is None:
            raise ValueError("generic envelope requires idempotency_key")
        return self


@router.post("")
def ingest_envelope(
    payload: EnvelopeSubmitRequest, request: Request, response: Response
) -> dict[str, object]:
    try:
        result = ContinuityEnvelopeService(
            request.app.state.database, settings=request.app.state.settings
        ).ingest(
            payload.connector_id,
            payload.envelope,
            require_api_capability=True,
            reject_secret=True,
            authority_verified=False,
        )
    except EnvelopeNotFoundError as error:
        raise HTTPException(404, detail={"code": error.code}) from error
    except EnvelopeScopeError as error:
        raise HTTPException(403, detail={"code": error.code}) from error
    except EnvelopeConflictError as error:
        raise HTTPException(409, detail={"code": error.code}) from error
    except EnvelopeValidationError as error:
        raise HTTPException(422, detail={"code": error.code}) from error

    response.status_code = status.HTTP_200_OK if result.duplicate else status.HTTP_201_CREATED
    return {
        "envelope_id": result.envelope_id,
        "source_revision_id": result.source_revision_id,
        "operation_id": result.operation_id,
        "duplicate": result.duplicate,
        "counts": result.counts,
    }
