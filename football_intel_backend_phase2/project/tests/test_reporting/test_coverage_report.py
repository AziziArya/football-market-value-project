import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pytest

from db.migrate import run_migrations
from reporting.coverage_report import build_coverage_report

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures"
SMALL_EA_CSV = FIXTURES_DIR / "small_ea_fc26.csv"
TM_SAMPLE_DIR = Path(__file__).parents[2] / "data" / "raw" / "transfermarkt_dataset" / "sample_2026-07-06"


@pytest.fixture
def db_con(tmp_path):
    db_path = tmp_path / "test.duckdb"
    run_migrations(db_path)
    con = duckdb.connect(str(db_path))
    yield con
    con.close()


def _now():
    return datetime.now(timezone.utc)


def _seed_two_players_one_incomplete(con):
    """Player A: fully matched, complete fields, has market value, EA value, no ML prediction.
    Player B: EA-only (no transfermarkt link at all — never matched)."""
    uid_a = str(uuid.uuid4())
    now = _now()
    con.execute(
        "insert into players (player_uid, ea_fc26_id, transfermarkt_id, display_name, created_at, updated_at) "
        "values (?, 1, 100, 'Player A', ?, ?)",
        [uid_a, now, now],
    )
    con.execute(
        "insert into ea_fc26_attributes (ea_fc26_id, player_uid, overall_rating, potential, value_eur_ingame, dataset_version) "
        "values (1, ?, 90, 92, 50000000, 'v1')",
        [uid_a],
    )
    con.execute(
        "insert into ea_fc26_attributes (ea_fc26_id, player_uid, overall_rating, potential, value_eur_ingame, dataset_version) "
        "values (2, NULL, 70, 71, 5000000, 'v1')"
    )
    con.execute(
        "insert into market_value_history (id, player_uid, player_id_in_source, value_eur, valuation_date, source, dataset_version, imported_at) "
        "values (1, ?, '100', 60000000, ?, 'transfermarkt_dataset', 'v1', ?)",
        [uid_a, date(2025, 1, 1), now],
    )
    # identity fields: Player A complete on both sources; Player B (ea_fc26_id=2) present but incomplete (no club)
    con.execute(
        "insert into player_field_values (id, player_uid, field_name, field_value, source, source_record_id, fetched_at, dataset_version) values "
        "(1, ?, 'display_name', 'Player A', 'ea_fc26', '1', ?, 'v1'),"
        "(2, ?, 'nationality', 'England', 'ea_fc26', '1', ?, 'v1'),"
        "(3, ?, 'club', 'Club A', 'ea_fc26', '1', ?, 'v1'),"
        "(4, ?, 'date_of_birth', '2000-01-01', 'ea_fc26', '1', ?, 'v1'),"
        "(5, ?, 'position', 'ST', 'ea_fc26', '1', ?, 'v1'),"
        "(6, NULL, 'display_name', 'Player B', 'ea_fc26', '2', ?, 'v1'),"
        "(7, NULL, 'nationality', 'France', 'ea_fc26', '2', ?, 'v1')",
        [uid_a, now, uid_a, now, uid_a, now, uid_a, now, uid_a, now, now, now],
    )
    return uid_a


# ═══════════════════════════════════════════════════════════
# synthetic unit tests
# ═══════════════════════════════════════════════════════════

def test_matching_stats_zero_when_no_matches(db_con):
    report = build_coverage_report(db_con)
    assert report.matching_stats["total"] == 0
    assert report.matching_stats["MATCHED"] == 0


def test_field_completeness_reflects_missing_fields(db_con):
    _seed_two_players_one_incomplete(db_con)
    report = build_coverage_report(db_con)
    ea = report.field_completeness["ea_fc26"]
    assert ea["display_name"]["present"] == 2  # both players have a name
    assert ea["club"]["present"] == 1          # only Player A has a club
    assert ea["club"]["total"] == 2
    assert ea["club"]["pct"] == 50.0


def test_lineage_coverage_computed_correctly(db_con):
    _seed_two_players_one_incomplete(db_con)
    report = build_coverage_report(db_con)
    lineage = report.lineage_coverage["ea_fc26_attributes"]
    assert lineage["total"] == 2
    assert lineage["with_player_uid"] == 1  # only Player A got matched/linked
    assert lineage["pct"] == 50.0


def test_injury_status_distinguishes_states(db_con):
    uid_a = _seed_two_players_one_incomplete(db_con)
    con = db_con
    con.execute(
        "insert into injury_data_status (player_uid, status, checked_at) values (?, 'NO_SOURCE_AVAILABLE', ?)",
        [uid_a, _now()],
    )
    report = build_coverage_report(con)
    assert report.injury_status["NO_SOURCE_AVAILABLE"] == 1
    assert report.injury_status["CONFIRMED_NO_INJURIES"] == 0
    assert report.injury_status["HAS_RECORDS"] == 0
    assert report.injury_status["total_players"] == 1  # only Player A is a canonical player
    assert report.injury_status["players_missing_status_row"] == 0


def test_injury_status_flags_missing_row_as_real_gap(db_con):
    _seed_two_players_one_incomplete(db_con)
    # deliberately do NOT insert an injury_data_status row — simulate the gap
    report = build_coverage_report(db_con)
    assert report.injury_status["players_missing_status_row"] == 1
    assert any("DATA GAP" in line for line in report.missing_data_summary)


def test_market_value_coverage_kept_separate_never_merged(db_con):
    _seed_two_players_one_incomplete(db_con)
    report = build_coverage_report(db_con)
    mv = report.market_value_coverage
    assert mv["transfermarkt_market_value"]["players_with_data"] == 1
    assert mv["ea_ingame_value"]["players_with_data"] == 1
    assert mv["ml_predicted_value"]["players_with_data"] == 0  # no ML predictions exist yet
    assert mv["ml_predicted_value"]["pct"] == 0.0
    # three genuinely independent numbers, not one blended figure
    assert len({mv["transfermarkt_market_value"]["players_with_data"],
                mv["ea_ingame_value"]["players_with_data"],
                mv["ml_predicted_value"]["players_with_data"]}) in (1, 2, 3)


def test_missing_data_summary_never_fabricates_beyond_the_numbers(db_con):
    _seed_two_players_one_incomplete(db_con)
    report = build_coverage_report(db_con)
    for line in report.missing_data_summary:
        assert isinstance(line, str) and len(line) > 0


def test_report_json_roundtrip(tmp_path, db_con):
    _seed_two_players_one_incomplete(db_con)
    report = build_coverage_report(db_con)
    saved = report.save(tmp_path / "report.json")
    import json
    data = json.loads(saved.read_text())
    assert data["market_value_coverage"]["ml_predicted_value"]["players_with_data"] == 0


# ═══════════════════════════════════════════════════════════
# real-data smoke test — actual pipeline, actual known players
# ═══════════════════════════════════════════════════════════

def test_real_data_smoke(tmp_path):
    from config.settings import PipelineConfig
    from scripts.run_ingestion import run_pipeline

    config = PipelineConfig(
        db_path=tmp_path / "real.duckdb",
        ea_fc26_csv_path=SMALL_EA_CSV,
        transfermarkt_dump_dir=TM_SAMPLE_DIR,
        report_dir=tmp_path / "reports",
    )
    pipeline_report = run_pipeline(config)
    assert pipeline_report.success is True

    con = duckdb.connect(str(config.db_path))
    report = build_coverage_report(con)
    con.close()

    assert report.matching_stats["MATCHED"] == 4
    assert report.matching_stats["PROBABLE_MATCH"] == 1
    assert report.matching_stats["total"] == 50

    assert report.source_coverage["ea_fc26"]["total_records"] == 50
    assert report.source_coverage["transfermarkt_dataset"]["total_player_records"] == 5

    # required EA fields are 100% complete on real data (validator enforces this)
    assert report.field_completeness["ea_fc26"]["display_name"]["pct"] == 100.0

    # no ML integration yet — must read as exactly 0%, not omitted or guessed
    assert report.market_value_coverage["ml_predicted_value"]["players_with_data"] == 0
    assert report.market_value_coverage["ml_predicted_value"]["pct"] == 0.0

    # injury: honest NO_SOURCE_AVAILABLE for every canonical (matched) player
    assert report.injury_status["NO_SOURCE_AVAILABLE"] == report.injury_status["total_players"]
    assert report.injury_status["players_missing_status_row"] == 0

    assert any("ml_predicted_value" in line for line in report.missing_data_summary)
    assert any("injury" in line for line in report.missing_data_summary)
