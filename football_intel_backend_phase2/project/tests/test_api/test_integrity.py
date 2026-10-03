"""Fail-closed startup (AD-4): every violated invariant => the app still starts, data endpoints answer 503,
and nothing internal (paths, SQL, exception text) reaches the client."""
import duckdb
import pytest

from api.domain.enums import EntityKind
from api.repositories.database import Database
from api.services.integrity_service import build_runtime

LEAK_MARKERS = ("duckdb", "Traceback", "select ", ".duckdb", "/tmp", "ea_fc26_attributes", "invariant")


def _assert_closed(client, expected_problem):
    state = client.app.state.services.runtime.state
    assert state.status == "unavailable" and any(expected_problem in p for p in state.problems), state.problems
    for path in ("/api/v1/health", "/api/v1/data-freshness"):
        r = client.get(path)
        assert r.status_code == 503 and r.headers["content-type"].startswith("application/problem+json")
        body = r.json()
        assert body["code"] == "DATA_UNAVAILABLE" and body["status"] == 503
        assert not any(m in r.text for m in LEAK_MARKERS), r.text


def _rw(path):
    return duckdb.connect(str(path))


def test_healthy_database_starts_ok(small_db):
    state = build_runtime(db := Database(small_db))
    assert state.ok and state.problems == () and len(state.index) == 50 and len(state.link_states) == 100
    assert state.index.count(EntityKind.CANONICAL) == 5
    db.close()


def test_missing_database_file(tmp_path, make_client):
    _assert_closed(make_client(tmp_path / "missing.duckdb"), "cannot be opened")


def test_unmigrated_empty_database(tmp_path, make_client):
    path = tmp_path / "empty.duckdb"
    _rw(path).close()
    _assert_closed(make_client(path), "missing schema objects")


def test_player_without_display_name(db_copy, make_client):
    con = _rw(db_copy)
    con.execute("delete from player_field_values where source='ea_fc26' and field_name='display_name' and source_record_id='209331'")
    con.close()
    _assert_closed(make_client(db_copy), "no display_name")


def test_predictions_present_although_no_model_is_integrated(db_copy, make_client):
    con = _rw(db_copy)
    con.execute("update ea_fc26_attributes set predicted_value_eur = 1000000 where ea_fc26_id = 209331")
    con.close()
    _assert_closed(make_client(db_copy), "no model is integrated")


def test_model_version_without_model(db_copy, make_client):
    con = _rw(db_copy)
    con.execute("update ea_fc26_attributes set model_version = 'x' where ea_fc26_id = 209331")
    con.close()
    _assert_closed(make_client(db_copy), "no model is integrated")


def test_player_without_overall_rating_or_potential(db_copy, make_client):
    """G14: the columns are nullable in the database, but the contract requires both: fail closed instead of answering 500s."""
    con = _rw(db_copy)
    con.execute("update ea_fc26_attributes set overall_rating = NULL where ea_fc26_id = 209331")
    con.close()
    _assert_closed(make_client(db_copy), "no overall_rating/potential")


def test_player_without_potential(db_copy, make_client):
    con = _rw(db_copy)
    con.execute("update ea_fc26_attributes set potential = NULL where ea_fc26_id = 209331")
    con.close()
    _assert_closed(make_client(db_copy), "no overall_rating/potential")


def test_matched_link_for_a_player_that_is_ea_only(db_copy, make_client):
    """A MATCHED link must never be shown next to an EA_ONLY identity (no inferred canonical identity)."""
    con = _rw(db_copy)
    nid = con.execute("select max(id) from identity_matches").fetchone()[0] + 1
    con.execute("insert into identity_matches (id, ea_fc26_id, transfermarkt_id, wikidata_id, match_status, match_confidence, matched_on, "
                f"matched_at, match_group_id, is_best) values ({nid}, 209331, NULL, NULL, 'MATCHED', 1.0, 'name', now() + interval 1 hour, 'g-x', true)")
    con.close()
    _assert_closed(make_client(db_copy), "is EA_ONLY".replace("is EA_ONLY", "EA_ONLY"))


def test_database_with_no_players(tmp_path, make_client):
    from tests.test_api.conftest import build_db
    path = build_db(tmp_path)
    con = _rw(path)
    for t in ("player_images", "player_wikidata_links", "injury_data_status", "review_queue", "identity_matches",
              "market_value_history", "transfers", "appearances", "player_field_values", "ea_fc26_attributes", "players"):
        con.execute(f"delete from {t}")
    con.close()
    _assert_closed(make_client(path), "no EA players")


def test_a_failed_startup_never_raises_out_of_the_factory(tmp_path):
    state = build_runtime(Database(tmp_path / "x.duckdb"))
    assert state.status == "unavailable" and state.index is None and not state.ok
