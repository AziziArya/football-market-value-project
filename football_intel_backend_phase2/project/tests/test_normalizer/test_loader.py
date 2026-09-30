from datetime import date, datetime, timezone

import duckdb
import pytest

from db.migrate import run_migrations
from ingestion.loader import (
    backfill_player_uid,
    load_appearance,
    load_ea_attributes,
    load_identity_fields,
    load_market_value,
    load_transfer,
)
from ingestion.normalizer import (
    AppearanceRecord,
    EAAttributesRecord,
    IdentityFieldValue,
    MarketValueRecord,
    TransferRecord,
)


@pytest.fixture
def db_con(tmp_path):
    db_path = tmp_path / "test.duckdb"
    run_migrations(db_path)
    con = duckdb.connect(str(db_path))
    yield con
    con.close()


def _now():
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════
# identity fields — append-only, conflicting sources both kept
# ═══════════════════════════════════════════════════════════

def test_load_identity_fields_writes_rows(db_con):
    fields = [
        IdentityFieldValue("ea_fc26", "1", "nationality", "Brazil", _now(), "2025-09-19"),
        IdentityFieldValue("ea_fc26", "1", "club", "Liverpool", _now(), "2025-09-19"),
    ]
    n = load_identity_fields(db_con, fields)
    assert n == 2
    rows = db_con.execute("select field_name, field_value from player_field_values order by field_name").fetchall()
    assert rows == [("club", "Liverpool"), ("nationality", "Brazil")]


def test_load_identity_fields_keeps_conflicting_sources_separate(db_con):
    ea_field = IdentityFieldValue("ea_fc26", "1", "nationality", "Brazil", _now(), "2025-09-19")
    tm_field = IdentityFieldValue("transfermarkt_dataset", "100", "nationality", "Portugal", _now(), "sample_2026-07-06")
    load_identity_fields(db_con, [ea_field])
    load_identity_fields(db_con, [tm_field])

    rows = db_con.execute("select source, field_value from player_field_values order by source").fetchall()
    assert rows == [("ea_fc26", "Brazil"), ("transfermarkt_dataset", "Portugal")]


def test_load_identity_fields_empty_list_is_noop(db_con):
    assert load_identity_fields(db_con, []) == 0


# ═══════════════════════════════════════════════════════════
# ea attributes — upsert by ea_fc26_id, refuses stale overwrite
# ═══════════════════════════════════════════════════════════

def test_load_ea_attributes_inserts_new(db_con):
    rec = EAAttributesRecord(ea_fc26_id=1, overall_rating=90, potential=92, value_eur_ingame=100_000_000, dataset_version="2025-09-19")
    outcome = load_ea_attributes(db_con, rec)
    assert outcome == "inserted"
    row = db_con.execute("select overall_rating, value_eur_ingame from ea_fc26_attributes where ea_fc26_id=1").fetchone()
    assert row == (90, 100_000_000)


def test_load_ea_attributes_updates_when_newer_version(db_con):
    rec_old = EAAttributesRecord(ea_fc26_id=1, overall_rating=90, potential=92, value_eur_ingame=100_000_000, dataset_version="2025-01-01")
    rec_new = EAAttributesRecord(ea_fc26_id=1, overall_rating=91, potential=92, value_eur_ingame=105_000_000, dataset_version="2025-09-19")
    load_ea_attributes(db_con, rec_old)
    outcome = load_ea_attributes(db_con, rec_new)
    assert outcome == "updated"
    row = db_con.execute("select overall_rating from ea_fc26_attributes where ea_fc26_id=1").fetchone()
    assert row == (91,)


def test_load_ea_attributes_refuses_stale_overwrite(db_con):
    rec_new = EAAttributesRecord(ea_fc26_id=1, overall_rating=91, potential=92, value_eur_ingame=105_000_000, dataset_version="2025-09-19")
    rec_old = EAAttributesRecord(ea_fc26_id=1, overall_rating=50, potential=50, value_eur_ingame=1, dataset_version="2020-01-01")
    load_ea_attributes(db_con, rec_new)
    outcome = load_ea_attributes(db_con, rec_old)
    assert outcome == "skipped_not_newer"
    row = db_con.execute("select overall_rating from ea_fc26_attributes where ea_fc26_id=1").fetchone()
    assert row == (91,)  # untouched, not clobbered by the stale re-run


# ═══════════════════════════════════════════════════════════
# market value — append-only, deduped by (player_id_in_source, date, source, version)
# ═══════════════════════════════════════════════════════════

def test_load_market_value_appends(db_con):
    rec = MarketValueRecord("418560:2025-06-01", "418560", 200_000_000, date(2025, 6, 1), "transfermarkt_dataset", "sample_2026-07-06")
    n = load_market_value(db_con, rec)
    assert n == 1
    row = db_con.execute("select player_id_in_source, value_eur from market_value_history").fetchone()
    assert row == ("418560", 200_000_000)


def test_load_market_value_idempotent_on_rerun(db_con):
    rec = MarketValueRecord("418560:2025-06-01", "418560", 200_000_000, date(2025, 6, 1), "transfermarkt_dataset", "sample_2026-07-06")
    load_market_value(db_con, rec)
    n2 = load_market_value(db_con, rec)  # same import run twice
    assert n2 == 0
    count = db_con.execute("select count(*) from market_value_history").fetchone()[0]
    assert count == 1


def test_load_market_value_different_dates_both_kept(db_con):
    rec1 = MarketValueRecord("418560:2024-06-01", "418560", 180_000_000, date(2024, 6, 1), "transfermarkt_dataset", "v1")
    rec2 = MarketValueRecord("418560:2025-06-01", "418560", 200_000_000, date(2025, 6, 1), "transfermarkt_dataset", "v1")
    load_market_value(db_con, rec1)
    load_market_value(db_con, rec2)
    rows = db_con.execute("select value_eur from market_value_history order by valuation_date").fetchall()
    assert rows == [(180_000_000,), (200_000_000,)]


def test_load_market_value_two_different_players_same_date_not_confused(db_con):
    rec1 = MarketValueRecord("A:2025-06-01", "A", 100, date(2025, 6, 1), "transfermarkt_dataset", "v1")
    rec2 = MarketValueRecord("B:2025-06-01", "B", 100, date(2025, 6, 1), "transfermarkt_dataset", "v1")
    load_market_value(db_con, rec1)
    n = load_market_value(db_con, rec2)
    assert n == 1  # not treated as a duplicate just because value+date+source+version match
    count = db_con.execute("select count(*) from market_value_history").fetchone()[0]
    assert count == 2


# ═══════════════════════════════════════════════════════════
# transfers / appearances — append-only
# ═══════════════════════════════════════════════════════════

def test_load_transfer(db_con):
    rec = TransferRecord("581678:2023-06-01:0", "581678", date(2023, 6, 1), "Borussia Dortmund", "Real Madrid", 103_000_000, False, "transfermarkt_dataset", "sample_2026-07-06")
    n = load_transfer(db_con, rec)
    assert n == 1
    row = db_con.execute("select from_club, to_club, fee_eur from transfers").fetchone()
    assert row == ("Borussia Dortmund", "Real Madrid", 103_000_000)


def test_load_appearance(db_con):
    rec = AppearanceRecord("418560:2025-08-16:0", "418560", date(2025, 8, 16), "Premier League", 90, 2, 0, "transfermarkt_dataset", "sample_2026-07-06")
    n = load_appearance(db_con, rec)
    assert n == 1
    row = db_con.execute("select goals, minutes_played from appearances").fetchone()
    assert row == (2, 90)


# ═══════════════════════════════════════════════════════════
# backfill_player_uid — links pre-existing rows once matching has run
# ═══════════════════════════════════════════════════════════

def test_backfill_player_uid_links_ea_attributes(db_con):
    import uuid
    player_uid = str(uuid.uuid4())
    now = _now()
    db_con.execute(
        "insert into players (player_uid, ea_fc26_id, transfermarkt_id, display_name, created_at, updated_at) "
        "values (?, 239085, 418560, 'Erling Haaland', ?, ?)",
        [player_uid, now, now],
    )
    rec = EAAttributesRecord(ea_fc26_id=239085, overall_rating=91, potential=94, value_eur_ingame=200_000_000, dataset_version="2025-09-19")
    load_ea_attributes(db_con, rec)

    row = db_con.execute("select player_uid from ea_fc26_attributes where ea_fc26_id=239085").fetchone()
    assert row[0] is None  # not linked yet — loaded before backfill

    counts = backfill_player_uid(db_con)
    assert counts["ea_fc26_attributes"] == 1

    row = db_con.execute("select player_uid from ea_fc26_attributes where ea_fc26_id=239085").fetchone()
    assert row[0] == player_uid


def test_backfill_player_uid_links_market_value_via_transfermarkt_id(db_con):
    import uuid
    player_uid = str(uuid.uuid4())
    now = _now()
    db_con.execute(
        "insert into players (player_uid, ea_fc26_id, transfermarkt_id, display_name, created_at, updated_at) "
        "values (?, 239085, 418560, 'Erling Haaland', ?, ?)",
        [player_uid, now, now],
    )
    rec = MarketValueRecord("418560:2025-06-01", "418560", 200_000_000, date(2025, 6, 1), "transfermarkt_dataset", "sample_2026-07-06")
    load_market_value(db_con, rec)

    counts = backfill_player_uid(db_con)
    assert counts["market_value_history"] == 1
    row = db_con.execute("select player_uid from market_value_history").fetchone()
    assert row[0] == player_uid


def test_backfill_player_uid_leaves_unmatched_rows_null(db_con):
    rec = MarketValueRecord("999999:2025-06-01", "999999", 1, date(2025, 6, 1), "transfermarkt_dataset", "v1")
    load_market_value(db_con, rec)
    counts = backfill_player_uid(db_con)
    assert counts["market_value_history"] == 0  # no matching player exists — honest NULL, not dropped or guessed
    row = db_con.execute("select player_uid from market_value_history").fetchone()
    assert row[0] is None
    total = db_con.execute("select count(*) from market_value_history").fetchone()[0]
    assert total == 1  # row still exists


# ═══════════════════════════════════════════════════════════
# Phase 2.6 — wikidata image loader (player_uid known at insert time)
# ═══════════════════════════════════════════════════════════

def _seed_player(con, ea_id=239085, tm_id=418560, name="Erling Haaland"):
    import uuid
    player_uid = str(uuid.uuid4())
    now = _now()
    con.execute(
        "insert into players (player_uid, ea_fc26_id, transfermarkt_id, display_name, created_at, updated_at) "
        "values (?, ?, ?, ?, ?, ?)",
        [player_uid, ea_id, tm_id, name, now, now],
    )
    return player_uid


def test_load_player_image_inserts_with_known_player_uid(db_con):
    from ingestion.loader import load_player_image
    from ingestion.normalizer import ImageRecord

    player_uid = _seed_player(db_con)
    record = ImageRecord(source_record_id="Q28967995", image_filename="Haaland.jpg", license=None, attribution="Bryan Berlin", dataset_version="live:2026-09-27")
    outcome = load_player_image(db_con, player_uid, record)
    assert outcome == "inserted"

    row = db_con.execute("select player_uid, image_url, license, attribution, is_primary from player_images").fetchone()
    assert row[0] == player_uid
    assert row[1] == "https://commons.wikimedia.org/wiki/Special:FilePath/Haaland.jpg"
    assert row[2] is None       # license genuinely unavailable — stays None
    assert row[3] == "Bryan Berlin"
    assert row[4] is True       # first image for this player -> primary


def test_load_player_image_skips_when_no_filename(db_con):
    from ingestion.loader import load_player_image
    from ingestion.normalizer import ImageRecord

    player_uid = _seed_player(db_con)
    record = ImageRecord(source_record_id="Q00000001", image_filename=None, license=None, attribution=None)
    outcome = load_player_image(db_con, player_uid, record)
    assert outcome == "skipped_no_image"
    assert db_con.execute("select count(*) from player_images").fetchone()[0] == 0


def test_load_player_image_second_image_not_marked_primary(db_con):
    from ingestion.loader import load_player_image
    from ingestion.normalizer import ImageRecord

    player_uid = _seed_player(db_con)
    load_player_image(db_con, player_uid, ImageRecord("Q1", "first.jpg", None, None))
    load_player_image(db_con, player_uid, ImageRecord("Q1", "second.jpg", None, None))
    rows = db_con.execute("select image_url, is_primary from player_images order by id").fetchall()
    assert rows[0] == ("https://commons.wikimedia.org/wiki/Special:FilePath/first.jpg", True)
    assert rows[1] == ("https://commons.wikimedia.org/wiki/Special:FilePath/second.jpg", False)


def test_load_identity_fields_for_player_sets_player_uid_directly(db_con):
    from ingestion.loader import load_identity_fields_for_player
    from ingestion.normalizer import IdentityFieldValue

    player_uid = _seed_player(db_con)
    fields = [IdentityFieldValue("wikidata", "Q28967995", "nationality", "Norway", _now(), "live:2026-09-27")]
    n = load_identity_fields_for_player(db_con, player_uid, fields)
    assert n == 1
    row = db_con.execute("select player_uid, source, field_value from player_field_values").fetchone()
    assert row == (player_uid, "wikidata", "Norway")
