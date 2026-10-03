from __future__ import annotations

from datetime import datetime, timezone

from api.domain.enums import MatchStatus
from api.domain.link_rules import derive_link_state
from api.domain.models import AliasRow, LinkStateRow, PlayerIndexRow
from api.readmodels.definitions import (
    RM1_PLAYER_INDEX, RM2_REVIEW_PENDING, RM2_TRANSFERMARKT_BEST, RM2_WIKIDATA_LINKS, RM3_ALIASES,
)
from api.repositories.exceptions import translate_db_errors


def _utc(ts: datetime | None) -> datetime | None:
    # ASSUMPTION: the pipeline stores UTC instants in TIMESTAMP columns (naive); we re-attach UTC.
    return ts.replace(tzinfo=timezone.utc) if ts is not None and ts.tzinfo is None else ts


@translate_db_errors
def load_player_index_rows(cur) -> list[PlayerIndexRow]:
    return [
        PlayerIndexRow(
            ea_fc26_id=int(r[0]), display_name=r[1], position=r[2], club=r[3], nationality=r[4],
            date_of_birth=r[5], preferred_foot=r[6], overall_rating=r[7], potential=r[8], value_eur_ingame=r[9],
            dataset_version=r[10], fetched_at=_utc(r[11]), canonical_player_uid=r[12],
            transfermarkt_id=int(r[13]) if r[13] is not None else None, wikidata_id=r[14],
        )
        for r in cur.execute(RM1_PLAYER_INDEX).fetchall()
    ]


@translate_db_errors
def load_link_states(cur, ea_ids: list[int]) -> list[LinkStateRow]:
    """RM2: exactly two rows per EA id (transfermarkt, wikidata). No stored result -> NOT_EVALUATED."""
    tm = {int(r[0]): r[1:] for r in cur.execute(RM2_TRANSFERMARKT_BEST).fetchall()}
    pending = {int(r[0]) for r in cur.execute(RM2_REVIEW_PENDING).fetchall()}
    wd = {int(r[0]): r[1:] for r in cur.execute(RM2_WIKIDATA_LINKS).fetchall()}
    out: list[LinkStateRow] = []
    for ea_id in ea_ids:
        st, conf, on = derive_link_state(*tm.get(ea_id, (None, None, None)))
        out.append(LinkStateRow(ea_id, "transfermarkt", st, conf, on, ea_id in pending))
        st, conf, on = derive_link_state(*wd.get(ea_id, (None, None, None)))
        out.append(LinkStateRow(ea_id, "wikidata", st, conf, on, False))
    return out


@translate_db_errors
def load_aliases(cur) -> list[AliasRow]:
    """RM3: other-source display names of canonical players (for search only)."""
    return [AliasRow(int(r[0]), r[1], r[2]) for r in cur.execute(RM3_ALIASES).fetchall()]
