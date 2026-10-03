"""Step 3.1 - identity/provenance contract checked against the REAL schema and a real
(fixture-sized) pipeline run. The contract is a document; this test stops it from
drifting from the database, and pins today's known gaps so that fixing one forces a
contract update instead of silently diverging."""
import json
import re
from pathlib import Path

import duckdb
import pytest

from config.settings import PipelineConfig
from scripts.run_ingestion import run_pipeline

ROOT = Path(__file__).parents[2]
CONTRACT = ROOT / "api_contract" / "openapi.json"
SMALL_EA_CSV = ROOT / "tests" / "fixtures" / "small_ea_fc26.csv"
TM_SAMPLE_DIR = ROOT / "data" / "raw" / "transfermarkt_dataset" / "sample_2026-07-06"
MIGRATIONS = ROOT / "db" / "migrations"

FORBIDDEN_PROPERTY_NAMES = {"value", "value_eur", "market_value", "predicted_value", "price"}


@pytest.fixture(scope="module")
def contract():
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    d = tmp_path_factory.mktemp("contract")
    cfg = PipelineConfig(db_path=d / "c.duckdb", ea_fc26_csv_path=SMALL_EA_CSV,
                         transfermarkt_dump_dir=TM_SAMPLE_DIR, report_dir=d / "r")
    assert run_pipeline(cfg).success
    c = duckdb.connect(str(cfg.db_path), read_only=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def con_full(tmp_path_factory):
    """Full real 16,107-player EA run (~5s) - needed for the real AMBIGUOUS case and real counts."""
    d = tmp_path_factory.mktemp("contract_full")
    cfg = PipelineConfig(db_path=d / "f.duckdb", transfermarkt_dump_dir=TM_SAMPLE_DIR, report_dir=d / "r")
    assert run_pipeline(cfg).success
    c = duckdb.connect(str(cfg.db_path), read_only=True)
    yield c
    c.close()


def _walk_props(schemas):
    for sname, s in schemas.items():
        stack = [s]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for pname, p in node.get("properties", {}).items():
                    yield sname, pname, p
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
            elif isinstance(node, list):
                stack.extend(node)


def _check_enum(migration_glob, table_hint):
    text = "".join(p.read_text() for p in sorted(MIGRATIONS.glob(migration_glob)))
    m = re.search(table_hint + r".*?IN \(([^)]*)\)", text, re.S)
    return [x.strip().strip("'") for x in m.group(1).split(",")]


def test_contract_version_is_draft(contract):
    assert contract["info"]["version"].endswith("-draft")


def test_no_generic_value_property(contract):
    names = {p for _, p, _ in _walk_props(contract["components"]["schemas"])}
    assert not (names & FORBIDDEN_PROPERTY_NAMES)
    vals = contract["components"]["schemas"]["Values"]["properties"]
    assert set(vals) == {"ea_ingame_value", "source_market_value", "model_estimate"}


def test_every_leaf_property_declares_source_or_derivation(contract):
    """Leaf properties (no $ref / object / array-of-refs / oneOf) must say where they come from."""
    missing = []
    for s_, n, p in _walk_props(contract["components"]["schemas"]):
        structural = ("$ref" in p or "oneOf" in p or p.get("type") == "object"
                      or (p.get("type") == "array" and "$ref" in p.get("items", {})))
        if not structural and "x-source" not in p and "x-derived" not in p:
            missing.append(f"{s_}.{n}")
    assert missing == []


def test_x_source_columns_exist_in_migrated_schema(contract, con):
    cols = {(t, c) for t, c in con.execute(
        "select table_name, column_name from information_schema.columns").fetchall()}
    bad = []
    for s, n, p in _walk_props(contract["components"]["schemas"]):
        xs = p.get("x-source")
        if not xs:
            continue
        if "column" in xs and (xs["table"], xs["column"]) not in cols:
            bad.append(f"{s}.{n} -> {xs['table']}.{xs['column']}")
        if "field_name" in xs:
            n_rows = con.execute(
                "select count(*) from player_field_values where field_name=? and source=?",
                [xs["field_name"], xs["source"]]).fetchone()[0]
            if n_rows == 0:
                bad.append(f"{s}.{n} -> no player_field_values({xs['field_name']},{xs['source']}) rows")
    assert bad == []


def test_enums_match_database_check_constraints(contract):
    sch = contract["components"]["schemas"]
    assert sch["MatchStatus"]["enum"][:4] == _check_enum("0001_init.sql", r"match_status\s+VARCHAR NOT NULL CHECK")
    assert sch["InjuryStatus"]["enum"][:3] == _check_enum("0001_init.sql", r"status\s+VARCHAR NOT NULL CHECK")
    assert set(sch["MatchStatus"]["enum"]) - set(sch["MatchStatus"]["enum"][:4]) == {"NOT_EVALUATED"}
    assert set(sch["InjuryStatus"]["enum"]) - set(sch["InjuryStatus"]["enum"][:3]) == {"NOT_EVALUATED"}


def test_every_ea_player_is_servable_without_a_canonical_row(con):
    """Decision D1: all EA players are addressable; core fields come from
    player_field_values(source=ea_fc26) keyed by source_record_id == ea_fc26_id."""
    ea_ids = {r[0] for r in con.execute("select ea_fc26_id from ea_fc26_attributes").fetchall()}
    assert len(ea_ids) == 50
    for field in ("display_name", "position", "club", "nationality", "date_of_birth"):
        rows = con.execute(
            "select source_record_id, count(*) from player_field_values "
            "where source='ea_fc26' and field_name=? group by 1", [field]).fetchall()
        assert {int(r[0]) for r in rows} == ea_ids, field
        assert all(r[1] == 1 for r in rows), f"{field}: duplicate rows would duplicate API entities"


def test_ea_only_players_have_no_inferred_identity(con):
    ea_only = {r[0] for r in con.execute(
        "select ea_fc26_id from ea_fc26_attributes where player_uid is null").fetchall()}
    canonical = {r[0] for r in con.execute("select ea_fc26_id from v_players").fetchall()}
    assert len(ea_only) == 45 and len(canonical) == 5 and not (ea_only & canonical)
    # no TM/Wikidata/injury/image/market-value row may hang off an EA-only player
    for tbl in ("market_value_history", "injury_data_status", "player_images"):
        n = con.execute(f"select count(*) from {tbl} where player_uid is not null and player_uid not in "
                        "(select player_uid from v_players)").fetchone()[0]
        assert n == 0, tbl
    n = con.execute("select count(*) from injury_data_status").fetchone()[0]
    assert n == 5  # -> EA_ONLY players get InjuryStatus NOT_EVALUATED, derived by absence


def test_ambiguous_candidate_ids_exist_but_must_not_be_exposed(con_full):
    con = con_full
    row = con.execute(
        "select m.ea_fc26_id, m.transfermarkt_id from identity_matches m "
        "where m.is_best and m.match_status='AMBIGUOUS'").fetchall()
    assert row, "full real run contains the real AMBIGUOUS case (EA 233097)"
    for ea_id, tm_id in row:
        assert tm_id is not None  # the DB knows a candidate...
        linked = con.execute("select count(*) from v_players where ea_fc26_id=?", [ea_id]).fetchone()[0]
        assert linked == 0        # ...but the player is EA_ONLY, so SourceIds.transfermarkt_id must be null


def test_images_are_wikidata_only(con):
    srcs = {r[0] for r in con.execute("select distinct source from player_images").fetchall()}
    assert srcs <= {"wikidata"}


def test_not_evaluated_is_needed_for_wikidata_link_state(con):
    """Enrichment iterates canonical players only: EA-only players were never compared to Wikidata."""
    n = con.execute("select count(*) from identity_matches where wikidata_id is not null "
                    "and ea_fc26_id in (select ea_fc26_id from ea_fc26_attributes where player_uid is null)").fetchone()[0]
    assert n == 0


# ---- known gaps, pinned so a fix forces a contract update ----------------

def test_known_gap_G1_raw_json_is_never_populated(con):
    """G1: ea_fc26_attributes.raw_json is NULL for every row, so detailed EA attributes
    (pace, finishing...) are NOT available from the DB. Step 3.2 must either scope the
    API to overall/potential/value, or a separate backend step must populate it."""
    total, filled = con.execute("select count(*), count(raw_json) from ea_fc26_attributes").fetchone()
    assert total == 50 and filled == 0


def test_G2_fixed_wikidata_fixture_is_tagged_fixture_not_live(con):
    """G2 (fixed in Step 3.2 prelude): fixture reads are stamped 'fixture:<date>'. The contract still derives
    data_origin from a source registry, but no 'live:' label may come from a fixture run."""
    v = con.execute("select distinct dataset_version from player_field_values where source='wikidata'").fetchall()
    assert v and all(x[0].startswith("fixture:") for x in v)


def test_full_scale_ea_universe_is_exposable(con_full):
    """Decision D1 at real scale: 16,107 EA players, 5 canonical, 16,102 EA-only."""
    total, only = con_full.execute(
        "select count(*), count(*) filter (where player_uid is null) from ea_fc26_attributes").fetchone()
    assert (total, only) == (16107, 16102)
    dup_names = con_full.execute(
        "select count(*) from (select lower(field_value) n from player_field_values "
        "where source='ea_fc26' and field_name='display_name' group by 1 having count(*)>1)").fetchone()[0]
    assert dup_names == 124  # names are NOT identifiers; search results must disambiguate
