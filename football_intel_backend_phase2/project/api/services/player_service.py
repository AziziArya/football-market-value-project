"""getPlayer: the same PlayerSummary as search + provenance + meta. EA-only players are served entirely from memory."""
from __future__ import annotations

from datetime import datetime, timezone

from api import CONTRACT_VERSION
from api.domain.enums import EntityKind
from api.domain.link_rules import LINKED
from api.domain.models import SourceFreshnessRow
from api.domain.player_id import parse_player_id
from api.domain.source_registry import origin_for
from api.errors import data_unavailable, invalid_player_id, player_not_found
from api.repositories import freshness_repository, player_detail_repository
from api.repositories.database import Database
from api.repositories.exceptions import DatabaseUnavailable
from api.schemas.common import ResponseMeta
from api.schemas.player import PlayerProfile, SourceProvenance
from api.services import player_summary
from api.services.runtime import RuntimeHolder


def _prov(source: str, row: SourceFreshnessRow | None) -> SourceProvenance:
    return SourceProvenance(source=source, dataset_version=row.dataset_version if row else None,
                            fetched_at=player_summary._seconds(row.last_fetched_at) if row else None, data_origin=origin_for(source))


class PlayerService:
    def __init__(self, runtime: RuntimeHolder, db: Database):
        self._runtime, self._db = runtime, db

    def get(self, raw_player_id: str) -> PlayerProfile:
        state = self._runtime.state
        if not state.ok:
            raise data_unavailable()
        ea_id = parse_player_id(raw_player_id)
        if ea_id is None:
            raise invalid_player_id()
        row = state.index.get(ea_id)
        if row is None:
            raise player_not_found(raw_player_id)             # well-formed, unknown: the ONLY 404

        links = state.links_by_player[ea_id]
        details = player_summary.EMPTY_DETAILS
        wikidata_row = tm_row = None
        try:
            if row.entity_kind is EntityKind.CANONICAL:        # EA-only: no database access at all
                with self._db.cursor() as cur:
                    details = player_summary.load_details(cur, [row.canonical_player_uid])
                    summary = player_summary.build_summary(row, links, details)
                    if links["wikidata"].status in LINKED or summary.image is not None:
                        wikidata_row = (player_detail_repository.wikidata_link_provenance(cur, row.canonical_player_uid)
                                        or freshness_repository.field_source_freshness(cur, "wikidata"))
                    if links["transfermarkt"].status in LINKED and summary.values.source_market_value.provenance is None:
                        tm_row = freshness_repository.field_source_freshness(cur, "transfermarkt_dataset")
            else:
                summary = player_summary.build_summary(row, links, details)
        except DatabaseUnavailable:
            raise data_unavailable()

        # One entry per source that contributed ANY block (order = source registry order).
        provenance = [summary.values.ea_ingame_value.provenance]
        mv_prov = summary.values.source_market_value.provenance
        if mv_prov is not None:
            provenance.append(mv_prov)
        elif tm_row is not None or links["transfermarkt"].status in LINKED:
            provenance.append(_prov("transfermarkt_dataset", tm_row))      # the linked id comes from this source
        if wikidata_row is not None or links["wikidata"].status in LINKED or summary.image is not None:
            provenance.append(_prov("wikidata", wikidata_row))

        return PlayerProfile(
            **summary.model_dump(), provenance=provenance,
            meta=ResponseMeta(contract_version=CONTRACT_VERSION, generated_at=datetime.now(timezone.utc).replace(microsecond=0),
                              contains_sample_data=player_summary.contains_sample_data(summary)))
