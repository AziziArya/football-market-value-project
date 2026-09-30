from pathlib import Path

import duckdb
import pytest

from config.settings import PipelineConfig
from scripts.run_ingestion import run_pipeline

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures"
SMALL_EA_CSV = FIXTURES_DIR / "small_ea_fc26.csv"
TM_SAMPLE_DIR = Path(__file__).parents[2] / "data" / "raw" / "transfermarkt_dataset" / "sample_2026-07-06"


def _config(tmp_path, **overrides) -> PipelineConfig:
    base = dict(
        db_path=tmp_path / "pipeline_test.duckdb",
        ea_fc26_csv_path=SMALL_EA_CSV,
        transfermarkt_dump_dir=TM_SAMPLE_DIR,
        report_dir=tmp_path / "reports",
    )
    base.update(overrides)
    return PipelineConfig(**base)


# ═══════════════════════════════════════════════════════════
# full pipeline smoke test
# ═══════════════════════════════════════════════════════════

def test_full_pipeline_smoke(tmp_path):
    config = _config(tmp_path)
    report = run_pipeline(config)

    assert report.success is True
    assert report.provider_errors == {}
    assert report.fetched_per_source["ea_fc26"] == 50
    assert report.fetched_per_source["transfermarkt_dataset"] == 29  # 5 players + 15 valuations + 3 transfers + 6 appearances
    assert report.valid_per_source["ea_fc26"] == 50
    assert report.invalid_per_source["ea_fc26"] == 0

    # matching found our known real players
    assert report.matching_counts.get("MATCHED", 0) == 4
    assert report.matching_counts.get("PROBABLE_MATCH", 0) == 1

    # db actually has rows
    assert report.db_row_counts_after["players"] == 5
    assert report.db_row_counts_after["ea_fc26_attributes"] == 50
    assert report.db_row_counts_after["market_value_history"] == 15
    assert report.db_row_counts_after["injury_data_status"] == 5

    con = duckdb.connect(str(config.db_path))
    haaland = con.execute(
        "select value_eur_ingame from ea_fc26_attributes where ea_fc26_id = 239085"
    ).fetchone()
    assert haaland is not None and haaland[0] > 0
    con.close()


def test_performance_regression_full_real_scale(tmp_path):
    """Phase 2.3 exists because the pre-batching pipeline took ~90-140s on
    the real 16,107-record EA dataset (measured: executemany() in DuckDB's
    Python driver is not vectorized, ~1ms/row). Post-fix, the same run
    takes ~5-6s. This asserts a generous ceiling (30s, ~5x margin over
    observed) as a regression guard — if a future change reintroduces
    per-record round trips, this test catches it."""
    import time
    from config.settings import DEFAULT_EA_FC26_CSV_PATH

    config = _config(tmp_path, ea_fc26_csv_path=DEFAULT_EA_FC26_CSV_PATH)  # full real 16,107 rows
    start = time.perf_counter()
    report = run_pipeline(config)
    elapsed = time.perf_counter() - start

    assert report.success is True
    assert report.fetched_per_source["ea_fc26"] == 16107
    assert elapsed < 30, f"pipeline took {elapsed:.1f}s — regression in batch loading? (was ~5-6s after Phase 2.3 fix)"


def test_report_saved_to_disk(tmp_path):
    config = _config(tmp_path)
    report = run_pipeline(config)
    saved = report.save(config.report_dir)
    assert saved.exists()
    import json
    data = json.loads(saved.read_text())
    assert data["success"] is True


# ═══════════════════════════════════════════════════════════
# failed provider handling
# ═══════════════════════════════════════════════════════════

def test_missing_ea_csv_aborts_cleanly_by_default(tmp_path):
    config = _config(tmp_path, ea_fc26_csv_path=tmp_path / "does_not_exist.csv")
    report = run_pipeline(config)

    assert report.success is False
    assert "ea_fc26" in report.provider_errors
    assert "unavailable" in report.provider_errors["ea_fc26"] or "health_check" in report.provider_errors["ea_fc26"]
    # must not proceed to matching/persistence on a known-bad source
    assert report.matching_counts == {}


def test_missing_transfermarkt_dump_aborts_cleanly(tmp_path):
    config = _config(tmp_path, transfermarkt_dump_dir=tmp_path / "no_such_dump")
    report = run_pipeline(config)
    assert report.success is False
    assert "transfermarkt_dataset" in report.provider_errors


def test_continue_on_error_skips_failed_provider_but_keeps_going(tmp_path):
    config = _config(tmp_path, transfermarkt_dump_dir=tmp_path / "no_such_dump", stop_on_provider_failure=False)
    report = run_pipeline(config)

    # transfermarkt failed and was skipped, but ea_fc26 still loaded
    assert "transfermarkt_dataset" in report.provider_errors
    assert report.fetched_per_source.get("ea_fc26") == 50
    # matching skipped since transfermarkt side is empty — no false "all unmatched" run
    assert report.matching_counts == {}
    # success is still False because a provider genuinely failed
    assert report.success is False


def test_unknown_provider_name_reported_clearly(tmp_path):
    with pytest.raises(ValueError, match="unknown provider"):
        _config(tmp_path, enabled_providers=("not_a_real_provider",))


# ═══════════════════════════════════════════════════════════
# rerun / idempotency
# ═══════════════════════════════════════════════════════════

def test_rerun_is_idempotent_for_non_history_tables(tmp_path):
    config = _config(tmp_path)
    report1 = run_pipeline(config)
    report2 = run_pipeline(config)

    assert report1.success and report2.success

    # these must NOT grow on rerun — same source, same version, deduped
    assert report2.db_row_counts_after["players"] == report1.db_row_counts_after["players"] == 5
    assert report2.db_row_counts_after["ea_fc26_attributes"] == report1.db_row_counts_after["ea_fc26_attributes"] == 50
    assert report2.db_row_counts_after["market_value_history"] == report1.db_row_counts_after["market_value_history"] == 15
    assert report2.db_row_counts_after["transfers"] == report1.db_row_counts_after["transfers"] == 3
    assert report2.db_row_counts_after["appearances"] == report1.db_row_counts_after["appearances"] == 6
    assert report2.db_row_counts_after["injury_data_status"] == report1.db_row_counts_after["injury_data_status"] == 5

    # identity_matches is DELIBERATELY append-only (full audit trail) — it DOES grow.
    # asserting this explicitly so it reads as intended behaviour, not a missed bug.
    assert report2.db_row_counts_after["identity_matches"] == 2 * report1.db_row_counts_after["identity_matches"]

    # player_field_values: EA/transfermarkt rows are append-only by design (they double), while
    # wikidata rows are de-duplicated per dataset_version (Phase 2.6b) so a same-day rerun adds none.
    con = duckdb.connect(str(config.db_path))
    wikidata_rows = con.execute("select count(*) from player_field_values where source = 'wikidata'").fetchone()[0]
    con.close()
    core1 = report1.db_row_counts_after["player_field_values"] - wikidata_rows
    core2 = report2.db_row_counts_after["player_field_values"] - wikidata_rows
    assert core2 == 2 * core1
