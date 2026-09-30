from datetime import date
from pathlib import Path

import duckdb
import pytest

from db.migrate import run_migrations
from matching.identity import match_one
from matching.loader import create_player_records, persist_identity_matches
from matching.schema import IdentityInput


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


def test_persist_identity_matches_writes_all_candidates(db_con):
    result = match_one(
        ea("Erling Haaland", date(2000, 7, 21), "Norway", "Manchester City", ext="239085"),
        [tm("Erling Haaland", date(2000, 7, 21), "Norway", "Manchester City", ext="418560")],
    )
    written = persist_identity_matches(db_con, [result])
    assert written == 1
    row = db_con.execute("select ea_fc26_id, transfermarkt_id, match_status from identity_matches").fetchone()
    assert row == (239085, 418560, "MATCHED")


def test_persist_identity_matches_sets_group_id_and_is_best(db_con):
    query = ea("John Smith", date(1999, 1, 1), "England", "Club A", ext="1")
    cand1 = tm("John Smith", date(1999, 1, 1), "England", "Club B", ext="201")
    cand2 = tm("John Smith", date(1999, 1, 1), "England", "Club C", ext="202")
    result = match_one(query, [cand1, cand2])  # forced AMBIGUOUS via margin rule
    persist_identity_matches(db_con, [result])

    rows = db_con.execute(
        "select transfermarkt_id, match_status, is_best, match_group_id from identity_matches order by id"
    ).fetchall()
    assert len(rows) == 2
    group_ids = {r[3] for r in rows}
    assert len(group_ids) == 1  # both candidates share one group id
    best_rows = [r for r in rows if r[2]]  # is_best = True
    assert len(best_rows) == 1
    assert best_rows[0][1] == "AMBIGUOUS"  # is_best row's status matches result.best.status exactly


def test_persist_identity_matches_is_append_only(db_con):
    result = match_one(ea("Player A", date(2000, 1, 1), "England", "Club"), [])
    persist_identity_matches(db_con, [result])
    persist_identity_matches(db_con, [result])
    count = db_con.execute("select count(*) from identity_matches").fetchone()[0]
    assert count == 2  # both writes kept, nothing overwritten


def test_create_player_records_creates_for_matched(db_con):
    result = match_one(
        ea("Erling Haaland", date(2000, 7, 21), "Norway", "Manchester City", ext="239085"),
        [tm("Erling Haaland", date(2000, 7, 21), "Norway", "Manchester City", ext="418560")],
    )
    counts = create_player_records(db_con, [result])
    assert counts["created"] == 1
    row = db_con.execute("select ea_fc26_id, transfermarkt_id, display_name from players").fetchone()
    assert row == (239085, 418560, "Erling Haaland")


def test_create_player_records_skips_ambiguous_and_unmatched(db_con):
    ambiguous = match_one(
        ea("John Smith", date(1999, 1, 1), "England", "Club A", ext="1"),
        [tm("John Smith", date(1999, 1, 1), "England", "Club B", ext="201"),
         tm("John Smith", date(1999, 1, 1), "England", "Club C", ext="202")],
    )
    unmatched = match_one(ea("Nobody", date(2000, 1, 1), "Iceland", "X", ext="2"), [])

    counts = create_player_records(db_con, [ambiguous, unmatched])
    assert counts["created"] == 0
    assert counts["skipped_low_confidence"] == 2
    total_players = db_con.execute("select count(*) from players").fetchone()[0]
    assert total_players == 0


def test_create_player_records_never_overwrites_existing_conflicting_link(db_con):
    first = match_one(
        ea("Player X", date(2000, 1, 1), "England", "Club", ext="111"),
        [tm("Player X", date(2000, 1, 1), "England", "Club", ext="999")],
    )
    create_player_records(db_con, [first])

    # same EA id, but now scored against a DIFFERENT transfermarkt id — must not overwrite
    conflicting = match_one(
        ea("Player X", date(2000, 1, 1), "England", "Club", ext="111"),
        [tm("Player X", date(2000, 1, 1), "England", "Club", ext="777")],
    )
    counts = create_player_records(db_con, [conflicting])
    assert counts["conflict"] == 1

    row = db_con.execute("select ea_fc26_id, transfermarkt_id from players where ea_fc26_id = 111").fetchone()
    assert row == (111, 999)  # original link untouched


def test_create_player_records_idempotent_for_same_pair(db_con):
    result = match_one(
        ea("Player Y", date(2000, 1, 1), "England", "Club", ext="222"),
        [tm("Player Y", date(2000, 1, 1), "England", "Club", ext="888")],
    )
    counts1 = create_player_records(db_con, [result])
    counts2 = create_player_records(db_con, [result])
    assert counts1["created"] == 1
    assert counts2["already_exists_consistent"] == 1
    total = db_con.execute("select count(*) from players").fetchone()[0]
    assert total == 1
