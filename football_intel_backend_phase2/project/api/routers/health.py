from __future__ import annotations

from fastapi import APIRouter, Request

from api.schemas.common import Problem
from api.schemas.health import HealthResponse

router = APIRouter()


@router.get("/health", operation_id="getHealth", response_model=HealthResponse,
            summary="Liveness + database reachability (no internals exposed)",
            responses={503: {"model": Problem}, 500: {"model": Problem}})
def get_health(request: Request) -> HealthResponse:
    return request.app.state.services.health.check()      # raises DATA_UNAVAILABLE (503 Problem) when unhealthy
