import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

from config.settings import PipelineConfig
from db.migrate import run_migrations
from ingestion.providers.base import RawRecord
from matching.wikidata_enrichment import enrich_players_with_wikidata
from scripts.run_ingestion import run_pipeline

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures"
SMALL_EA_CSV = FIXTURES_DIR / "small_ea_fc26.csv"
TM_SAMPLE_DIR = Path(__file__).parents[2] / "data" / "raw" / "transfermarkt_dataset" / "sample_2026-07-06"
CORE = ("ea_fc26", "transfermarkt_dataset")

HAALAND, MBAPPE, VINI, SAKA, BELLINGHAM = 239085, 231747, 238794, 246669, 252371
REAL_NAMES = {HAALAND: "Erling Haaland", MBAPPE: "Kylian Mbappé"}


def _now():
    return datetime.now(timezone.utc)


def wd(qid, name, dob=None, nat=None, image=None, license=None, attribution=None, version="live:2026-09-28"):
    return RawRecord(
        source="wikidata", source_record_id=qid, record_type="player",
        payload={"qid": qid, "name": name, "date_of_birth": dob, "country_of_citizenship": nat,
                 "image_filename": image, "image_license": license, "image_attribution": attribution},
        fetched_at=_now(), dataset_version=version,
    )


def good_records():
    return [
        wd("Q28967995", "Erling Haaland", "2000-07-21", "Norway", image="Haaland Photo.jpg", attribution="Bryan Berlin"),
        wd("Q00000001", "Kylian Mbappe", "1998-12-20", "France"),
    ]


def _build_db(tmp_path, preset_wikidata_ids=None):
    """Real core pipeline (EA + transfermarkt, no wikidata) -> real canonical players.
    `preset_wikidata_ids` {ea_id: qid}: players created BEFORE the pipeline with a wikidata_id
    (the only moment it can be set — see migration 0006)."""
    db_path = tmp_path / "enrich.duckdb"
    run_migrations(db_path)
    if preset_wikidata_ids:
        con = duckdb.connect(str(db_path))
        for ea_id, qid in preset_wikidata_ids.items():
            con.execute(
                "insert into players (player_uid, ea_fc26_id, wikidata_id, display_name, created_at, updated_at) "
                "values (?, ?, ?, ?, ?, ?)",
                [str(uuid.uuid4()), ea_id, qid, REAL_NAMES[ea_id], _now(), _now()],
            )
        con.close()
    report = run_pipeline(PipelineConfig(
        db_path=db_path, ea_fc26_csv_path=SMALL_EA_CSV, transfermarkt_dump_dir=TM_SAMPLE_DIR,
        enabled_providers=CORE, report_dir=tmp_path / "reports",
    ))
    assert report.success
    return duckdb.connect(str(db_path))


@pytest.fixture
def con(tmp_path):
    c = _build_db(tmp_path)
    yield c
    c.close()


def _count(con, table, where=""):
    return con.execute(f"select count(*) from {table} {where}").fetchone()[0]


def _nothing_stored(con):
    assert _count(con, "player_wikidata_links") == 0
    assert _count(con, "player_field_values", "where source = 'wikidata'") == 0
    assert _count(con, "player_images") == 0


# ═══════════════════════════════════════════════════════════
# happy path + non-destructiveness
# ═══════════════════════════════════════════════════════════

def test_links_stores_provenance_and_leaves_players_table_untouched(con):
    counts = enrich_players_with_wikidata(con, good_records())
    assert counts["linked"] == 2
    row = con.execute(
        "select w.wikidata_id, w.match_status, w.match_confidence, w.matched_on, w.dataset_version "
        "from player_wikidata_links w join players p using(player_uid) where p.ea_fc26_id = ?", [HAALAND],
    ).fetchone()
    assert row[0] == "Q28967995"
    assert row[1] == "PROBABLE_MATCH"
    assert row[2] == pytest.approx(0.85, abs=1e-3)
    assert row[3] == "name,dob,nationality"
    assert row[4] == "live:2026-09-28"
    assert _count(con, "players", "where wikidata_id is not null") == 0  # players.wikidata_id never modified


def test_v_players_exposes_effective_wikidata_id(con):
    enrich_players_with_wikidata(con, good_records())
    rows = dict(con.execute("select ea_fc26_id, wikidata_id from v_players").fetchall())
    assert rows[HAALAND] == "Q28967995"
    assert rows[MBAPPE] == "Q00000001"
    assert rows[SAKA] is None  # no candidate -> stays NULL


def test_never_touches_ea_or_transfermarkt_rows(con):
    before = con.execute(
        "select * from player_field_values where source != 'wikidata' order by id"
    ).fetchall()
    enrich_players_with_wikidata(con, good_records())
    after = con.execute(
        "select * from player_field_values where source != 'wikidata' order by id"
    ).fetchall()
    assert before == after


def test_wikidata_rows_are_source_tagged_and_separate(con):
    enrich_players_with_wikidata(con, good_records())
    rows = con.execute(
        "select distinct source, dataset_version from player_field_values where source = 'wikidata'"
    ).fetchall()
    assert rows == [("wikidata", "live:2026-09-28")]
    # EA nationality for Haaland is still there, alongside (not replaced by) the wikidata one
    sources = {r[0] for r in con.execute(
        "select f.source from player_field_values f join players p using(player_uid) "
        "where p.ea_fc26_id = ? and f.field_name = 'nationality'", [HAALAND]).fetchall()}
    assert {"ea_fc26", "wikidata"} <= sources


def test_link_table_constraints_block_relinking_by_construction(con):
    enrich_players_with_wikidata(con, good_records())
    uid = con.execute("select player_uid from players where ea_fc26_id = ?", [HAALAND]).fetchone()[0]
    with pytest.raises(duckdb.ConstraintException):  # PK(player_uid): cannot re-link a player
        con.execute("insert into player_wikidata_links values (?, 'Q1', 'MATCHED', 1.0, '', 'v', ?)", [uid, _now()])
    other = con.execute("select player_uid from players where ea_fc26_id = ?", [SAKA]).fetchone()[0]
    with pytest.raises(duckdb.ConstraintException):  # UNIQUE(wikidata_id): a QID cannot have two owners
        con.execute("insert into player_wikidata_links values (?, 'Q28967995', 'MATCHED', 1.0, '', 'v', ?)", [other, _now()])


# ═══════════════════════════════════════════════════════════
# conflict protection
# ═══════════════════════════════════════════════════════════

def test_existing_different_players_wikidata_id_is_never_overwritten(tmp_path):
    c = _build_db(tmp_path, preset_wikidata_ids={HAALAND: "Q999"})
    counts = enrich_players_with_wikidata(c, good_records())
    assert counts["conflict_skipped"] == 1
    assert counts["linked"] == 1  # Mbappe still links normally
    assert c.execute("select wikidata_id from v_players where ea_fc26_id = ?", [HAALAND]).fetchone()[0] == "Q999"
    uid = c.execute("select player_uid from players where ea_fc26_id = ?", [HAALAND]).fetchone()[0]
    assert _count(c, "player_wikidata_links", f"where player_uid = '{uid}'") == 0
    assert _count(c, "player_field_values", f"where source = 'wikidata' and player_uid = '{uid}'") == 0
    assert _count(c, "player_images", f"where player_uid = '{uid}'") == 0
    c.close()


def test_existing_same_qid_in_players_is_accepted_not_a_conflict(tmp_path):
    c = _build_db(tmp_path, preset_wikidata_ids={HAALAND: "Q28967995"})
    counts = enrich_players_with_wikidata(c, good_records())
    assert counts["conflict_skipped"] == 0
    assert c.execute("select wikidata_id from v_players where ea_fc26_id = ?", [HAALAND]).fetchone()[0] == "Q28967995"
    c.close()


def test_qid_already_owned_by_another_player_is_not_shared(tmp_path):
    # Mbappe already owns Haaland's QID -> Haaland must not get it, and Mbappe's own
    # (different) candidate QID must not overwrite what he already has.
    c = _build_db(tmp_path, preset_wikidata_ids={MBAPPE: "Q28967995"})
    counts = enrich_players_with_wikidata(c, good_records())
    assert counts["conflict_skipped"] == 2
    assert counts["linked"] == 0
    assert _count(c, "player_wikidata_links") == 0
    assert _count(c, "player_field_values", "where source = 'wikidata'") == 0
    c.close()


def test_existing_link_with_different_qid_is_never_overwritten(con):
    enrich_players_with_wikidata(con, good_records())
    changed = [wd("Q777", "Erling Haaland", "2000-07-21", "Norway")]
    counts = enrich_players_with_wikidata(con, changed)
    assert counts["conflict_skipped"] == 1
    assert con.execute("select wikidata_id from v_players where ea_fc26_id = ?", [HAALAND]).fetchone()[0] == "Q28967995"
    assert _count(con, "player_wikidata_links", "where wikidata_id = 'Q777'") == 0


# ═══════════════════════════════════════════════════════════
# no fabrication: nothing is stored unless confident
# ═══════════════════════════════════════════════════════════

def test_zero_candidates_stores_nothing(con):
    counts = enrich_players_with_wikidata(con, [])
    assert counts["linked"] == 0
    assert counts["no_confident_match"] == _count(con, "players")
    _nothing_stored(con)


def test_multiple_candidates_stores_nothing(con):
    twins = [
        wd("Q1000001", "Erling Haaland", "2000-07-21", "Norway", image="a.jpg"),
        wd("Q1000002", "Erling Haaland", "2000-07-21", "Norway", image="b.jpg"),
    ]
    counts = enrich_players_with_wikidata(con, twins)
    assert counts["linked"] == 0
    _nothing_stored(con)  # no QID, no identity field, no image for an ambiguous pick


def test_dob_conflict_candidate_is_not_trusted(con):
    counts = enrich_players_with_wikidata(con, [wd("Q5", "Erling Haaland", "1990-01-01", "Norway", image="x.jpg")])
    assert counts["linked"] == 0
    _nothing_stored(con)


def test_unrelated_person_stores_nothing(con):
    counts = enrich_players_with_wikidata(con, [wd("Q6", "Somebody Else", "1970-05-05", "Iceland", image="y.jpg")])
    assert counts["linked"] == 0
    _nothing_stored(con)


def test_missing_image_fields_stay_null_and_no_image_row_is_made(con):
    counts = enrich_players_with_wikidata(con, [wd("Q28967995", "Erling Haaland", "2000-07-21", "Norway")])
    assert counts["linked"] == 1
    assert _count(con, "player_images") == 0
    fields = {r[0] for r in con.execute("select field_name from player_field_values where source = 'wikidata'").fetchall()}
    assert fields == {"display_name", "nationality", "date_of_birth"}


def test_missing_license_and_attribution_stay_null(con):
    enrich_players_with_wikidata(con, [wd("Q28967995", "Erling Haaland", "2000-07-21", "Norway", image="Photo Name.jpg")])
    row = con.execute("select image_url, license, attribution, source from player_images").fetchone()
    assert row[0] == "https://commons.wikimedia.org/wiki/Special:FilePath/Photo_Name.jpg"  # encoded, valid URL
    assert row[1] is None and row[2] is None  # never guessed
    assert row[3] == "wikidata"


def test_missing_nationality_or_dob_never_creates_a_field_row(con):
    recs = [
        wd("Q28967995", "Erling Haaland", "2000-07-21", None),   # nationality unavailable
        wd("Q00000001", "Kylian Mbappe", None, "France"),        # dob unavailable
    ]
    enrich_players_with_wikidata(con, recs)
    by_qid = {}
    for qid, field in con.execute(
        "select source_record_id, field_name from player_field_values where source = 'wikidata'").fetchall():
        by_qid.setdefault(qid, set()).add(field)
    assert "nationality" not in by_qid.get("Q28967995", set())
    assert "date_of_birth" not in by_qid.get("Q00000001", set())


# ═══════════════════════════════════════════════════════════
# idempotency
# ═══════════════════════════════════════════════════════════

def test_rerun_same_dataset_version_is_idempotent(con):
    enrich_players_with_wikidata(con, good_records())
    snapshot = (
        _count(con, "player_wikidata_links"),
        _count(con, "player_field_values", "where source = 'wikidata'"),
        _count(con, "player_images"),
    )
    counts2 = enrich_players_with_wikidata(con, good_records())
    assert counts2["linked"] == 0 and counts2["already_linked"] == 2
    assert snapshot == (
        _count(con, "player_wikidata_links"),
        _count(con, "player_field_values", "where source = 'wikidata'"),
        _count(con, "player_images"),
    )
