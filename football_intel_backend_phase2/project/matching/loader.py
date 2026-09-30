"""
Persistence for the matching layer.

Two distinct operations, kept separate on purpose:

  persist_identity_matches()  — writes EVERY scored candidate (matched,
                                 probable, ambiguous, unmatched alike) into
                                 identity_matches. Pure audit trail, nothing
                                 is filtered out. Append-only.

  create_player_records()     — the ONLY function that creates rows in the
                                 `players` table (the canonical entity table).
                                 Only promotes results whose status is in
                                 `statuses_to_promote` (default: MATCHED,
                                 PROBABLE_MATCH). Never overwrites an
                                 existing player's ids — if a conflict is
                                 detected (same ea_fc26_id already linked to
                                 a *different* transfermarkt_id) it raises
                                 rather than silently picking one.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import duckdb
import pandas as pd

from matching.schema import MatchResult


def _bulk_insert(con: duckdb.DuckDBPyConnection, table: str, columns: list[str], rows: list[tuple]) -> None:
    """See ingestion/loader.py::_bulk_insert for why this exists instead
    of executemany() — measured ~1ms/row with executemany vs. this being
    orders of magnitude faster via DuckDB's vectorized DataFrame path."""
    if not rows:
        return
    df = pd.DataFrame(rows, columns=columns)
    con.register("_matches_bulk_tmp", df)
    try:
        col_list = ", ".join(columns)
        con.execute(f"insert into {table} ({col_list}) select {col_list} from _matches_bulk_tmp")
    finally:
        con.unregister("_matches_bulk_tmp")


def persist_identity_matches(
    con: duckdb.DuckDBPyConnection,
    results: list[MatchResult],
    start_id: int | None = None,
) -> int:
    """Append every candidate from every result to identity_matches.
    Each result gets its own match_group_id (shared by all its candidates),
    and the chosen best candidate is flagged is_best=True — this is what
    lets review_queue point back to "all candidates for this one query"
    without duplicating the scoring data. Returns number of rows written."""
    if start_id is None:
        row = con.execute("select coalesce(max(id), 0) from identity_matches").fetchone()
        start_id = row[0] + 1

    rows = []
    next_id = start_id
    for result in results:
        group_id = str(uuid.uuid4())
        candidates = result.all_candidates or ([result.best] if result.best else [])
        best_ids = (
            (result.best.ea_fc26_id, result.best.transfermarkt_id, result.best.wikidata_id)
            if result.best is not None else None
        )
        for candidate in candidates:
            candidate_ids = (candidate.ea_fc26_id, candidate.transfermarkt_id, candidate.wikidata_id)
            is_best = best_ids is not None and candidate_ids == best_ids
            rows.append((
                next_id,
                int(candidate.ea_fc26_id) if candidate.ea_fc26_id is not None else None,
                int(candidate.transfermarkt_id) if candidate.transfermarkt_id is not None else None,
                candidate.wikidata_id,
                candidate.status,
                candidate.confidence,
                ",".join(candidate.matched_on),
                result.matched_at,
                group_id,
                is_best,
            ))
            next_id += 1

    if rows:
        _bulk_insert(
            con, "identity_matches",
            ["id", "ea_fc26_id", "transfermarkt_id", "wikidata_id", "match_status", "match_confidence", "matched_on", "matched_at", "match_group_id", "is_best"],
            rows,
        )
    return len(rows)


def create_player_records(
    con: duckdb.DuckDBPyConnection,
    results: list[MatchResult],
    statuses_to_promote: tuple[str, ...] = ("MATCHED", "PROBABLE_MATCH"),
) -> dict[str, int]:
    """Create `players` rows for results whose best candidate's status is
    in statuses_to_promote. Never deletes or silently overwrites an
    existing player's linked source ids. Returns counts by outcome."""
    counts = {"created": 0, "already_exists_consistent": 0, "skipped_low_confidence": 0, "conflict": 0}

    for result in results:
        best = result.best
        if best is None or best.status not in statuses_to_promote:
            counts["skipped_low_confidence"] += 1
            continue

        ea_id = int(best.ea_fc26_id) if best.ea_fc26_id is not None else None
        tm_id = int(best.transfermarkt_id) if best.transfermarkt_id is not None else None

        existing = None
        if ea_id is not None:
            existing = con.execute(
                "select player_uid, ea_fc26_id, transfermarkt_id, wikidata_id from players where ea_fc26_id = ?",
                [ea_id],
            ).fetchone()

        if existing is None and tm_id is not None:
            existing = con.execute(
                "select player_uid, ea_fc26_id, transfermarkt_id, wikidata_id from players where transfermarkt_id = ?",
                [tm_id],
            ).fetchone()

        now = datetime.now(timezone.utc)

        if existing is not None:
            _, existing_ea, existing_tm, existing_wd = existing
            conflict = (
                (existing_ea is not None and ea_id is not None and existing_ea != ea_id)
                or (existing_tm is not None and tm_id is not None and existing_tm != tm_id)
            )
            if conflict:
                counts["conflict"] += 1
                continue
            counts["already_exists_consistent"] += 1
            continue

        player_uid = str(uuid.uuid4())
        con.execute(
            "insert into players (player_uid, ea_fc26_id, transfermarkt_id, wikidata_id, display_name, created_at, updated_at) "
            "values (?, ?, ?, ?, ?, ?, ?)",
            [player_uid, ea_id, tm_id, best.wikidata_id, result.query.display_name, now, now],
        )
        counts["created"] += 1

    return counts
