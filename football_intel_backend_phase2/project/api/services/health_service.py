from __future__ import annotations

from api import CONTRACT_VERSION
from api.errors import data_unavailable
from api.repositories.database import Database
from api.schemas.health import HealthResponse
from api.services.runtime import RuntimeHolder


class HealthService:
    def __init__(self, runtime: RuntimeHolder, db: Database):
        self._runtime, self._db = runtime, db

    def check(self) -> HealthResponse:
        """ok only if startup verification passed AND the database still answers a trivial read now.
        Otherwise raises DATA_UNAVAILABLE (503 Problem): the contract has no 'unavailable' health body (G11)."""
        if not (self._runtime.state.ok and self._db.ping()):
            raise data_unavailable()
        return HealthResponse(status="ok", database_reachable=True, contract_version=CONTRACT_VERSION)
