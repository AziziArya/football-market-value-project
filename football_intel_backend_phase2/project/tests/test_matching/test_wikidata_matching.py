import dataclasses
import inspect
import uuid
from datetime import date, datetime, timezone

import duckdb
import pytest

import matching.identity as identity_module
import matching.wikidata_enrichment as enrichment_module
from db.migrate import run_migrations
from ingestion.providers.base import RawRecord
from matching.identity import (
    DEFAULT_THRESHOLDS,
    build_identity_input_for_player,
    from_wikidata_record,
    score_pair,
)
from matching.schema import MatchThresholds


def _now():
    return datetime.now(timezone.utc)


def _wd_record(qid="Q28967995", **payload):
    base = {"qid": qid, "name": "Erling Haaland"}
    base.update(payload)
    return RawRecord(source="wikidata", source_record_id=qid, record_type="player",
                     payload=base, fetched_at=_now(), dataset_version="live:2026-09-28")


@pytest.fixture
def db_con(tmp_path):
    db_path = tmp_path / "t.duckdb"
    run_migrations(db_path)
    con = duckdb.connect(str(db_path))
    yield con
    con.close()


def _seed_player(con, ea_id=239085, name="Erling Haaland"):
    uid = str(uuid.uuid4())
    con.execute(
        "insert into players (player_uid, ea_fc26_id, display_name, created_at, updated_at) values (?, ?, ?, ?, ?)",
        [uid, ea_id, name, _now(), _now()],
    )
    return uid


def _seed_field(con, uid, source, field, value, _id=[0]):
    _id[0] += 1
    con.execute(
        "insert into player_field_values (id, player_uid, field_name, field_value, source, source_record_id, fetched_at, dataset_version) "
        "values (?, ?, ?, ?, ?, 'x', ?, 'v1')",
        [_id[0] + hash(uid) % 1_000_000 * 100, uid, field, value, source, _now()],
    )


# ═══════════════════════════════════════════════════════════
# from_wikidata_record
# ═══════════════════════════════════════════════════════════

def test_from_wikidata_record_maps_fields():
    r = _wd_record(date_of_birth="2000-07-21", country_of_citizenship="Norway")
    ident = from_wikidata_record(r)
    assert ident.source == "wikidata"
    assert ident.external_id == "Q28967995"
    assert ident.display_name == "Erling Haaland"
    assert ident.date_of_birth == date(2000, 7, 21)
    assert ident.nationality == "Norway"


def test_from_wikidata_record_never_invents_club_or_position():
    ident = from_wikidata_record(_wd_record(date_of_birth="2000-07-21", country_of_citizenship="Norway"))
    assert ident.club is None
    assert ident.position is None


def test_from_wikidata_record_missing_optional_fields_stay_none():
    ident = from_wikidata_record(_wd_record(date_of_birth=None, country_of_citizenship=None))
    assert ident.date_of_birth is None
    assert ident.nationality is None


def test_from_wikidata_record_rejects_wrong_source():
    bad = RawRecord("ea_fc26", "1", "player", {"id": 1}, _now())
    with pytest.raises(AssertionError):
        from_wikidata_record(bad)


# ═══════════════════════════════════════════════════════════
# build_identity_input_for_player
# ═══════════════════════════════════════════════════════════

def test_build_identity_input_uses_ea_fields(db_con):
    uid = _seed_player(db_con)
    _seed_field(db_con, uid, "ea_fc26", "date_of_birth", "2000-07-21")
    _seed_field(db_con, uid, "ea_fc26", "nationality", "Norway")
    _seed_field(db_con, uid, "ea_fc26", "club", "Manchester City")
    ident = build_identity_input_for_player(db_con, uid)
    assert ident.display_name == "Erling Haaland"
    assert ident.external_id == "239085"
    assert ident.date_of_birth == date(2000, 7, 21)
    assert ident.nationality == "Norway"
    assert ident.club == "Manchester City"


def test_build_identity_input_prefers_ea_over_transfermarkt(db_con):
    uid = _seed_player(db_con)
    _seed_field(db_con, uid, "transfermarkt_dataset", "club", "TM Club")
    _seed_field(db_con, uid, "ea_fc26", "club", "EA Club")
    assert build_identity_input_for_player(db_con, uid).club == "EA Club"


def test_build_identity_input_falls_back_to_transfermarkt(db_con):
    uid = _seed_player(db_con)
    _seed_field(db_con, uid, "transfermarkt_dataset", "nationality", "Norway")
    assert build_identity_input_for_player(db_con, uid).nationality == "Norway"


def test_build_identity_input_missing_fields_are_none_not_invented(db_con):
    uid = _seed_player(db_con)
    ident = build_identity_input_for_player(db_con, uid)
    assert ident.date_of_birth is None
    assert ident.nationality is None
    assert ident.club is None
    assert ident.position is None


def test_build_identity_input_unknown_player_raises(db_con):
    with pytest.raises(ValueError):
        build_identity_input_for_player(db_con, "does-not-exist")


# ═══════════════════════════════════════════════════════════
# reuse of the existing engine — no new logic, no new thresholds
# ═══════════════════════════════════════════════════════════

def test_score_pair_assigns_wikidata_id_and_keeps_ea_id(db_con):
    uid = _seed_player(db_con)
    _seed_field(db_con, uid, "ea_fc26", "date_of_birth", "2000-07-21")
    _seed_field(db_con, uid, "ea_fc26", "nationality", "Norway")
    player = build_identity_input_for_player(db_con, uid)
    wd = from_wikidata_record(_wd_record(date_of_birth="2000-07-21", country_of_citizenship="Norway"))
    c = score_pair(player, wd)
    assert c.wikidata_id == "Q28967995"
    assert c.ea_fc26_id == "239085"
    assert c.status == "PROBABLE_MATCH"  # no club on the wikidata side caps the score at 0.85


def test_matching_thresholds_dataclass_has_no_wikidata_specific_fields():
    fields = {f.name for f in dataclasses.fields(MatchThresholds)}
    assert fields == {
        "matched", "probable", "ambiguous", "ambiguity_margin", "dob_conflict_review_floor",
        "weight_name", "weight_dob", "weight_nationality", "weight_club",
    }


def test_no_wikidata_specific_thresholds_or_scoring_in_identity_module():
    names = [n for n in dir(identity_module) if "wikidata" in n.lower()]
    assert names == ["from_wikidata_record"]  # the adapter is the ONLY wikidata-specific symbol


def test_enrichment_defaults_to_the_shared_default_thresholds():
    sig = inspect.signature(enrichment_module.enrich_players_with_wikidata)
    assert sig.parameters["thresholds"].default is DEFAULT_THRESHOLDS


def test_enrichment_calls_the_existing_match_one_with_default_thresholds(db_con, monkeypatch):
    uid = _seed_player(db_con)
    _seed_field(db_con, uid, "ea_fc26", "date_of_birth", "2000-07-21")
    _seed_field(db_con, uid, "ea_fc26", "nationality", "Norway")

    seen = []
    real = enrichment_module.match_one

    def spy(query, candidates, thresholds):
        seen.append(thresholds)
        return real(query, candidates, thresholds)

    monkeypatch.setattr(enrichment_module, "match_one", spy)
    enrichment_module.enrich_players_with_wikidata(
        db_con, [_wd_record(date_of_birth="2000-07-21", country_of_citizenship="Norway")]
    )
    assert seen and all(t is DEFAULT_THRESHOLDS for t in seen)
