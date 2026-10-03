from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"]            # unhealthy => 503 Problem, never a HealthResponse (G11)
    database_reachable: bool
    contract_version: str
