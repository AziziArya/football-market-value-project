from __future__ import annotations

from datetime import datetime, timezone

from api import CONTRACT_VERSION
from api.domain.enums import Availability, DataOrigin, FreshnessRole
from api.domain.source_registry import INJURY_SOURCE_INTEGRATED, MODEL_INTEGRATED, SOURCES, is_live
from api.errors import data_unavailable
from api.repositories import freshness_repository
from api.repositories.database import Database
from api.repositories.exceptions import DatabaseUnavailable
from api.schemas.common import ResponseMeta
from api.schemas.freshness import DataFreshnessResponse, SourceFreshness
from api.services.runtime import RuntimeHolder


class FreshnessService:
    def __init__(self, runtime: RuntimeHolder, db: Database):
        self._runtime, self._db = runtime, db

    def get(self) -> DataFreshnessResponse:
        if not self._runtime.state.ok:
            raise data_unavailable()
        entries: list[SourceFreshness] = []
        try:
            with self._db.cursor() as cur:
                for info in SOURCES:
                    loader = (freshness_repository.market_value_freshness if info.role is FreshnessRole.MARKET_VALUE
                              else freshness_repository.field_source_freshness)
                    row = loader(cur, info.name)
                    entries.append(SourceFreshness(
                        role=info.role, source=info.name,
                        availability=Availability.AVAILABLE if row else Availability.NO_SOURCE_DATA,
                        data_origin=info.data_origin if row else None,
                        dataset_version=row.dataset_version if row else None,
                        last_fetched_at=_seconds(row.last_fetched_at) if row else None,
                        record_count=row.record_count if row else None,
                        is_live=is_live(info.name),
                    ))
        except DatabaseUnavailable:
            raise data_unavailable()
        # Capabilities that do not exist yet are reported honestly, never omitted.
        entries.append(SourceFreshness(
            role=FreshnessRole.INJURY, availability=Availability.AVAILABLE if INJURY_SOURCE_INTEGRATED else Availability.NO_SOURCE_DATA))
        entries.append(SourceFreshness(
            role=FreshnessRole.MODEL, availability=Availability.AVAILABLE if MODEL_INTEGRATED else Availability.NOT_YET_INTEGRATED))
        sample = any(e.availability is Availability.AVAILABLE and e.data_origin is DataOrigin.SAMPLE_FIXTURE for e in entries)
        return DataFreshnessResponse(
            sources=entries,
            meta=ResponseMeta(contract_version=CONTRACT_VERSION, generated_at=datetime.now(timezone.utc).replace(microsecond=0),
                              contains_sample_data=sample))


def _seconds(ts):
    return ts.replace(microsecond=0) if ts is not None else None
