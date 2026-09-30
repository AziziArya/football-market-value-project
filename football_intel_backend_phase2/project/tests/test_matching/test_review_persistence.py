from datetime import date

import duckdb
import pytest

from db.migrate import run_migrations
from matching.identity import match_all
from matching.loader import persist_identity_matches
from matching.review_persistence import (
    get_candidates_for_review,
    list_pending,
    persist_review_queue,
    promote_review_decision,
    resolve,
)
from matching.schema import IdentityInput
from ingestion.loader import load_identity_fields
from ingestion.normalizer import IdentityFieldValue
from datetime import datetime, timezone


def ea(name, dob, nat, club, ext="1"):
    return IdentityInput(source="ea_fc26", source_record_id=ext, external_id=ext,
                          display_name=name, date_of_birth=dob, nationality=nat, club=club, position=None)


def tm(name, dob, nat, club, ext="100"):
    return IdentityInput(source="transfermarkt_dataset", source_record_id=ext, external_id=ext,
                          display_name=name, date_of_birth=dob, nationality=nat, club=club, position=None)


@pytest.fixture
def db_con(tmp_path):
    db_path = tmp_path / "test.duckdb"
    run_migrations(db_path)
    con = duckdb.connect(str(db_path))
    yield con
    con.close()


def _seed_ambiguous_case(con):
    """Builds a real AMBIGUOUS result (two equally-good candidates),
    persists identity_matches for it (so match_group_id exists), and
    seeds the display_name into player_field_values (needed by promote)."""
    query = ea("John Smith", date(1999, 1, 1), "England", "Club A", ext="1")
    cand1 = tm("John Smith", date(1999, 1, 1), "England", "Club B", ext="201")
    cand2 = tm("John Smith", date(1999, 1, 1), "England", "Club C", ext="202")
    results = match_all([query], [cand1, cand2])
    persist_identity_matches(con, results)
    load_identity_fields(con, [
        IdentityFieldValue("ea_fc26", "1", "display_name", "John Smith", datetime.now(timezone.utc), "test-version"),
    ])
    return results


# ═══════════════════════════════════════════════════════════
# persist_review_queue
# ═══════════════════════════════════════════════════════════

def test_persist_review_queue_inserts_pending_row(db_con):
    results = _seed_ambiguous_case(db_con)
    n = persist_review_queue(db_con, results)
    assert n == 1
    pending = list_pending(db_con)
    assert len(pending) == 1
    assert pending[0]["query_source_record_id"] == "1"
    assert len(pending[0]["candidates"]) == 2


def test_persist_review_queue_ignores_non_ambiguous(db_con):
    matched = match_all(
        [ea("Erling Haaland", date(2000, 7, 21), "Norway", "Manchester City", ext="1")],
        [tm("Erling Haaland", date(2000, 7, 21), "Norway", "Manchester City", ext="101")],
    )
    persist_identity_matches(db_con, matched)
    n = persist_review_queue(db_con, matched)
    assert n == 0
    assert list_pending(db_con) == []


def test_persist_review_queue_deduped_on_rerun(db_con):
    results = _seed_ambiguous_case(db_con)
    persist_review_queue(db_con, results)
    # simulate a rerun: re-persist identity_matches (new match_group_id) and
    # re-attempt persist_review_queue for the "same" conceptual query
    persist_identity_matches(db_con, results)
    n_second = persist_review_queue(db_con, results)
    assert n_second == 0  # not duplicated — already has an open entry
    assert len(list_pending(db_con)) == 1


# ═══════════════════════════════════════════════════════════
# get_candidates_for_review
# ═══════════════════════════════════════════════════════════

def test_get_candidates_for_review_joins_identity_matches(db_con):
    results = _seed_ambiguous_case(db_con)
    persist_review_queue(db_con, results)
    item_id = list_pending(db_con)[0]["id"]
    candidates = get_candidates_for_review(db_con, item_id)
    assert len(candidates) == 2
    assert {c["transfermarkt_id"] for c in candidates} == {201, 202}


# ═══════════════════════════════════════════════════════════
# resolve — records decision, never touches players
# ═══════════════════════════════════════════════════════════

def test_resolve_records_decision_without_touching_players(db_con):
    results = _seed_ambiguous_case(db_con)
    persist_review_queue(db_con, results)
    item_id = list_pending(db_con)[0]["id"]
    candidates = list_pending(db_con)[0]["candidates"]
    chosen = candidates[0]

    resolve(db_con, item_id, chosen, "verified manually against transfermarkt profile")

    assert list_pending(db_con) == []  # no longer pending
    row = db_con.execute("select status, reviewer_decision, resolved_at from review_queue where id=?", [item_id]).fetchone()
    assert row[0] == "RESOLVED"
    assert "verified manually" in row[1]
    assert row[2] is not None

    # critically: players table untouched by resolve() alone
    players_count = db_con.execute("select count(*) from players").fetchone()[0]
    assert players_count == 0


def test_resolve_rejects_candidate_not_in_snapshot(db_con):
    results = _seed_ambiguous_case(db_con)
    persist_review_queue(db_con, results)
    item_id = list_pending(db_con)[0]["id"]

    fake_candidate = {"ea_fc26_id": "999", "transfermarkt_id": "888", "wikidata_id": None,
                       "confidence": 0.99, "signals": {}, "matched_on": [], "status": "MATCHED", "reason": "fabricated"}
    with pytest.raises(ValueError, match="never considered"):
        resolve(db_con, item_id, fake_candidate, "trying to sneak one in")


def test_resolve_can_confirm_no_match(db_con):
    results = _seed_ambiguous_case(db_con)
    persist_review_queue(db_con, results)
    item_id = list_pending(db_con)[0]["id"]
    resolve(db_con, item_id, None, "neither candidate is actually this player")
    row = db_con.execute("select reviewer_decision from review_queue where id=?", [item_id]).fetchone()
    assert '"chosen": null' in row[0]


def test_resolve_twice_raises(db_con):
    results = _seed_ambiguous_case(db_con)
    persist_review_queue(db_con, results)
    item_id = list_pending(db_con)[0]["id"]
    resolve(db_con, item_id, None, "first decision")
    with pytest.raises(ValueError, match="already RESOLVED"):
        resolve(db_con, item_id, None, "trying to change my mind")


# ═══════════════════════════════════════════════════════════
# promote_review_decision — separate explicit step, conflict-safe
# ═══════════════════════════════════════════════════════════

def test_promote_creates_player_after_chosen_resolution(db_con):
    results = _seed_ambiguous_case(db_con)
    persist_review_queue(db_con, results)
    item_id = list_pending(db_con)[0]["id"]
    chosen = list_pending(db_con)[0]["candidates"][0]
    resolve(db_con, item_id, chosen, "confirmed via transfermarkt profile page")

    counts = promote_review_decision(db_con, item_id)
    assert counts["created"] == 1
    row = db_con.execute("select ea_fc26_id, transfermarkt_id, display_name from players").fetchone()
    assert row[0] == 1
    assert row[1] == int(chosen["transfermarkt_id"])
    assert row[2] == "John Smith"


def test_promote_does_nothing_when_resolved_as_no_match(db_con):
    results = _seed_ambiguous_case(db_con)
    persist_review_queue(db_con, results)
    item_id = list_pending(db_con)[0]["id"]
    resolve(db_con, item_id, None, "not a match")

    counts = promote_review_decision(db_con, item_id)
    assert counts["created"] == 0
    assert db_con.execute("select count(*) from players").fetchone()[0] == 0


def test_promote_before_resolve_raises(db_con):
    results = _seed_ambiguous_case(db_con)
    persist_review_queue(db_con, results)
    item_id = list_pending(db_con)[0]["id"]
    with pytest.raises(ValueError, match="not RESOLVED yet"):
        promote_review_decision(db_con, item_id)


def test_promote_refuses_conflicting_existing_link(db_con):
    results = _seed_ambiguous_case(db_con)
    persist_review_queue(db_con, results)
    item_id = list_pending(db_con)[0]["id"]
    chosen = list_pending(db_con)[0]["candidates"][0]  # tm 201

    # a DIFFERENT process already linked ea_fc26_id=1 to a different transfermarkt id
    import uuid
    now = datetime.now(timezone.utc)
    db_con.execute(
        "insert into players (player_uid, ea_fc26_id, transfermarkt_id, display_name, created_at, updated_at) "
        "values (?, 1, 555, 'John Smith', ?, ?)",
        [str(uuid.uuid4()), now, now],
    )

    resolve(db_con, item_id, chosen, "reviewer picked 201, unaware of existing conflicting link")
    counts = promote_review_decision(db_con, item_id)
    assert counts["conflict"] == 1
    # original link untouched
    row = db_con.execute("select transfermarkt_id from players where ea_fc26_id=1").fetchone()
    assert row[0] == 555
