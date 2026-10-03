"""getPlayer over HTTP on the REAL 16,107-player database built by the real pipeline."""
import hashlib
import json
import logging
import random
import re
import shutil
import time
from urllib.parse import urlencode

import duckdb
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.config import ApiConfig
from api.domain.player_id import PLAYER_ID_PATTERN, format_player_id, parse_player_id
from tests.test_api.test_search_endpoint import count_queries, mask

P = "/api/v1/players/"
HAALAND, SALAH, VAN_DRONGELEN, VINI = 239085, 209331, 233097, 238794
UID_RE = re.compile(r"\bp:[0-9a-f]{8}-[0-9a-f]{4}-")


@pytest.fixture(scope="module")
def client(full_db):
    logging.getLogger("api").setLevel(logging.CRITICAL)
    with TestClient(create_app(ApiConfig(db_path=full_db)), raise_server_exceptions=False) as c:
        yield c


def profile(client, ea_id):
    r = client.get(f"{P}ea:{ea_id}")
    assert r.status_code == 200, r.text
    return r.json()


def sources(body):
    return [(p["source"], p["dataset_version"], p["data_origin"]) for p in body["provenance"]]


# ============================== domain: the one public id ==============================
@pytest.mark.parametrize("raw,expected", [("ea:0", 0), ("ea:1", 1), ("ea:239085", 239085), ("ea:999999999", 999999999)])
def test_parse_valid(raw, expected):
    assert parse_player_id(raw) == expected and format_player_id(expected) == raw


@pytest.mark.parametrize("raw", ["", "239085", "ea:", "EA:1", "Ea:1", "ea:01", "ea:-1", "ea:+1", "ea:1.0", "ea:1e3", "ea:1x", "ea: 1", "ea:1 ", " ea:1",
                                 "ea:1234567890", "ea:1\n", "ea:\uff11\uff12\uff13", "ea:\u0661\u0662", "ea:1\uff12\uff13", "ea:1\u0662\u0663", "ea:12\u0663", "p:3943921e-6d70-4677-acb2-bceb4988cf07", "p:1", "x:1",
                                 "ea:1;DROP TABLE players", "ea:' OR '1'='1", "ea:" + "9" * 5000])
def test_parse_invalid(raw):
    assert parse_player_id(raw) is None


def test_pattern_constant_equals_the_contract(contract):
    assert PLAYER_ID_PATTERN == contract["components"]["schemas"]["PlayerId"]["pattern"]


# ============================== contract conformance ==============================
def test_profiles_conform_to_the_contract_schema(client, validate):
    rows = client.app.state.services.runtime.state.index.rows
    rng = random.Random(7)
    ids = [HAALAND, SALAH, VAN_DRONGELEN, VINI] + [r.ea_fc26_id for r in rows if r.canonical_player_uid] \
        + [min(r.ea_fc26_id for r in rows), max(r.ea_fc26_id for r in rows)] + [r.ea_fc26_id for r in rng.sample(rows, 150)]
    for ea_id in ids:
        r = client.get(f"{P}ea:{ea_id}")
        assert r.status_code == 200 and r.headers["content-type"] == "application/json"
        validate("PlayerProfile", r.json())


def test_contract_examples_replay_exactly(client, contract):
    ex = contract["components"]["examples"]
    for name, ea_id in {"profile_canonical_haaland": HAALAND, "profile_ea_only_salah": SALAH, "profile_ea_only_ambiguous": VAN_DRONGELEN}.items():
        assert mask(profile(client, ea_id)) == mask(ex[name]["value"]), name
    for name, pid, status in (("problem_invalid_player_id", "p:3943921e-6d70-4677-acb2-bceb4988cf07", 400), ("problem_not_found", "ea:1", 404)):
        r = client.get(P + pid)
        assert r.status_code == status and r.json() == ex[name]["value"], name


def test_profile_is_search_item_plus_provenance_and_meta(client):
    """The SAME PlayerSummary: nothing is computed differently for getPlayer."""
    rows = client.app.state.services.runtime.state.index.rows
    sample = [r for r in rows if r.canonical_player_uid] + random.Random(11).sample([r for r in rows if len(r.display_name) >= 6], 120)
    for r in sample:
        body, found, offset = profile(client, r.ea_fc26_id), None, 0
        while found is None:
            page = client.get("/api/v1/players/search?" + urlencode({"q": r.display_name, "limit": 50, "offset": offset})).json()
            found = next((i for i in page["items"] if i["id"] == f"ea:{r.ea_fc26_id}"), None)
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        assert found is not None, r.display_name
        found = {k: v for k, v in found.items() if k != "match"}
        body = {k: v for k, v in body.items() if k not in ("provenance", "meta")}
        assert mask(found) == mask(body), r.display_name


# ============================== ids: 400 / 404 / the removed p: alias ==============================
BAD_IDS = ["239085", "ea:", "ea:abc", "EA:239085", "Ea:239085", "ea:-1", "ea:%2B1", "ea:239085x", "ea:%20239085", "ea:239085%20", "ea:0239085", "ea:1234567890",
           "ea:1.5", "ea:1e5", "x:1", "p:1", "p:3943921e-6d70-4677-acb2-bceb4988cf07", "p:3943921E-6D70-4677-ACB2-BCEB4988CF07",
           "ea:1;DROP%20TABLE%20players", "ea:'%20OR%20'1'='1", "ea:1%0A", "ea:1%00", "ea:%E2%82%AC", "ea:%EF%BC%91%EF%BC%92%EF%BC%93", "ea:1%EF%BC%92%EF%BC%93", "ea:1%D9%A2%D9%A3", "ea:" + "9" * 3000,
           "ea:239085%3Cscript%3E"]


@pytest.mark.parametrize("pid", BAD_IDS)
def test_malformed_ids_are_400_invalid_player_id(client, validate, pid):
    r = client.get(P + pid)
    assert r.status_code == 400 and r.headers["content-type"].startswith("application/problem+json"), (pid[:40], r.status_code)
    body = r.json(); validate("Problem", body)
    assert body["code"] == "INVALID_PLAYER_ID" and body["title"] == "Invalid player id"
    assert body["errors"] == [{"parameter": "player_id", "reason": "must match ea:<number> (no leading zeros, at most 9 digits)"}]
    assert body["instance"] is None                                   # the path contains the rejected value: never reflected back
    assert "DROP" not in r.text and "script" not in r.text and "OR '1'" not in r.text and "9999" not in r.text and "players" not in r.text.replace("player_id", "")


@pytest.mark.parametrize("pid", ["ea:0", "ea:1", "ea:19540", "ea:279949", "ea:999999999"])
def test_well_formed_unknown_ids_are_404(client, validate, pid):
    ids = {r.ea_fc26_id for r in client.app.state.services.runtime.state.index.rows}
    assert int(pid[3:]) not in ids and 19541 in ids and 279948 in ids          # the universe really ends just before these
    r = client.get(P + pid)
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/problem+json")
    body = r.json(); validate("Problem", body)
    assert body["code"] == "PLAYER_NOT_FOUND" and body["detail"] == f"No player with id {pid}." and body["instance"] == P + pid and "errors" not in body


def test_percent_encoded_colon_is_the_same_valid_id(client):
    assert client.get(f"{P}ea%3A{HAALAND}").json()["id"] == f"ea:{HAALAND}"


def test_p_uuid_alias_is_never_accepted_and_never_returned(client):
    h = profile(client, HAALAND)
    uid = h["identity"]["canonical_player_uid"]
    assert uid
    for pid in (f"p:{uid}", uid, f"p%3A{uid}"):
        r = client.get(P + pid)
        assert r.status_code == 400 and r.json()["code"] == "INVALID_PLAYER_ID", pid
    # `id` is always ea:<n>; the raw uuid appears ONLY in the documented internal field, and no "p:<uuid>" string exists anywhere
    h.pop("identity")
    assert uid not in json.dumps(h) and not UID_RE.search(json.dumps(h))
    search = client.get("/api/v1/players/search?q=haaland&limit=50").text
    assert not UID_RE.search(search) and not UID_RE.search(client.get(f"{P}ea:{HAALAND}").text)


def test_search_is_not_shadowed_by_the_id_route(client):
    assert "items" in client.get("/api/v1/players/search?q=haaland").json()
    r = client.get("/api/v1/players/search")                       # missing q => the SEARCH validation, not INVALID_PLAYER_ID
    assert r.status_code == 400 and r.json()["code"] == "INVALID_PARAMETER"


def test_sub_resources_are_not_built_yet_and_writes_are_rejected(client):
    for tail in ("market-value", "injuries", "ea-attributes", "lineage"):
        assert client.get(f"{P}ea:{HAALAND}/{tail}").status_code == 404
    assert client.post(f"{P}ea:{HAALAND}").status_code == 405 and client.delete(f"{P}ea:{HAALAND}").status_code == 405


# ============================== content & honesty ==============================
def test_canonical_profile_haaland(client):
    h = profile(client, HAALAND)
    assert h["id"] == f"ea:{HAALAND}" and h["identity"]["entity_kind"] == "CANONICAL"
    assert h["identity"]["source_ids"] == {"ea_fc26_id": HAALAND, "transfermarkt_id": 418560, "wikidata_id": "Q28967995"}
    assert sources(h) == [("ea_fc26", "2025-09-19", "REAL_FULL"), ("transfermarkt_dataset", "sample_2026-07-06", "SAMPLE_FIXTURE"),
                          ("wikidata", h["provenance"][2]["dataset_version"], "SAMPLE_FIXTURE")]
    assert h["provenance"][2]["dataset_version"].startswith("fixture:")
    assert h["provenance"][0] == h["values"]["ea_ingame_value"]["provenance"] and h["provenance"][1] == h["values"]["source_market_value"]["provenance"]
    assert all(p["fetched_at"].endswith("Z") for p in h["provenance"])
    assert h["meta"]["contains_sample_data"] is True and h["injury"]["status"] == "NO_SOURCE_AVAILABLE" and h["image"]["source"] == "wikidata"
    v = h["values"]
    assert v["ea_ingame_value"]["amount_eur"] != v["source_market_value"]["amount_eur"] and v["model_estimate"]["amount_eur"] is None


def test_ea_only_profile_nothing_inferred(client):
    s = profile(client, SALAH)
    assert s["identity"]["entity_kind"] == "EA_ONLY" and s["identity"]["canonical_player_uid"] is None
    assert s["identity"]["source_ids"] == {"ea_fc26_id": SALAH, "transfermarkt_id": None, "wikidata_id": None}
    assert s["identity"]["links"]["wikidata"]["status"] == "NOT_EVALUATED" and s["identity"]["links"]["transfermarkt"]["status"] == "UNMATCHED"
    assert s["values"]["source_market_value"] == {"availability": "NOT_MATCHED", "amount_eur": None, "valuation_date": None, "provenance": None}
    assert s["injury"] == {"status": "NOT_EVALUATED", "checked_at": None} and s["image"] is None
    assert sources(s) == [("ea_fc26", "2025-09-19", "REAL_FULL")] and s["meta"]["contains_sample_data"] is False


def test_ambiguous_candidates_never_leak(client):
    r = client.get(f"{P}ea:{VAN_DRONGELEN}")
    b = r.json()
    assert b["identity"]["entity_kind"] == "EA_ONLY"
    assert b["identity"]["links"]["transfermarkt"] == {"status": "AMBIGUOUS", "confidence": None, "matched_on": None,
                                                       "reference_data_origin": "SAMPLE_FIXTURE", "review_pending": True}
    assert b["identity"]["source_ids"]["transfermarkt_id"] is None and sources(b) == [("ea_fc26", "2025-09-19", "REAL_FULL")]
    assert "342229" not in r.text and "Mbapp" not in r.text and "418560" not in r.text and b["meta"]["contains_sample_data"] is False


def test_whole_universe_invariants_via_the_service(client):
    """All 16,107 EA players, straight from the service (no HTTP): identity, separation, provenance, no p: ids."""
    svc = client.app.state.services.player
    rows = client.app.state.services.runtime.state.index.rows
    canonical = 0
    t = time.perf_counter()
    for r in rows:
        p = svc.get(f"ea:{r.ea_fc26_id}")
        assert p.id == f"ea:{r.ea_fc26_id}" and p.identity.source_ids.ea_fc26_id == r.ea_fc26_id
        assert p.values.model_estimate.model_dump() == {"availability": "NOT_YET_INTEGRATED", "target": "EA_INGAME_VALUE", "amount_eur": None, "model_version": None}
        assert p.provenance[0].source == "ea_fc26" and p.values.ea_ingame_value.provenance == p.provenance[0]
        assert len({x.source for x in p.provenance}) == len(p.provenance)
        if r.canonical_player_uid is None:
            assert p.identity.entity_kind.value == "EA_ONLY" and p.identity.canonical_player_uid is None and p.image is None
            assert p.identity.source_ids.transfermarkt_id is None and p.identity.source_ids.wikidata_id is None
            assert p.values.source_market_value.availability.value == "NOT_MATCHED" and p.injury.status.value == "NOT_EVALUATED"
            assert [x.source for x in p.provenance] == ["ea_fc26"] and p.meta.contains_sample_data is False
        else:
            canonical += 1
            assert p.identity.entity_kind.value == "CANONICAL" and "transfermarkt_dataset" in [x.source for x in p.provenance]
        if p.image:
            assert p.image.source == "wikidata"
        assert not UID_RE.search(p.model_dump_json()) and "live:" not in p.model_dump_json() and "sofifa" not in p.model_dump_json().lower()
    assert canonical == 5 and len(rows) == 16107
    assert time.perf_counter() - t < 60


# ============================== provenance rules on altered data ==============================
def _alter(db_copy, *statements):
    con = duckdb.connect(str(db_copy))
    for s in statements:
        con.execute(s)
    con.close()


def test_transfermarkt_provenance_falls_back_to_the_source_load_when_no_value_is_shown(db_copy, make_client):
    uid = "(select player_uid from v_players where ea_fc26_id = 239085)"
    _alter(db_copy, f"delete from market_value_history where player_uid = {uid}")
    h = make_client(db_copy).get(f"{P}ea:{HAALAND}").json()
    assert h["values"]["source_market_value"]["availability"] == "NO_SOURCE_DATA"
    tm = [p for p in h["provenance"] if p["source"] == "transfermarkt_dataset"]
    assert len(tm) == 1 and tm[0]["dataset_version"] == "sample_2026-07-06" and tm[0]["data_origin"] == "SAMPLE_FIXTURE"     # the linked id still comes from it


HAALAND_UID = "(select player_uid from v_players where ea_fc26_id = 239085)"


def test_wikidata_provenance_when_the_link_is_gone_but_the_image_is_shown(db_copy, make_client):
    # (one read-write alteration per test: DuckDB refuses a second connection with a different configuration in one process)
    _alter(db_copy, f"delete from player_wikidata_links where player_uid = {HAALAND_UID}")
    h = make_client(db_copy).get(f"{P}ea:{HAALAND}").json()
    assert h["identity"]["source_ids"]["wikidata_id"] is None and h["image"] is not None       # ids hidden, image is a wikidata contribution
    assert [p["source"] for p in h["provenance"]] == ["ea_fc26", "transfermarkt_dataset", "wikidata"]
    wd = h["provenance"][2]
    assert wd["dataset_version"].startswith("fixture:") and wd["data_origin"] == "SAMPLE_FIXTURE"      # falls back to the source's latest load


def test_wikidata_contributes_nothing_when_neither_link_nor_image_is_shown(db_copy, make_client):
    _alter(db_copy, f"delete from player_wikidata_links where player_uid = {HAALAND_UID}", f"delete from player_images where player_uid = {HAALAND_UID}")
    h = make_client(db_copy).get(f"{P}ea:{HAALAND}").json()
    assert h["image"] is None and [p["source"] for p in h["provenance"]] == ["ea_fc26", "transfermarkt_dataset"]


def test_non_wikidata_image_is_neither_shown_nor_a_provenance_source(db_copy, make_client):
    _alter(db_copy, "update player_images set source = 'sofifa'")
    h = make_client(db_copy).get(f"{P}ea:{HAALAND}").json()
    assert h["image"] is None and "sofifa" not in json.dumps(h).lower()
    assert [p["source"] for p in h["provenance"]] == ["ea_fc26", "transfermarkt_dataset", "wikidata"]      # the wikidata LINK is still shown


def test_link_not_matched_hides_the_id_but_a_shown_value_keeps_its_provenance(db_copy, make_client):
    _alter(db_copy, "update identity_matches set match_status = 'AMBIGUOUS' where ea_fc26_id = 239085 and is_best")
    h = make_client(db_copy).get(f"{P}ea:{HAALAND}").json()
    assert h["identity"]["source_ids"]["transfermarkt_id"] is None and h["identity"]["links"]["transfermarkt"]["status"] == "AMBIGUOUS"
    assert "transfermarkt_dataset" in [p["source"] for p in h["provenance"]]                              # a market value is still shown


def test_legacy_duplicate_rows_do_not_change_a_profile(db_copy, small_db, make_client):
    nid = "(select max(id) from player_field_values)"
    _alter(db_copy, f"insert into player_field_values select id + {nid}, player_uid, field_name, field_value, source, source_record_id, fetched_at - interval 1 day, "
                    "dataset_version, is_current, confidence from player_field_values where source in ('ea_fc26', 'transfermarkt_dataset', 'wikidata')")
    dup, clean = make_client(db_copy), make_client(small_db)
    for ea_id in (HAALAND, SALAH, VAN_DRONGELEN, VINI):
        assert mask(dup.get(f"{P}ea:{ea_id}").json()) == mask(clean.get(f"{P}ea:{ea_id}").json())


# ============================== availability, queries, read-only, speed ==============================
def test_503_when_the_database_is_unavailable_from_the_start(tmp_path, make_client, validate):
    c = make_client(tmp_path / "missing.duckdb")
    for pid in ("ea:239085", "ea:1"):
        r = c.get(P + pid)
        assert r.status_code == 503 and r.json()["code"] == "DATA_UNAVAILABLE"
        validate("Problem", r.json())


def test_database_lost_after_startup(small_db, make_client):
    c = make_client(small_db)
    assert c.get(f"{P}ea:{HAALAND}").status_code == 200
    c.app.state.services.db.close()
    assert c.get(f"{P}ea:{HAALAND}").status_code == 503              # canonical details need the database
    assert c.get(f"{P}ea:{SALAH}").status_code == 200                # EA-only is served from memory
    assert c.get(f"{P}ea:1").status_code == 404 and c.get(f"{P}p:abc").status_code == 400


def test_query_counts(client):
    with count_queries(client) as c:
        profile(client, SALAH); ea_only = c["n"]; c["n"] = 0
        profile(client, HAALAND); canonical = c["n"]; c["n"] = 0
        profile(client, VINI); vini = c["n"]; c["n"] = 0
        client.get(P + "ea:1"); client.get(P + "ea:abc"); misses = c["n"]
    assert ea_only == 0 and misses == 0          # no database access for EA-only players, unknown ids or malformed ids
    assert canonical == 4                        # market value + injury + image (batch) + the wikidata link provenance
    assert vini == 3                             # Vini has no Wikidata link and no image: nothing to look up for it


def test_deterministic_and_never_modifies_the_database(full_db, tmp_path):
    path = tmp_path / "ro.duckdb"; shutil.copy(full_db, path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with TestClient(create_app(ApiConfig(db_path=path))) as c:
        a = [mask(c.get(f"{P}ea:{i}").json()) for i in (HAALAND, SALAH, VAN_DRONGELEN, VINI)]
        b = [mask(c.get(f"{P}ea:{i}").json()) for i in (HAALAND, SALAH, VAN_DRONGELEN, VINI)]
        assert a == b
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_latency_budget(client):
    ids = [HAALAND, SALAH, VAN_DRONGELEN, VINI] * 15
    times = []
    for i in ids:
        t = time.perf_counter(); assert client.get(f"{P}ea:{i}").status_code == 200; times.append(time.perf_counter() - t)
    times.sort()
    assert times[int(len(times) * 0.95) - 1] < 0.5          # generous guard; measured a few ms
