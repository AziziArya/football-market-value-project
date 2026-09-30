"""
Persistent (DuckDB-backed) review queue for AMBIGUOUS identity matches.

Counterpart to matching/review_queue.py's in-memory ReviewQueue — that
class is still useful for interactive/in-process review; this module is
for review state that must survive across pipeline runs.

Three deliberately SEPARATE actions, never combined into one call:
  1. persist_review_queue()      — pipeline calls this after matching.
                                     Only ever creates PENDING rows.
  2. resolve()                   — a human calls this. Records the
                                     decision. Never touches `players`.
  3. promote_review_decision()   — a human explicitly calls this AFTER
                                     resolve(), to actually create a
                                     player record from a resolved
                                     MATCHED decision. Reuses the same
                                     conflict-refusal rule as
                                     matching/loader.py::create_player_records.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import duckdb

from matching.loader import create_player_records
from matching.schema import IdentityInput, MatchCandidate, MatchResult
from matching.schema import VALID_STATUSES  # noqa: F401  (re-exported for callers that want it)


def _candidate_to_dict(c: MatchCandidate) -> dict:
    return {
        "ea_fc26_id": c.ea_fc26_id,
        "transfermarkt_id": c.transfermarkt_id,
        "wikidata_id": c.wikidata_id,
        "confidence": c.confidence,
        "signals": c.signals,
        "matched_on": c.matched_on,
        "status": c.status,
        "reason": c.reason,
    }


def persist_review_queue(con: duckdb.DuckDBPyConnection, results: list[MatchResult]) -> int:
    """Insert a PENDING row for every AMBIGUOUS result, skipping any
    (query_source, query_source_record_id) that already has an open
    (PENDING) or closed (RESOLVED) entry — a rerun must not reopen or
    duplicate a case, whether or not a human has acted on it yet."""
    ambiguous = [r for r in results if r.best is not None and r.best.status == "AMBIGUOUS"]
    if not ambiguous:
        return 0

    existing = set(con.execute(
        "select query_source, query_source_record_id from review_queue"
    ).fetchall())

    next_id_row = con.execute("select coalesce(max(id), 0) + 1 from review_queue").fetchone()
    next_id = next_id_row[0]
    now = datetime.now(timezone.utc)

    inserted = 0
    for result in ambiguous:
        key = (result.query.source, result.query.source_record_id)
        if key in existing:
            continue
        existing.add(key)  # guard against duplicates within the same batch too

        snapshot = json.dumps([_candidate_to_dict(c) for c in result.all_candidates])
        # match_group_id: find it from identity_matches (persist_identity_matches
        # already wrote it) rather than inventing a new one here, so review_queue
        # can always join back to the authoritative scored rows.
        group_row = con.execute(
            "select match_group_id from identity_matches where ea_fc26_id = ? "
            "order by matched_at desc limit 1" if result.query.source == "ea_fc26" else
            "select match_group_id from identity_matches where transfermarkt_id = ? "
            "order by matched_at desc limit 1",
            [int(result.query.external_id)],
        ).fetchone()
        match_group_id = group_row[0] if group_row else str(uuid.uuid4())

        con.execute(
            "insert into review_queue "
            "(id, match_group_id, query_source, query_source_record_id, candidates_snapshot, confidence, status, reviewer_decision, created_at, resolved_at) "
            "values (?, ?, ?, ?, ?, ?, 'PENDING', NULL, ?, NULL)",
            [next_id, match_group_id, result.query.source, result.query.source_record_id, snapshot, result.best.confidence, now],
        )
        next_id += 1
        inserted += 1

    return inserted


def list_pending(con: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = con.execute(
        "select id, match_group_id, query_source, query_source_record_id, candidates_snapshot, confidence, created_at "
        "from review_queue where status = 'PENDING' order by created_at"
    ).fetchall()
    return [
        {
            "id": r[0], "match_group_id": r[1], "query_source": r[2], "query_source_record_id": r[3],
            "candidates": json.loads(r[4]), "confidence": r[5], "created_at": r[6],
        }
        for r in rows
    ]


def get_candidates_for_review(con: duckdb.DuckDBPyConnection, review_queue_id: int) -> list[dict]:
    """Authoritative candidate rows from identity_matches (not just the
    denormalized snapshot), via the stored match_group_id."""
    row = con.execute("select match_group_id from review_queue where id = ?", [review_queue_id]).fetchone()
    if row is None:
        raise ValueError(f"no review_queue row with id {review_queue_id}")
    match_group_id = row[0]
    candidates = con.execute(
        "select ea_fc26_id, transfermarkt_id, wikidata_id, match_status, match_confidence, matched_on, is_best "
        "from identity_matches where match_group_id = ? order by match_confidence desc",
        [match_group_id],
    ).fetchall()
    return [
        {"ea_fc26_id": c[0], "transfermarkt_id": c[1], "wikidata_id": c[2], "status": c[3],
         "confidence": c[4], "matched_on": c[5], "is_best": c[6]}
        for c in candidates
    ]


def resolve(
    con: duckdb.DuckDBPyConnection,
    review_queue_id: int,
    chosen_candidate: dict | None,
    reviewer_note: str,
) -> None:
    """Record a human's decision. chosen_candidate must be one of the
    candidates actually in this item's snapshot (or None, meaning
    'confirmed not a match'). Never touches `players`."""
    row = con.execute(
        "select candidates_snapshot, status from review_queue where id = ?", [review_queue_id]
    ).fetchone()
    if row is None:
        raise ValueError(f"no review_queue row with id {review_queue_id}")
    snapshot, status = row
    if status == "RESOLVED":
        raise ValueError(f"review_queue row {review_queue_id} is already RESOLVED — cannot resolve twice")

    if chosen_candidate is not None:
        candidates = json.loads(snapshot)
        match = any(
            c["ea_fc26_id"] == chosen_candidate.get("ea_fc26_id")
            and c["transfermarkt_id"] == chosen_candidate.get("transfermarkt_id")
            and c["wikidata_id"] == chosen_candidate.get("wikidata_id")
            for c in candidates
        )
        if not match:
            raise ValueError(
                "chosen_candidate must be one of the candidates that were actually "
                "scored for this item — cannot resolve to a candidate that was never considered"
            )

    decision = json.dumps({"chosen": chosen_candidate, "note": reviewer_note})
    now = datetime.now(timezone.utc)
    con.execute(
        "update review_queue set status = 'RESOLVED', reviewer_decision = ?, resolved_at = ? where id = ?",
        [decision, now, review_queue_id],
    )


def promote_review_decision(con: duckdb.DuckDBPyConnection, review_queue_id: int) -> dict:
    """Separate, explicit step AFTER resolve(). Only creates a `players`
    row if the human chose a candidate — refuses on conflict, exactly
    like matching/loader.py::create_player_records. Returns the outcome
    counts from that same function so callers get identical semantics."""
    row = con.execute(
        "select query_source, query_source_record_id, reviewer_decision, status from review_queue where id = ?",
        [review_queue_id],
    ).fetchone()
    if row is None:
        raise ValueError(f"no review_queue row with id {review_queue_id}")
    query_source, query_source_record_id, decision_json, status = row
    if status != "RESOLVED":
        raise ValueError(f"review_queue row {review_queue_id} is not RESOLVED yet — resolve() must be called first")

    decision = json.loads(decision_json)
    chosen = decision["chosen"]
    if chosen is None:
        return {"created": 0, "already_exists_consistent": 0, "skipped_low_confidence": 1, "conflict": 0}

    name_row = con.execute(
        "select field_value from player_field_values where source = ? and source_record_id = ? "
        "and field_name = 'display_name' limit 1",
        [query_source, query_source_record_id],
    ).fetchone()
    display_name = name_row[0] if name_row else ""

    # rebuild a minimal MatchResult so we can reuse create_player_records()
    # unchanged, rather than duplicating its conflict-refusal logic here.
    fake_query = IdentityInput(
        source=query_source, source_record_id=query_source_record_id,
        external_id=query_source_record_id, display_name=display_name, date_of_birth=None,
        nationality=None, club=None, position=None,
    )
    fake_candidate = MatchCandidate(
        ea_fc26_id=chosen["ea_fc26_id"], transfermarkt_id=chosen["transfermarkt_id"],
        wikidata_id=chosen["wikidata_id"], confidence=chosen["confidence"],
        signals=chosen["signals"], matched_on=chosen["matched_on"],
        status="MATCHED", reason="promoted_from_human_review",
    )
    fake_result = MatchResult(query=fake_query, best=fake_candidate, all_candidates=[fake_candidate])
    return create_player_records(con, [fake_result], statuses_to_promote=("MATCHED",))
