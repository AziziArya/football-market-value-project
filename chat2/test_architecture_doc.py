"""Step 3.3 - validates ARCHITECTURE_API.md against the contract and pins the facts its decisions rest on.
Documentation tests only: no API exists and none is imported."""
import ast
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import duckdb
import pytest

from config.settings import PipelineConfig
from scripts.run_ingestion import run_pipeline

ROOT = Path(__file__).parents[2]
DOC = (ROOT / "ARCHITECTURE_API.md").read_text(encoding="utf-8")
SPEC = json.loads((ROOT / "api_contract" / "openapi.json").read_text(encoding="utf-8"))
SMALL_EA = ROOT / "tests" / "fixtures" / "small_ea_fc26.csv"


def test_required_sections_present():
    for h in ["## 0. Scope", "## 2. Framework", "## 3. Layers", "## 4. Read-only", "## 5. SQL read models", "## 6. Search design",
              "## 7. Service rules", "## 8. Error handling", "## 9. Configuration", "## 10. Test strategy", "## 11. Decisions on G1 and ML",
              "## 12. API and frontend boundary", "## 14. Known gaps register", "## 15. Proposed next step"]:
        assert h in DOC, h


def test_doc_introduces_no_endpoint_outside_the_contract():
    contract_paths = {re.sub(r"\{[^}]+\}", "*", p) for p in SPEC["paths"]}
    for m in re.findall(r"`(/(?:players|health|data-freshness)[^`\s]*)`", DOC):
        assert re.sub(r"\{[^}]+\}", "*", m) in contract_paths, m
    assert "getAdmin" not in DOC and "/admin" in DOC          # admin is mentioned only as deferred
    assert re.search(r"Out of scope.*`/admin/\*`", DOC.replace("\n", " "))


def test_doc_states_the_non_negotiables():
    for s in ["read_only=True", "EA_INGAME_VALUE", "NOT_YET_INTEGRATED", "ea:<id>", "is_live", "SAMPLE_FIXTURE", "fails closed",
              "Build-then-swap", "contract wins", "no arithmetic between them"]:
        assert s.lower() in DOC.lower(), s
    assert "NOT installed" in DOC


def test_gap_register_g1_to_g10():
    rows = dict(re.findall(r"^\| (G\d+) \| ([A-Z]+(?: \([^)]*\))?) \|", DOC, re.M))
    assert set(rows) == {f"G{i}" for i in range(1, 11)}
    assert rows["G1"].startswith("OPEN") and rows["G2"] == "FIXED"
    assert all(rows[f"G{i}"].startswith("KNOWN") for i in range(3, 9))
    assert rows["G9"].startswith("NEW") and rows["G10"].startswith("NEW")


def test_requirements_split():
    dev = (ROOT / "requirements-dev.txt").read_text()
    prod = (ROOT / "requirements.txt").read_text()
    assert "jsonschema==" in dev and "-r requirements.txt" in dev
    assert "jsonschema" not in prod                       # production requirements untouched
    for fw in ("fastapi", "uvicorn", "pydantic", "flask"):
        assert fw not in dev.lower() and fw not in prod.lower()   # nothing installed/declared for the API yet


def test_g1_g8_state_unchanged_in_the_database(tmp_path):
    """The decisions in section 11 assume today's state: raw_json NULL, ML columns NULL."""
    cfg = PipelineConfig(db_path=tmp_path / "d.duckdb", ea_fc26_csv_path=SMALL_EA, report_dir=tmp_path / "r")
    assert run_pipeline(cfg).success
    c = duckdb.connect(str(cfg.db_path), read_only=True)
    total, raw, pred, ver = c.execute(
        "select count(*), count(raw_json), count(predicted_value_eur), count(model_version) from ea_fc26_attributes").fetchone()
    assert (total, raw, pred, ver) == (50, 0, 0, 0)
    c.close()


def test_G9_rerun_into_same_db_duplicates_ea_rows(tmp_path):
    """Pinned: if ingestion becomes idempotent this fails and ARCHITECTURE_API.md (AD-2, G9) must be updated."""
    cfg = PipelineConfig(db_path=tmp_path / "d.duckdb", ea_fc26_csv_path=SMALL_EA, report_dir=tmp_path / "r")
    assert run_pipeline(cfg).success and run_pipeline(cfg).success
    c = duckdb.connect(str(cfg.db_path), read_only=True)
    rows, ids = c.execute("select count(*), count(distinct source_record_id) from player_field_values "
                          "where source='ea_fc26' and field_name='display_name'").fetchone()
    assert (rows, ids) == (100, 50)
    assert c.execute("select count(*) from player_field_values where source='ea_fc26' and not is_current").fetchone()[0] == 0
    best = c.execute("select count(*) from (select ea_fc26_id from identity_matches where is_best and ea_fc26_id is not null "
                     "group by 1 having count(*) = 2)").fetchone()[0]
    assert best == 50
    attrs = c.execute("select count(*) from ea_fc26_attributes").fetchone()[0]
    assert attrs == 50                                        # attributes stay unique
    c.close()


def test_AD1_duckdb_single_writer_semantics(tmp_path):
    """Pinned: many read-only processes are fine; a writer is locked out while a reader is open (basis of AD-2)."""
    db = tmp_path / "t.duckdb"
    con = duckdb.connect(str(db)); con.execute("create table t(a int)"); con.execute("insert into t values (1)"); con.close()
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import duckdb, time
        c = duckdb.connect(r'{db}', read_only=True); print('ready', flush=True); time.sleep(8)""")],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "ready"
        ro = subprocess.run([sys.executable, "-c", f"import duckdb;print(duckdb.connect(r'{db}', read_only=True).execute('select count(*) from t').fetchone()[0])"],
                            capture_output=True, text=True)
        assert ro.stdout.strip() == "1"
        rw = subprocess.run([sys.executable, "-c", f"import duckdb;duckdb.connect(r'{db}')"], capture_output=True, text=True)
        assert rw.returncode != 0 and "lock" in rw.stderr.lower()
    finally:
        holder.kill(); holder.wait()


# ---- architecture fitness: applies the moment an api/ package exists --------------------------
FORBIDDEN_ANYWHERE = {"ingestion", "matching", "scripts", "reporting", "pandas", "tensorflow", "keras", "catboost", "sklearn"}
LAYER_RULES = {
    "routers": {"duckdb", "api.repositories", "api.readmodels"},
    "services": {"duckdb", "fastapi", "api.routers"},
    "repositories": {"fastapi", "api.schemas", "api.services", "api.routers"},
    "domain": {"duckdb", "fastapi", "pydantic", "api.schemas", "api.services", "api.repositories", "api.routers", "api.readmodels"},
}


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_api_package_respects_layer_boundaries_if_present():
    api = ROOT / "api"
    if not api.exists():
        assert not list(ROOT.glob("api*.py"))              # nothing implemented yet (Step 3.3)
        return
    for f in api.rglob("*.py"):
        layer = f.relative_to(api).parts[0]
        for imp in _imports(f):
            assert imp.split(".")[0] not in FORBIDDEN_ANYWHERE, f"{f}: {imp}"
            for bad in LAYER_RULES.get(layer, set()):
                assert not (imp == bad or imp.startswith(bad + ".")), f"{f}: layer {layer} imports {imp}"
        if layer != "repositories" and layer != "readmodels":
            src = f.read_text(encoding="utf-8").lower()
            assert not re.search(r"\b(select|insert|update|delete)\b\s+[\w*(].*\b(from|into|set)\b", src), f"SQL outside repositories: {f}"
