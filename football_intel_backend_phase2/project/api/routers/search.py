from __future__ import annotations

from fastapi import APIRouter, Query, Request

from api.errors import invalid_parameters
from api.schemas.common import Problem
from api.schemas.search import EntityKind, SearchResponse, SearchSort

router = APIRouter()
_ALLOWED = {"q", "entity_kind", "position", "nationality", "club", "min_overall", "sort", "limit", "offset"}


@router.get("/players/search", operation_id="searchPlayers", response_model=SearchResponse,
            summary="Search all EA players (+ canonical aliases)",
            responses={400: {"model": Problem}, 503: {"model": Problem}, 500: {"model": Problem}})
def search_players(
    request: Request,
    q: str = Query(..., min_length=2, max_length=80),
    entity_kind: EntityKind | None = Query(None),
    position: str | None = Query(None, max_length=8),
    nationality: str | None = Query(None, max_length=60),
    club: str | None = Query(None, max_length=80),
    min_overall: int | None = Query(None, ge=0, le=99),
    sort: SearchSort = Query(SearchSort.relevance),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0, le=10000),
) -> SearchResponse:
    unknown = sorted(set(request.query_params.keys()) - _ALLOWED)
    if unknown:                                  # a typo must never silently produce an UNFILTERED result
        raise invalid_parameters([{"parameter": u, "reason": "unknown parameter"} for u in unknown])
    return request.app.state.services.search.search(
        q=q, entity_kind=entity_kind, position=position, nationality=nationality, club=club,
        min_overall=min_overall, sort=sort, limit=limit, offset=offset)
