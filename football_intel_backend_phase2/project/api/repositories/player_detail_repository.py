"""Batch enrichment for the canonical players of one page: ONE query per table for the whole page (no N+1)."""
from __future__ import annotations

from datetime import timezone
from typing import Mapping, Sequence

from api.domain.models import ImageRow, InjuryStateRow, MarketValueRow
from api.domain.source_registry import IMAGE_SOURCES
from api.domain.models import SourceFreshnessRow
from api.readmodels.definitions import (
    BATCH_INJURY_STATUS, BATCH_LATEST_MARKET_VALUE, BATCH_PRIMARY_IMAGE, WIKIDATA_LINK_PROVENANCE,
)
from api.repositories.exceptions import translate_db_errors


def _utc(ts):
    # ASSUMPTION (documented): the pipeline stores UTC instants in naive TIMESTAMP columns.
    return ts.replace(tzinfo=timezone.utc) if ts is not None and ts.tzinfo is None else ts


@translate_db_errors
def latest_market_values(cur, player_uids: Sequence[str]) -> Mapping[str, MarketValueRow]:
    if not player_uids:
        return {}
    return {r[0]: MarketValueRow(r[0], int(r[1]), r[2], r[3], r[4], _utc(r[5]))
            for r in cur.execute(BATCH_LATEST_MARKET_VALUE, [list(player_uids)]).fetchall()}


@translate_db_errors
def injury_states(cur, player_uids: Sequence[str]) -> Mapping[str, InjuryStateRow]:
    if not player_uids:
        return {}
    return {r[0]: InjuryStateRow(r[0], r[1], _utc(r[2]))
            for r in cur.execute(BATCH_INJURY_STATUS, [list(player_uids)]).fetchall()}


@translate_db_errors
def primary_images(cur, player_uids: Sequence[str]) -> Mapping[str, ImageRow]:
    if not player_uids:
        return {}
    return {r[0]: ImageRow(r[0], r[1], r[2], r[3], r[4])
            for r in cur.execute(BATCH_PRIMARY_IMAGE, [list(player_uids), list(IMAGE_SOURCES)]).fetchall()}


@translate_db_errors
def wikidata_link_provenance(cur, player_uid: str) -> SourceFreshnessRow | None:
    """(dataset_version, linked_at) of this player's Wikidata link; record_count is not applicable (1)."""
    row = cur.execute(WIKIDATA_LINK_PROVENANCE, [player_uid]).fetchone()
    return SourceFreshnessRow("wikidata", row[0], _utc(row[1]), 1) if row else None
