from __future__ import annotations

from fastapi import APIRouter, Request

from api.schemas.common import Problem
from api.schemas.freshness import DataFreshnessResponse

router = APIRouter()


@router.get("/data-freshness", operation_id="getDataFreshness", response_model=DataFreshnessResponse,
            summary="Per-source origin, dataset version and ingestion time",
            responses={503: {"model": Problem}, 500: {"model": Problem}})
def get_data_freshness(request: Request) -> DataFreshnessResponse:
    return request.app.state.services.freshness.get()
