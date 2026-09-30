import uuid
from datetime import datetime, timezone

import duckdb
import pytest

from db.migrate import run_migrations
from ingestion.injury_loader import mark_no_injury_source_available


@pytest.fixture
def db_con(tmp_path):
    db_path = tmp_path / "test.duckdb"
    run_migrations(db_path)
    con = duckdb.connect(str(db_path))
    yield con
    con.close()


def _insert_player(con):
    player_uid = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    con.execute(
        "insert into players (player_uid, ea_fc26_id, display_name, created_at, updated_at) values (?, ?, ?, ?, ?)",
        [player_uid, hash(player_uid) % 1_000_000, "Test Player", now, now],
    )
    return player_uid


def test_marks_all_players_without_status(db_con):
    p1 = _insert_player(db_con)
    p2 = _insert_player(db_con)
    n = mark_no_injury_source_available(db_con)
    assert n == 2
    rows = db_con.execute("select player_uid, status from injury_data_status order by player_uid").fetchall()
    statuses = {r[1] for r in rows}
    assert statuses == {"NO_SOURCE_AVAILABLE"}


def test_idempotent_does_not_duplicate(db_con):
    _insert_player(db_con)
    mark_no_injury_source_available(db_con)
    n2 = mark_no_injury_source_available(db_con)
    assert n2 == 0
    count = db_con.execute("select count(*) from injury_data_status").fetchone()[0]
    assert count == 1


def test_never_downgrades_an_existing_confirmed_status(db_con):
    """If some future real source ever sets CONFIRMED_NO_INJURIES or
    HAS_RECORDS, this function must never overwrite it back to
    NO_SOURCE_AVAILABLE."""
    player_uid = _insert_player(db_con)
    db_con.execute(
        "insert into injury_data_status (player_uid, status, checked_at) values (?, 'CONFIRMED_NO_INJURIES', ?)",
        [player_uid, datetime.now(timezone.utc)],
    )
    mark_no_injury_source_available(db_con)
    row = db_con.execute("select status from injury_data_status where player_uid=?", [player_uid]).fetchone()
    assert row[0] == "CONFIRMED_NO_INJURIES"


def test_no_players_no_rows(db_con):
    n = mark_no_injury_source_available(db_con)
    assert n == 0
