"""
Wikidata enrichment of already-canonical players.

Runs AFTER core matching (EA <-> transfermarkt) has created `players`.
Matching logic is NOT reimplemented here: it calls the existing
matching.identity.match_one() with the existing DEFAULT_THRESHOLDS. No
wikidata-specific thresholds or scoring exist.

Only MATCHED / PROBABLE_MATCH results are ever acted on. AMBIGUOUS,
UNMATCHED, zero-candidate, DOB-conflict and multi-candidate cases store
NOTHING: no link, no identity field, no image.

Non-destructive rules:
  * players.wikidata_id is never modified (see migration 0006 for why an
    UPDATE is impossible in DuckDB anyway).
  * a player that already has a DIFFERENT QID (in players.wikidata_id or in
    player_wikidata_links) is skipped, never overwritten.
  * a QID already owned by ANOTHER player is skipped, never shared.
  * wikidata-sourced identity fields are appended as their own
    source-tagged rows; EA / transfermarkt rows are never touched.
  * re-runs are idempotent: same player + same dataset_version does not
    re-append fields; the same image URL is not inserted twice.
"""
from __future__ import annotations

from datetime import datetime, timezone

import duckdb

from ingestion.loader import load_identity_fields_for_player, load_player_image
from ingestion.normalizer import normalize
from ingestion.providers.base import RawRecord
from matching.identity import (
    DEFAULT_THRESHOLDS,
    build_identity_input_for_player,
    from_wikidata_record,
    match_one,
)
from matching.schema import MatchThresholds


def _link_state(con: duckdb.DuckDBPyConnection, player_uid: str, qid: str) -> str:
    """One of: 'free', 'already_linked', 'conflict_player_has_other_qid',
    'conflict_qid_taken'."""
    p_qid = con.execute("select wikidata_id from players where player_uid = ?", [player_uid]).fetchone()[0]
    link = con.execute("select wikidata_id from player_wikidata_links where player_uid = ?", [player_uid]).fetchone()
    l_qid = link[0] if link else None

    for existing in (p_qid, l_qid):
        if existing is not None and existing != qid:
            return "conflict_player_has_other_qid"

    taken = con.execute(
        "select 1 from players where wikidata_id = ? and player_uid != ? "
        "union all select 1 from player_wikidata_links where wikidata_id = ? and player_uid != ?",
        [qid, player_uid, qid, player_uid],
    ).fetchone()
    if taken is not None:
        return "conflict_qid_taken"

    return "already_linked" if l_qid == qid else "free"


def enrich_players_with_wikidata(
    con: duckdb.DuckDBPyConnection,
    records: list[RawRecord],
    thresholds: MatchThresholds = DEFAULT_THRESHOLDS,
) -> dict[str, int]:
    """`records` must already be validated wikidata RawRecords."""
    counts = {"linked": 0, "already_linked": 0, "conflict_skipped": 0, "no_confident_match": 0}

    identities = [from_wikidata_record(r) for r in records]
    records_by_qid = {r.source_record_id: r for r in records}

    for (player_uid,) in con.execute("select player_uid from players").fetchall():
        player_identity = build_identity_input_for_player(con, player_uid)
        result = match_one(player_identity, identities, thresholds)
        best = result.best

        if best is None or best.status not in ("MATCHED", "PROBABLE_MATCH") or not best.wikidata_id:
            counts["no_confident_match"] += 1
            continue

        qid = best.wikidata_id
        state = _link_state(con, player_uid, qid)
        if state.startswith("conflict"):
            counts["conflict_skipped"] += 1
            continue

        record = records_by_qid[qid]
        if state == "free":
            con.execute(
                "insert into player_wikidata_links "
                "(player_uid, wikidata_id, match_status, match_confidence, matched_on, dataset_version, linked_at) "
                "values (?, ?, ?, ?, ?, ?, ?)",
                [player_uid, qid, best.status, best.confidence, ",".join(best.matched_on),
                 record.dataset_version, datetime.now(timezone.utc)],
            )
            counts["linked"] += 1
        else:
            counts["already_linked"] += 1

        out = normalize(record)

        already_loaded = con.execute(
            "select 1 from player_field_values where player_uid = ? and source = 'wikidata' and dataset_version = ? limit 1",
            [player_uid, record.dataset_version],
        ).fetchone()
        if already_loaded is None:
            load_identity_fields_for_player(con, player_uid, out.identity_fields)
        if out.image is not None:
            load_player_image(con, player_uid, out.image)

    return counts
