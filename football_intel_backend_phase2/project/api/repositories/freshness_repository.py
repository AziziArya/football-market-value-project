from __future__ import annotations

from datetime import timezone

from api.domain.models import SourceFreshnessRow
from api.readmodels.definitions import RM7_FIELD_COUNT, RM7_FIELD_LATEST, RM7_MV_COUNT, RM7_MV_LATEST
from api.repositories.exceptions import translate_db_errors


def _row(source, latest, count) -> SourceFreshnessRow | None:
    if latest is None or not count:
        return None                                  # nothing loaded from this source
    ts = latest[1]
    if ts is not None and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)         # ASSUMPTION: stored as UTC (see player_index_repository)
    return SourceFreshnessRow(source=source, dataset_version=latest[0], last_fetched_at=ts, record_count=int(count))


@translate_db_errors
def field_source_freshness(cur, source: str) -> SourceFreshnessRow | None:
    return _row(source, cur.execute(RM7_FIELD_LATEST, [source]).fetchone(), cur.execute(RM7_FIELD_COUNT, [source]).fetchone()[0])


@translate_db_errors
def market_value_freshness(cur, source: str) -> SourceFreshnessRow | None:
    return _row(source, cur.execute(RM7_MV_LATEST, [source]).fetchone(), cur.execute(RM7_MV_COUNT, [source]).fetchone()[0])
