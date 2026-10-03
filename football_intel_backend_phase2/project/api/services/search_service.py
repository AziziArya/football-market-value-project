from __future__ import annotations

from datetime import datetime, timezone

from api import CONTRACT_VERSION
from api.domain.enums import EntityKind, SearchSort
from api.domain.normalize import normalize
from api.domain.search import SearchQuery
from api.errors import data_unavailable, invalid_parameters
from api.repositories.database import Database
from api.repositories.exceptions import DatabaseUnavailable
from api.schemas.common import ResponseMeta
from api.schemas.search import SearchItem, SearchMatch, SearchResponse
from api.services import player_summary
from api.services.runtime import RuntimeHolder

Q_MIN, Q_MAX = 2, 80


def _filter(value: str | None) -> str | None:
    """Blank filter values are treated as absent; others compare casefolded (contract: exact, case-insensitive)."""
    value = (value or "").strip()
    return value.casefold() if value else None


class SearchService:
    def __init__(self, runtime: RuntimeHolder, db: Database):
        self._runtime, self._db = runtime, db

    def search(self, *, q: str, entity_kind: EntityKind | None = None, position: str | None = None,
               nationality: str | None = None, club: str | None = None, min_overall: int | None = None,
               sort: SearchSort = SearchSort.relevance, limit: int = 20, offset: int = 0) -> SearchResponse:
        state = self._runtime.state
        if not state.ok:
            raise data_unavailable()

        q_clean = q.strip()
        q_norm = normalize(q_clean)
        errors = []
        if not Q_MIN <= len(q_clean) <= Q_MAX:
            errors.append({"parameter": "q", "reason": f"length must be between {Q_MIN} and {Q_MAX} characters after trimming"})
        elif not q_norm:
            errors.append({"parameter": "q", "reason": "must contain at least one letter or digit"})
        if errors:
            raise invalid_parameters(errors)

        result = state.search_index.search(SearchQuery(
            q_norm=q_norm, entity_kind=entity_kind, position=_filter(position), nationality=_filter(nationality),
            club=_filter(club), min_overall=min_overall, sort=sort, limit=limit, offset=offset))

        rows = [state.index.get(h.ea_fc26_id) for h in result.hits]
        uids = [r.canonical_player_uid for r in rows if r.canonical_player_uid]
        details = player_summary.EMPTY_DETAILS          # a page of EA-only players needs no database access at all
        if uids:
            try:
                with self._db.cursor() as cur:           # canonical enrichment: at most 3 queries for the whole page
                    details = player_summary.load_details(cur, uids)
            except DatabaseUnavailable:
                raise data_unavailable()

        items = []
        for hit, row in zip(result.hits, rows):
            summary = player_summary.build_summary(row, state.links_by_player[row.ea_fc26_id], details)
            items.append(SearchItem(**summary.model_dump(), match=SearchMatch(
                quality=hit.quality, matched_alias=hit.matched_alias, matched_alias_source=hit.matched_alias_source)))
        end = offset + limit
        return SearchResponse(
            items=items, total=result.total, limit=limit, offset=offset,
            next_offset=end if end < result.total else None, query_normalized=q_norm,
            meta=ResponseMeta(contract_version=CONTRACT_VERSION, generated_at=datetime.now(timezone.utc).replace(microsecond=0),
                              contains_sample_data=any(player_summary.contains_sample_data(i) for i in items)))
