"""G9 - ingestion idempotency invariants (written BEFORE the fix; they fail on the pre-fix code).

Rules under test (owner-approved):
  R1 re-running the SAME dataset into the SAME db creates no new rows in ANY table.
  R2 source tables stay append-only: a CHANGED value (same or new dataset_version) is appended, nothing is deleted.
  R3 `is_current` alone is never the selection criterion; the read-model rule is deterministic:
     latest row per logical key by (fetched_at DESC, id DESC).
  R4 no migration/schema change is needed (asserted: migrations dir unchanged = 6 files).
"""
import shutil
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from config.settings import PipelineConfig
from scripts.run_ingestion import run_pipeline

ROOT = Path(__file__).parents[2]
SMALL_EA = ROOT / "tests" / "fixtures" / "small_ea_fc26.csv"
TM_SAMPLE = ROOT / "data" / "raw" / "transfermarkt_dataset" / "sample_2026-07-06"
TABLES = ["players", "player_field_values", "ea_fc26_attributes", "market_value_history", "transfers", "appearances",
          "injury_data_status", "identity_matches", "review_queue", "player_images", "player_wikidata_links"]

# The ONE read-model selection rule (executable spec; ARCHITECTURE_API.md section 4).
LATEST_FIELD_VALUES_SQL = """
select source, source_record_id, field_name, field_value, dataset_version, fetched_at, id from (
  select *, row_number() over (partition by source, source_record_id, field_name order by fetched_at desc, id desc) rn
  from player_field_values) where rn = 1
"""
LATEST_BEST_MATCH_SQL = """
select ea_fc26_id, transfermarkt_id, match_status, match_confidence, matched_on from (
  select *, row_number() over (partition by ea_fc26_id order by matched_at desc, id desc) rn
  from identity_matches where is_best and ea_fc26_id is not null) where rn = 1
"""


def _cfg(tmp, name="g9.duckdb", ea=SMALL_EA, tm=TM_SAMPLE):
    return PipelineConfig(db_path=tmp / name, ea_fc26_csv_path=ea, transfermarkt_dump_dir=tm, report_dir=tmp / "r")


def _counts(path):
    c = duckdb.connect(str(path), read_only=True)
    try:
        return {t: c.execute(f"select count(*) from {t}").fetchone()[0] for t in TABLES}
    finally:
        c.close()


def _q(path, sql):
    c = duckdb.connect(str(path), read_only=True)
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


# ---------------- R1: same dataset, same DB, no new rows --------------------------------------
def test_R1_second_and_third_run_add_no_rows_to_any_table(tmp_path):
    cfg = _cfg(tmp_path)
    assert run_pipeline(cfg).success
    first = _counts(cfg.db_path)
    assert first["player_field_values"] > 0 and first["identity_matches"] > 0       # the test is not vacuous
    for _ in range(2):
        assert run_pipeline(cfg).success
        assert _counts(cfg.db_path) == first


def test_R1_logical_keys_are_unique_after_reruns(tmp_path):
    cfg = _cfg(tmp_path)
    for _ in range(3):
        assert run_pipeline(cfg).success
    dup_fields = _q(cfg.db_path, "select count(*) from (select 1 from player_field_values "
                    "group by source, source_record_id, field_name, field_value, dataset_version having count(*) > 1)")[0][0]
    assert dup_fields == 0
    # one EA display_name row per EA player
    assert _q(cfg.db_path, "select count(*), count(distinct source_record_id) from player_field_values "
              "where source='ea_fc26' and field_name='display_name'")[0] == (50, 50)
    # exactly one best-match row per EA id after identical reruns
    assert _q(cfg.db_path, "select count(*) from (select 1 from identity_matches where is_best and ea_fc26_id is not null "
              "group by ea_fc26_id having count(*) > 1)")[0][0] == 0


def test_R1_rerun_database_is_logically_identical_to_single_run(tmp_path):
    once, twice = _cfg(tmp_path, "once.duckdb"), _cfg(tmp_path, "twice.duckdb")
    run_pipeline(once); run_pipeline(twice); run_pipeline(twice)
    for sql in (LATEST_FIELD_VALUES_SQL.replace("fetched_at, id", "dataset_version").replace("source, source_record_id, field_name, field_value, dataset_version, dataset_version", "source, source_record_id, field_name, field_value, dataset_version") + " order by 1,2,3",
                LATEST_BEST_MATCH_SQL + " order by 1"):
        assert sorted(map(str, _q(once.db_path, sql))) == sorted(map(str, _q(twice.db_path, sql)))


def test_R1_wikidata_fixture_rerun_on_a_different_day_adds_no_rows(tmp_path, monkeypatch):
    """The fixture's dataset_version must identify its CONTENT, not the run date."""
    import ingestion.providers.wikidata as wd
    cfg = _cfg(tmp_path)

    class Day1(datetime):
        @classmethod
        def now(cls, tz=None): return datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

    class Day2(datetime):
        @classmethod
        def now(cls, tz=None): return datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(wd, "datetime", Day1); assert run_pipeline(cfg).success
    first = _counts(cfg.db_path)
    monkeypatch.setattr(wd, "datetime", Day2); assert run_pipeline(cfg).success
    assert _counts(cfg.db_path) == first
    versions = {r[0] for r in _q(cfg.db_path, "select distinct dataset_version from player_field_values where source='wikidata'")}
    assert len(versions) == 1 and next(iter(versions)).startswith("fixture:")      # still never 'live:'


# ---------------- R2: changes are appended, nothing deleted ------------------------------------
def _modified_ea_csv(tmp_path, club="Test FC"):
    df = pd.read_csv(SMALL_EA)
    club_col = "team"          # ingestion/normalizer.py maps EA payload key 'team' -> field 'club'
    df.loc[df.index[0], club_col] = club
    p = tmp_path / "ea_modified.csv"; df.to_csv(p, index=False)
    return p, df.iloc[0], club_col


def test_R2_changed_value_same_dataset_version_is_appended_and_latest_wins(tmp_path):
    cfg = _cfg(tmp_path)
    assert run_pipeline(cfg).success
    before = _counts(cfg.db_path)
    mod, row, _ = _modified_ea_csv(tmp_path)
    cfg2 = _cfg(tmp_path, ea=mod); assert run_pipeline(cfg2).success
    after = _counts(cfg.db_path)
    assert after["player_field_values"] == before["player_field_values"] + 1       # exactly the changed field, appended
    ea_id = str(int(row["id"]))
    rows = _q(cfg.db_path, f"select field_value from player_field_values where source='ea_fc26' and source_record_id='{ea_id}' and field_name='club' order by fetched_at, id")
    assert len(rows) == 2 and rows[-1][0] == "Test FC"                              # old row kept (append-only)
    latest = _q(cfg.db_path, f"select field_value from ({LATEST_FIELD_VALUES_SQL}) where source='ea_fc26' and source_record_id='{ea_id}' and field_name='club'")
    assert latest == [("Test FC",)]                                                  # deterministic latest-wins


def test_R3_is_current_is_not_the_selection_criterion(tmp_path):
    cfg = _cfg(tmp_path)
    run_pipeline(cfg)
    mod, row, _ = _modified_ea_csv(tmp_path); run_pipeline(_cfg(tmp_path, ea=mod))
    # both the superseded and the new row are flagged current -> is_current cannot pick the valid one
    cur = _q(cfg.db_path, "select count(*) from (select 1 from player_field_values where is_current group by source, source_record_id, field_name having count(*) > 1)")[0][0]
    assert cur == 1
    # the read-model rule still yields exactly one row per logical key
    n_keys = _q(cfg.db_path, "select count(*) from (select 1 from player_field_values group by source, source_record_id, field_name)")[0][0]
    assert _q(cfg.db_path, f"select count(*) from ({LATEST_FIELD_VALUES_SQL})")[0][0] == n_keys


def test_R2_changed_match_outcome_appends_a_new_group_and_latest_group_wins(tmp_path):
    """Same EA data but a different transfermarkt dataset version/content => matching is a NEW result: history kept."""
    cfg = _cfg(tmp_path)
    assert run_pipeline(cfg).success
    base = _counts(cfg.db_path)["identity_matches"]
    tm2 = tmp_path / "tm2"; shutil.copytree(TM_SAMPLE, tm2)
    players = tm2 / "players.csv"
    df = pd.read_csv(players)
    df.loc[df.index[0], "name"] = "Zzz Changed Name"; df.to_csv(players, index=False)
    cfg2 = _cfg(tmp_path, tm=tm2); cfg2.db_path = cfg.db_path
    assert run_pipeline(cfg2).success
    c = _counts(cfg.db_path)["identity_matches"]
    assert c > base                                                                  # new outcome appended, old rows kept
    assert _q(cfg.db_path, f"select count(*) from ({LATEST_BEST_MATCH_SQL})")[0][0] == 50   # still one per EA id


# ---------------- R4: no migration needed -------------------------------------------------------
def test_R4_no_new_migration_for_g9():
    assert len(list((ROOT / "db" / "migrations").glob("*.sql"))) == 6


# ---------------- legacy databases (built before the fix) -----------------------------------------
def test_R5_legacy_duplicates_are_not_deleted_not_grown_and_read_model_collapses_them(tmp_path):
    """A DB that already holds pre-fix duplicates: a rerun adds nothing, deletes nothing; the read-model rule
    still yields exactly one row per logical key, and the choice is deterministic."""
    cfg = _cfg(tmp_path)
    assert run_pipeline(cfg).success
    c = duckdb.connect(str(cfg.db_path))
    nid = c.execute("select max(id) from player_field_values").fetchone()[0]
    # simulate the old behaviour: a full second copy of the EA field rows, with a later fetched_at
    c.execute(f"insert into player_field_values select id + {nid}, player_uid, field_name, field_value, source, source_record_id, "
              "fetched_at + interval 1 hour, dataset_version, is_current, confidence from player_field_values where source = 'ea_fc26'")
    c.close()
    legacy = _counts(cfg.db_path)
    assert legacy["player_field_values"] > 337
    assert run_pipeline(cfg).success
    assert _counts(cfg.db_path) == legacy                                              # no growth, no deletion
    n_keys = _q(cfg.db_path, "select count(*) from (select 1 from player_field_values group by source, source_record_id, field_name)")[0][0]
    assert _q(cfg.db_path, f"select count(*) from ({LATEST_FIELD_VALUES_SQL})")[0][0] == n_keys
    first = _q(cfg.db_path, f"select * from ({LATEST_FIELD_VALUES_SQL}) order by 1,2,3")
    assert first == _q(cfg.db_path, f"select * from ({LATEST_FIELD_VALUES_SQL}) order by 1,2,3")   # deterministic
