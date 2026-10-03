from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from api.domain.enums import Availability, DataOrigin, FreshnessRole
from api.schemas.common import ResponseMeta


class SourceFreshness(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)
    role: FreshnessRole
    source: str | None = None
    availability: Availability
    data_origin: DataOrigin | None = None
    dataset_version: str | None = None
    last_fetched_at: datetime | None = None
    record_count: int | None = None
    is_live: Literal[False] = False          # constant false: no provider retrieves live data today


class DataFreshnessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[SourceFreshness]
    meta: ResponseMeta
