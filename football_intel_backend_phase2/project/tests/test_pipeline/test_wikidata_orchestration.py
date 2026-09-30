import json
import logging
from pathlib import Path

import duckdb
import pytest

import matching.wikidata_enrichment as enrichment_module
import scripts.run_ingestion as run_ingestion
from config.settings import PipelineConfig
from ingestion.providers.wikidata import WikidataProvider
from scripts.run_ingestion import run_pipeline

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures"
SMALL_EA_CSV = FIXTURES_DIR / "small_ea_fc26.csv"
TM_SAMPLE_DIR = Path(__file__).parents[2] / "data" / "raw" / "transfermarkt_dataset" / "sample_2026-07-06"
CORE = ("ea_fc26", "transfermarkt_dataset")
CORE_TABLES = ("players", "ea_fc26_attributes", "market_value_history", "transfers", "appearances",
               "identity_matches", "injury_data_status")


def _config(tmp_path, name="run", **overrides):
    base = dict(
        db_path=tmp_path / f"{name}.duckdb", ea_fc26_csv_path=SMALL_EA_CSV,
        transfermarkt_dump_dir=TM_SAMPLE_DIR, report_dir=tmp_path / "reports",
    )
    base.update(overrides)
    return PipelineConfig(**base)


def _core_counts(report):
    return {t: report.db_row_counts_after[t] for t in CORE_TABLES}


def _count(config, table, where=""):
    con = duckdb.connect(str(config.db_path))
    try:
        return con.execute(f"select count(*) from {table} {where}").fetchone()[0]
    finally:
        con.close()


@pytest.fixture
def control_core_counts(tmp_path):
    """Core row counts of a clean run WITHOUT wikidata — the yardstick that proves a
    wikidata failure changed nothing in the EA/transfermarkt data."""
    report = run_pipeline(_config(tmp_path, "control", enabled_providers=CORE))
    assert report.success
    return _core_counts(report)


# ═══════════════════════════════════════════════════════════
# ordering: wikidata only after matching + players exist
# ═══════════════════════════════════════════════════════════

def test_enrichment_runs_only_after_matching_and_players_exist(tmp_path, monkeypatch):
    config = _config(tmp_path)
    observed = {}
    real = run_ingestion._run_wikidata_enrichment

    def spy(cfg, con, report):
        observed["players"] = con.execute("select count(*) from players").fetchone()[0]
        observed["identity_matches"] = con.execute("select count(*) from identity_matches").fetchone()[0]
        observed["review_or_backfilled"] = con.execute(
            "select count(*) from ea_fc26_attributes where player_uid is not null").fetchone()[0]
        return real(cfg, con, report)

    monkeypatch.setattr(run_ingestion, "_run_wikidata_enrichment", spy)
    report = run_pipeline(config)
    assert report.success
    assert observed["players"] == 5
    assert observed["identity_matches"] > 0
    assert observed["review_or_backfilled"] == 5  # backfill already done


def test_happy_path_links_and_reports(tmp_path):
    config = _config(tmp_path)
    report = run_pipeline(config)
    assert report.success is True
    assert report.enrichment_errors == {}
    assert report.enrichment_counts["linked"] == 4
    assert report.enrichment_counts["no_confident_match"] == 1  # Vini Jr. (nickname) -> AMBIGUOUS -> nothing stored
    assert report.fetched_per_source["wikidata"] == 5
    assert report.db_row_counts_after["player_wikidata_links"] == 4
    assert report.db_row_counts_after["player_images"] == 1


def test_wikidata_not_run_when_not_enabled(tmp_path):
    config = _config(tmp_path, enabled_providers=CORE)
    report = run_pipeline(config)
    assert report.success
    assert report.enrichment_counts == {} and report.enrichment_errors == {}
    assert "wikidata" not in report.fetched_per_source
    assert _count(config, "player_wikidata_links") == 0


# ═══════════════════════════════════════════════════════════
# non-blocking failure modes
# ═══════════════════════════════════════════════════════════

def test_missing_fixture_is_nonblocking_and_reported(tmp_path, control_core_counts, caplog):
    caplog.set_level(logging.WARNING)
    config = _config(tmp_path, wikidata_fixture_path=tmp_path / "nope.csv")
    report = run_pipeline(config)

    assert report.success is True                       # core pipeline still succeeds
    assert report.provider_errors == {}                  # not treated as a core provider failure
    assert "wikidata" in report.enrichment_errors        # but clearly recorded
    assert "health_check" in report.enrichment_errors["wikidata"]
    assert _core_counts(report) == control_core_counts   # EA/TM data completely intact
    assert any("wikidata" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


def test_fetch_exception_is_nonblocking_and_reported(tmp_path, control_core_counts, monkeypatch):
    def boom(self, **kwargs):
        raise RuntimeError("wikidata endpoint exploded")

    monkeypatch.setattr(WikidataProvider, "fetch", boom)
    report = run_pipeline(_config(tmp_path))

    assert report.success is True
    assert report.provider_errors == {}
    assert "wikidata endpoint exploded" in report.enrichment_errors["wikidata"]
    assert _core_counts(report) == control_core_counts


def test_mid_enrichment_failure_rolls_back_only_the_enrichment(tmp_path, control_core_counts, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("image loader failed")

    monkeypatch.setattr(enrichment_module, "load_player_image", boom)
    config = _config(tmp_path)
    report = run_pipeline(config)

    assert report.success is True
    assert "image loader failed" in report.enrichment_errors["wikidata"]
    # every partial enrichment write was rolled back...
    assert _count(config, "player_wikidata_links") == 0
    assert _count(config, "player_field_values", "where source = 'wikidata'") == 0
    assert _count(config, "player_images") == 0
    # ...while the already-committed core data is untouched
    assert _core_counts(report) == control_core_counts
    assert report.enrichment_counts == {}


def test_enrichment_failure_never_flips_success_or_touches_core_providers_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(WikidataProvider, "health_check", lambda self: False)
    report = run_pipeline(_config(tmp_path))
    assert report.success is True
    assert report.provider_errors == {}
    assert list(report.enrichment_errors) == ["wikidata"]


def test_core_failure_still_aborts_before_any_enrichment(tmp_path):
    config = _config(tmp_path, ea_fc26_csv_path=tmp_path / "missing.csv")
    report = run_pipeline(config)
    assert report.success is False
    assert "ea_fc26" in report.provider_errors
    assert report.enrichment_counts == {} and report.enrichment_errors == {}
    assert "wikidata" not in report.fetched_per_source


def test_enrichment_errors_are_saved_in_the_json_report(tmp_path):
    config = _config(tmp_path, wikidata_fixture_path=tmp_path / "nope.csv")
    report = run_pipeline(config)
    saved = json.loads(report.save(config.report_dir).read_text())
    assert "wikidata" in saved["enrichment_errors"]
    assert saved["success"] is True


# ═══════════════════════════════════════════════════════════
# no fabrication through the whole pipeline + idempotent reruns
# ═══════════════════════════════════════════════════════════

def test_header_only_wikidata_file_links_nothing(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("qid,name,date_of_birth,country_of_citizenship,image_filename,image_license,image_attribution\n")
    config = _config(tmp_path, wikidata_fixture_path=empty)
    report = run_pipeline(config)
    assert report.success is True
    assert report.enrichment_counts["linked"] == 0
    assert _count(config, "player_wikidata_links") == 0
    assert _count(config, "player_field_values", "where source = 'wikidata'") == 0
    assert _count(config, "player_images") == 0


def test_pipeline_rerun_does_not_duplicate_wikidata_data(tmp_path):
    config = _config(tmp_path)
    run_pipeline(config)
    first = (_count(config, "player_wikidata_links"),
             _count(config, "player_field_values", "where source = 'wikidata'"),
             _count(config, "player_images"))
    report2 = run_pipeline(config)
    assert report2.success
    assert report2.enrichment_counts["linked"] == 0 and report2.enrichment_counts["already_linked"] == 4
    assert first == (4, 12, 1)
    assert first == (_count(config, "player_wikidata_links"),
                     _count(config, "player_field_values", "where source = 'wikidata'"),
                     _count(config, "player_images"))
