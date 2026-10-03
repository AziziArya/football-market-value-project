from __future__ import annotations

from fastapi import APIRouter, Path, Request

from api.schemas.common import Problem
from api.schemas.player import PLAYER_ID_PATTERN, PlayerProfile

router = APIRouter()


@router.get("/players/{player_id}", operation_id="getPlayer", response_model=PlayerProfile,
            summary="Player intelligence profile",
            responses={400: {"model": Problem}, 404: {"model": Problem}, 503: {"model": Problem}, 500: {"model": Problem}})
def get_player(request: Request, player_id: str = Path(
        ..., description="Public id `ea:<ea_fc26_id>` only. `p:<uuid>` is not accepted.",
        json_schema_extra={"pattern": PLAYER_ID_PATTERN})) -> PlayerProfile:
    # The pattern is documented in the schema but validated by the service, so a malformed id is
    # INVALID_PLAYER_ID (contract) and not the generic INVALID_PARAMETER.
    return request.app.state.services.player.get(player_id)
