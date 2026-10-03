"""Startup verification (AD-4): open the DB read-only, check schema objects, load RM1/RM2 and check their invariants.
FAIL CLOSED: any problem yields status 'unavailable' (the app still starts, data endpoints answer 503)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from api.domain.enums import EntityKind, MatchStatus
from api.domain.link_rules import LINKED
from api.domain.player_index import PlayerIndex
from api.domain.search import SearchIndex
from api.repositories import catalog_repository, player_index_repository
from api.repositories.database import Database
from api.repositories.exceptions import DatabaseUnavailable
from api.services.runtime import RuntimeState

log = logging.getLogger("api.integrity")


def build_runtime(db: Database) -> RuntimeState:
    try:
        db.open()
    except DatabaseUnavailable as exc:
        log.error("startup: database cannot be opened: %s", exc)
        return RuntimeState("unavailable", False, (f"database cannot be opened: {exc}",))
    try:
        with db.cursor() as cur:
            missing = catalog_repository.missing_objects(cur)
            if missing:
                return _fail(f"missing schema objects: {', '.join(missing)}")
            rows = player_index_repository.load_player_index_rows(cur)
            index = PlayerIndex(rows)
            links = player_index_repository.load_link_states(cur, [r.ea_fc26_id for r in index.rows])
            aliases = player_index_repository.load_aliases(cur)
            problems = _check_invariants(index, links, catalog_repository.ea_attribute_count(cur),
                                         catalog_repository.ml_columns_in_use(cur))
            problems += [f"alias for unknown EA player {a.ea_fc26_id}" for a in aliases if index.get(a.ea_fc26_id) is None][:5]
    except DatabaseUnavailable as exc:
        return _fail(f"read failed: {exc}")
    except ValueError as exc:                       # e.g. an unknown match status in the data
        return _fail(f"unrecognised stored value: {exc}")
    if problems:
        for p in problems:
            log.error("startup invariant violated: %s", p)
        return RuntimeState("unavailable", True, tuple(problems))
    search_index = SearchIndex(index.rows, aliases)
    by_player: dict[int, dict] = {}
    for link in links:
        by_player.setdefault(link.ea_fc26_id, {})[link.source] = link
    log.info("startup ok: %d EA players (%d canonical, %d EA-only), %d aliases", len(index),
             index.count(EntityKind.CANONICAL), index.count(EntityKind.EA_ONLY), len(aliases))
    return RuntimeState("ok", True, (), index, tuple(links), by_player, search_index, datetime.now(timezone.utc))


def _fail(problem: str) -> RuntimeState:
    log.error("startup: %s", problem)
    return RuntimeState("unavailable", True, (problem,))


def _check_invariants(index: PlayerIndex, links, attribute_count: int, ml_in_use: int) -> list[str]:
    problems: list[str] = []
    if len(index) == 0:
        problems.append("no EA players in the database")
    if len(index) != attribute_count:
        problems.append(f"RM1 has {len(index)} rows but ea_fc26_attributes has {attribute_count}")
    if dups := index.duplicate_ids():
        problems.append(f"RM1 duplicate ea_fc26_id values: {sorted(dups)[:5]}")
    if nameless := [r.ea_fc26_id for r in index.rows if not r.display_name]:
        problems.append(f"{len(nameless)} EA players have no display_name (first: {nameless[:5]})")
    if unrated := [r.ea_fc26_id for r in index.rows if r.overall_rating is None or r.potential is None]:
        problems.append(f"{len(unrated)} EA players have no overall_rating/potential (first: {unrated[:5]})")
    if ml_in_use:
        problems.append(f"{ml_in_use} rows have predicted_value_eur/model_version although no model is integrated")
    per_player: dict[int, int] = {}
    for link in links:
        per_player[link.ea_fc26_id] = per_player.get(link.ea_fc26_id, 0) + 1
        row = index.get(link.ea_fc26_id)
        if link.status in LINKED and row is not None and row.entity_kind is not EntityKind.CANONICAL:
            problems.append(f"EA {link.ea_fc26_id}: {link.source} link is {link.status.value} but the player is EA_ONLY")
        if link.status not in LINKED and (link.confidence is not None or link.matched_on is not None):
            problems.append(f"EA {link.ea_fc26_id}: {link.source} exposes confidence for status {link.status.value}")
    if any(n != 2 for n in per_player.values()) or len(per_player) != len(index):
        problems.append("RM2 must hold exactly two link states per EA player")
    return problems
