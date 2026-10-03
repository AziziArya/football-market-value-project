"""HTTP behaviour of the two Step 4.1 endpoints, validated against the contract schemas."""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from api import CONTRACT_VERSION
from tests.test_api.conftest import build_db

HEALTH, FRESH = "/api/v1/health", "/api/v1/data-freshness"


def _by_role(body):
    return {s["role"]: s for s in body["sources"]}


# ---------------- health ----------------
def test_health_ok_conforms_to_contract(small_db, make_client, validate, contract):
    r = make_client(small_db).get(HEALTH)
    assert r.status_code == 200 and r.headers["content-type"] == "application/json"
    validate("HealthResponse", r.json())
    assert r.json() == {"status": "ok", "database_reachable": True, "contract_version": contract["info"]["version"]}


def test_health_turns_unavailable_if_the_database_dies_after_startup(small_db, make_client):
    c = make_client(small_db)
    assert c.get(HEALTH).status_code == 200
    c.app.state.services.db.close()                                   # simulate losing the database at runtime
    r = c.get(HEALTH)
    assert r.status_code == 503 and r.json()["code"] == "DATA_UNAVAILABLE"
    assert c.get(FRESH).status_code == 503


# ---------------- data freshness ----------------
def test_freshness_small_db(small_db, make_client, validate):
    r = make_client(small_db).get(FRESH)
    assert r.status_code == 200
    body = r.json(); validate("DataFreshnessResponse", body)
    roles = _by_role(body)
    assert list(roles) == ["PLAYER_ATTRIBUTES", "MARKET_VALUE", "ENRICHMENT", "INJURY", "MODEL"]
    ea, mv, wd = roles["PLAYER_ATTRIBUTES"], roles["MARKET_VALUE"], roles["ENRICHMENT"]
    assert (ea["source"], ea["data_origin"], ea["dataset_version"], ea["record_count"]) == ("ea_fc26", "REAL_FULL", "2025-09-19", 50)
    assert (mv["source"], mv["data_origin"], mv["dataset_version"], mv["record_count"]) == ("transfermarkt_dataset", "SAMPLE_FIXTURE", "sample_2026-07-06", 5)
    assert wd["data_origin"] == "SAMPLE_FIXTURE" and wd["dataset_version"].startswith("fixture:")
    assert all(s["is_live"] is False for s in body["sources"])
    assert "live:" not in r.text
    assert body["meta"]["contains_sample_data"] is True and body["meta"]["contract_version"] == CONTRACT_VERSION


def test_freshness_full_scale(full_db, make_client, validate):
    body = make_client(full_db).get(FRESH).json(); validate("DataFreshnessResponse", body)
    assert _by_role(body)["PLAYER_ATTRIBUTES"]["record_count"] == 16107


def test_freshness_reports_capabilities_that_do_not_exist_yet(small_db, make_client):
    roles = _by_role(make_client(small_db).get(FRESH).json())
    injury, model = roles["INJURY"], roles["MODEL"]
    assert injury == {"role": "INJURY", "source": None, "availability": "NO_SOURCE_DATA", "data_origin": None, "dataset_version": None,
                      "last_fetched_at": None, "record_count": None, "is_live": False}
    assert model["availability"] == "NOT_YET_INTEGRATED" and model["source"] is None and model["record_count"] is None


def test_freshness_timestamps_are_utc_instants_not_in_the_future(small_db, make_client):
    body = make_client(small_db).get(FRESH).json()
    now = datetime.now(timezone.utc)
    for s in body["sources"]:
        if s["last_fetched_at"]:
            ts = datetime.fromisoformat(s["last_fetched_at"].replace("Z", "+00:00"))
            assert ts.tzinfo is not None and ts <= now + timedelta(seconds=2)
    assert abs(now - datetime.fromisoformat(body["meta"]["generated_at"].replace("Z", "+00:00"))) < timedelta(minutes=1)


def test_freshness_is_honest_when_sources_are_absent(tmp_path, make_client, validate):
    """Only EA loaded: no market-value source, no enrichment, no matching. Nothing is invented."""
    path = build_db(tmp_path, providers=("ea_fc26",))
    c = make_client(path)
    assert c.get(HEALTH).status_code == 200
    body = c.get(FRESH).json(); validate("DataFreshnessResponse", body)
    roles = _by_role(body)
    for role in ("MARKET_VALUE", "ENRICHMENT"):
        assert roles[role]["availability"] == "NO_SOURCE_DATA" and roles[role]["data_origin"] is None
        assert roles[role]["dataset_version"] is None and roles[role]["last_fetched_at"] is None and roles[role]["record_count"] is None
    assert roles["PLAYER_ATTRIBUTES"]["availability"] == "AVAILABLE"
    assert body["meta"]["contains_sample_data"] is False              # nothing sample-based was loaded
    links = c.app.state.services.runtime.state.link_states
    assert {l.status.value for l in links} == {"NOT_EVALUATED"}       # no matching ran -> never "UNMATCHED"


# ---------------- errors ----------------
def test_unexpected_error_is_a_generic_500_problem(small_db, make_client, validate):
    c = make_client(small_db)

    def boom():
        raise RuntimeError("secret /etc/passwd SELECT * FROM players")
    c.app.state.services.freshness.get = boom
    r = c.get(FRESH)
    assert r.status_code == 500 and r.headers["content-type"].startswith("application/problem+json")
    validate("Problem", r.json())
    assert r.json()["code"] == "INTERNAL_ERROR" and r.json()["instance"] == FRESH
    assert "secret" not in r.text and "passwd" not in r.text and "SELECT" not in r.text and "errors" not in r.json()


def test_problem_503_shape(tmp_path, make_client, validate):
    r = make_client(tmp_path / "missing.duckdb").get(FRESH)
    validate("Problem", r.json())
    assert r.json()["title"] == "Data unavailable" and r.json()["instance"] == FRESH and "errors" not in r.json()


# ---------------- scope & safety ----------------
def test_only_the_implemented_contract_endpoints_exist(small_db, make_client):
    c = make_client(small_db)
    # FastAPI >= 0.14x wraps included routers lazily, so the route inventory is read from the generated schema.
    inventory = {(m.upper(), path) for path, item in c.app.openapi()["paths"].items() for m in item}
    assert inventory == {("GET", HEALTH), ("GET", FRESH), ("GET", "/api/v1/players/search"),
                         ("GET", "/api/v1/players/{player_id}")}   # Steps 4.1-4.3; docs are off by default
    for later in ("/api/v1/players/ea:209331/market-value", "/api/v1/players/ea:209331/ea-attributes", "/api/v1/players/ea:209331/lineage",
                  "/api/v1/players/ea:209331/injuries", "/api/v1/admin/review-queue"):
        assert c.get(later).status_code == 404                        # contract endpoints not built yet (Step 4.4+)
    assert c.post(HEALTH).status_code == 405                           # read-only API


def test_docs_are_off_by_default_and_on_when_enabled(small_db, make_client):
    off = make_client(small_db)
    assert off.get("/docs").status_code == 404 and off.get("/openapi.json").status_code == 404
    on = make_client(small_db, enable_docs=True)
    assert on.get("/openapi.json").status_code == 200


def test_api_never_modifies_the_database_file(small_db, make_client, tmp_path):
    import shutil
    path = tmp_path / "ro.duckdb"; shutil.copy(small_db, path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    c = make_client(path)
    for _ in range(3):
        assert c.get(HEALTH).status_code == 200 and c.get(FRESH).status_code == 200
    c.__exit__(None, None, None)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not list(tmp_path.glob("ro.duckdb.wal"))


def test_cors_is_deny_by_default(small_db, make_client):
    r = make_client(small_db).get(HEALTH, headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    allowed = make_client(small_db, cors_origins=("https://app.example",))
    assert allowed.get(HEALTH, headers={"Origin": "https://app.example"}).headers["access-control-allow-origin"] == "https://app.example"
    assert "access-control-allow-origin" not in allowed.get(HEALTH, headers={"Origin": "https://evil.example"}).headers


def test_full_scale_startup_is_fast(full_db, make_client):
    import time
    t = time.perf_counter()
    c = make_client(full_db)
    assert c.get(HEALTH).status_code == 200
    assert time.perf_counter() - t < 10          # generous guard (measured ~0.6 s): RM1/RM2 load at 16,107 players
