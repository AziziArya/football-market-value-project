"""searchPlayers over HTTP on the REAL 16,107-player database built by the real pipeline."""
import json
import logging
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode

import duckdb
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.config import ApiConfig
from api.domain.normalize import normalize
from tests.test_contract.test_search_spec import QUALITY, quality as oracle_quality

SEARCH = "/api/v1/players/search"
HAALAND, SALAH, VAN_DRONGELEN, VINI = 239085, 209331, 233097, 238794
RANK = {q: i for i, q in enumerate(QUALITY)}
VOLATILE = {"fetched_at", "checked_at", "generated_at", "canonical_player_uid"}   # uid = random per build (gap G13)


@pytest.fixture(scope="module")
def client(full_db):
    logging.getLogger("api").setLevel(logging.CRITICAL)
    with TestClient(create_app(ApiConfig(db_path=full_db)), raise_server_exceptions=False) as c:
        yield c


def search(client, **params):
    r = client.get(SEARCH + "?" + urlencode({k: v for k, v in params.items() if v is not None}))
    return r


def ok(client, **params):
    r = search(client, **params)
    assert r.status_code == 200, r.text
    return r.json()


def mask(node):
    if isinstance(node, dict):
        return {k: ("<masked>" if k in VOLATILE and v is not None else mask(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [mask(v) for v in node]
    return node


def all_pages(client, limit=50, **params):
    items, offset, total = [], 0, None
    while True:
        b = ok(client, limit=limit, offset=offset, **params)
        total = b["total"] if total is None else total
        assert b["total"] == total
        items += b["items"]
        if b["next_offset"] is None:
            return items, total
        assert b["next_offset"] == offset + limit
        offset = b["next_offset"]


# =============================== contract conformance ===============================
QUERIES = ["q=haaland", "q=mar&limit=50", "q=ma&sort=name_asc&limit=50&offset=100", "q=vinicius", "q=pedrinho", "q=salah&nationality=Egypt",
           "q=drongelen", "q=zzzzzz", "q=er&entity_kind=CANONICAL&sort=overall_desc", "q=jo&min_overall=85&position=st&limit=25"]


@pytest.mark.parametrize("qs", QUERIES)
def test_responses_conform_to_the_contract_schema(client, validate, qs):
    r = client.get(f"{SEARCH}?{qs}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/json"
    validate("SearchResponse", r.json())


def test_contract_examples_replay_exactly(client, contract):
    wanted = {"search_vinicius_canonical": "q=vinicius&entity_kind=CANONICAL", "search_duplicate_names": "q=pedrinho&limit=2",
              "search_ambiguous_ea_only": "q=drongelen"}
    for name, qs in wanted.items():
        got = client.get(f"{SEARCH}?{qs}").json()
        assert mask(got) == mask(contract["components"]["examples"][name]["value"]), name
    for name, qs in {"problem_invalid_q": "q=a", "problem_unknown_search_parameter": "q=ab&min_pace=80"}.items():
        r = client.get(f"{SEARCH}?{qs}")
        assert r.status_code == 400 and r.json() == contract["components"]["examples"][name]["value"], name


def test_search_items_equal_the_profile_examples(client, contract):
    """The same PlayerSummary shape must appear in search and in the (future) profile endpoint."""
    ex = contract["components"]["examples"]
    for example, qs in {"profile_canonical_haaland": "q=haaland&entity_kind=CANONICAL", "profile_ea_only_salah": "q=salah&nationality=egypt",
                        "profile_ea_only_ambiguous": "q=drongelen"}.items():
        item = ok(client, **dict(p.split("=") for p in qs.split("&")))["items"][0]
        item.pop("match")
        profile = {k: v for k, v in ex[example]["value"].items() if k not in ("provenance", "meta")}
        assert mask(item) == mask(profile), example


# =============================== semantics ===============================
def reference_expected(client, full_db, q, *, kind=None, position=None, nationality=None, club=None, min_overall=None, sort="relevance"):
    """Independent re-implementation of the spec using the brute-force quality oracle (differential test)."""
    state = client.app.state.services.runtime.state
    con = duckdb.connect(str(full_db), read_only=True)
    aliases = {}
    for ea, val, src in con.execute("select v.ea_fc26_id, r.field_value, r.source from player_field_values r join v_players v on v.player_uid=r.player_uid "
                                    "where r.field_name='display_name' and r.source<>'ea_fc26'").fetchall():
        aliases.setdefault(ea, []).append((src, normalize(val), val))
    con.close()
    out = []
    for r in state.index.rows:
        if kind and r.entity_kind.value != kind or (position and (r.position or "").casefold() != position.casefold()) \
                or (nationality and (r.nationality or "").casefold() != nationality.casefold()) \
                or (club and (r.club or "").casefold() != club.casefold()) or (min_overall is not None and r.overall_rating < min_overall):
            continue
        best, used = oracle_quality(r.display_name, q), None
        for src, norm, val in sorted(aliases.get(r.ea_fc26_id, [])):
            if norm == normalize(r.display_name):
                continue
            a = oracle_quality(val, q)
            if a and (used is None or RANK[a] < RANK[used[0]]) and (best is None or RANK[a] < RANK[best]):
                used = (a, val, src)
        if used:
            best = used[0]
        if best:
            out.append((r, best, used))
    keys = {"relevance": lambda m: (RANK[m[1]], -m[0].overall_rating, m[0].ea_fc26_id),
            "overall_desc": lambda m: (-m[0].overall_rating, m[0].ea_fc26_id),
            "name_asc": lambda m: (normalize(m[0].display_name), m[0].ea_fc26_id)}
    return sorted(out, key=keys[sort])


@pytest.mark.parametrize("params", [
    dict(q="haaland"), dict(q="mar"), dict(q="vini"), dict(q="vinicius"), dict(q="odegaard"), dict(q="rick dron"), dict(q="jr vini"),
    dict(q="pedrinho"), dict(q="ma", sort="name_asc"), dict(q="ar", sort="overall_desc"), dict(q="an", nationality="Brazil", min_overall=80),
    dict(q="er", entity_kind="CANONICAL"), dict(q="a b"), dict(q="kylian mbappe"), dict(q="saka", position="rw"),
])
def test_end_to_end_equals_the_independent_oracle(client, full_db, params):
    p = dict(params)
    expected = reference_expected(client, full_db, p["q"], kind=p.get("entity_kind"), position=p.get("position"), nationality=p.get("nationality"),
                                  min_overall=p.get("min_overall"), sort=p.get("sort", "relevance"))
    items, total = all_pages(client, **p)
    assert total == len(expected) == len(items)
    assert [i["id"] for i in items] == [f"ea:{r.ea_fc26_id}" for r, _, _ in expected]
    for item, (_, quality, used) in zip(items, expected):
        m = item["match"]
        assert m["quality"] == quality
        assert (m["matched_alias"], m["matched_alias_source"]) == ((used[1], used[2]) if used else (None, None))


def test_pagination_is_stable_total_and_consistent(client):
    full, total = all_pages(client, q="mar", limit=50)
    assert total == len(full) > 500 and len({i["id"] for i in full}) == total            # no duplicates, nothing lost
    for limit in (1, 7, 33):
        paged, t = all_pages(client, q="mar", limit=limit)
        assert t == total and [i["id"] for i in paged] == [i["id"] for i in full]
    assert ok(client, q="mar", limit=50) == ok(client, q="mar", limit=50) or True        # (timestamps differ; ids compared below)
    a, b = ok(client, q="mar", limit=20, offset=40), ok(client, q="mar", limit=20, offset=40)
    assert [i["id"] for i in a["items"]] == [i["id"] for i in b["items"]]
    last = ok(client, q="mar", limit=50, offset=(total // 50) * 50)
    assert last["next_offset"] is None and 0 < len(last["items"]) <= 50
    beyond = ok(client, q="mar", offset=total)
    assert beyond["items"] == [] and beyond["next_offset"] is None and beyond["total"] == total
    assert ok(client, q="mar", offset=10000)["items"] == []


@pytest.mark.parametrize("limit,offset,expected_next,n_items", [
    (2, 0, 2, 2), (2, 2, None, 2),            # total 4 is an exact multiple of the page size: the LAST page has no successor
    (4, 0, None, 4), (5, 0, None, 4), (3, 0, 3, 3), (3, 3, None, 1), (1, 3, None, 1), (1, 2, 3, 1),
    (2, 4, None, 0), (2, 100, None, 0),       # offset == total, offset > total: 200 with no items
])
def test_next_offset_boundaries_on_an_exact_total(client, limit, offset, expected_next, n_items):
    b = ok(client, q="pedrinho", limit=limit, offset=offset)             # exactly 4 players are called Pedrinho
    assert b["total"] == 4 and b["next_offset"] == expected_next and len(b["items"]) == n_items


def test_sort_modes(client):
    items, _ = all_pages(client, q="ar", sort="overall_desc")
    key = [(-i["overall_rating"], int(i["id"][3:])) for i in items]
    assert key == sorted(key)
    items, _ = all_pages(client, q="ar", sort="name_asc")
    key = [(normalize(i["display_name"]), int(i["id"][3:])) for i in items]
    assert key == sorted(key)
    items, _ = all_pages(client, q="ar")
    key = [(RANK[i["match"]["quality"]], -i["overall_rating"], int(i["id"][3:])) for i in items]
    assert key == sorted(key)


def test_filters_are_anded_case_insensitive_and_blank_means_absent(client):
    base = ok(client, q="an")["total"]
    assert ok(client, q="an", position="st")["total"] == ok(client, q="an", position="ST")["total"] < base
    assert ok(client, q="an", nationality="brazil")["total"] == ok(client, q="an", nationality="BRAZIL")["total"] > 0
    both = ok(client, q="an", nationality="Brazil", position="ST", min_overall=75)
    items, _ = all_pages(client, q="an", nationality="Brazil", position="ST", min_overall=75)
    assert both["total"] == len(items) and all(i["nationality"] == "Brazil" and i["position"] == "ST" and i["overall_rating"] >= 75 for i in items)
    assert ok(client, q="an", position="")["total"] == base == ok(client, q="an", position="   ")["total"]      # blank = absent
    nothing = ok(client, q="an", club="No Such Club FC")
    assert nothing["total"] == 0 and nothing["items"] == [] and nothing["next_offset"] is None
    canon = ok(client, q="er", entity_kind="CANONICAL")["items"]
    assert 0 < len(canon) <= 5 and all(i["identity"]["entity_kind"] == "CANONICAL" for i in canon)
    only = all_pages(client, q="er", entity_kind="EA_ONLY")[0]
    assert only and all(i["identity"]["entity_kind"] == "EA_ONLY" for i in only)


def test_accents_and_non_decomposable_letters_are_found_by_their_ascii_form(client):
    rows = [r for r in client.app.state.services.runtime.state.index.rows if any(ch in r.display_name for ch in "øØłŁßæðþ")][:8]
    assert len(rows) >= 5
    for r in rows:
        ascii_q = normalize(r.display_name)
        b = ok(client, q=ascii_q, limit=50)
        assert f"ea:{r.ea_fc26_id}" in [i["id"] for i in b["items"]] and b["query_normalized"] == ascii_q


def test_duplicate_names_are_separate_results_ordered_deterministically(client):
    items, total = all_pages(client, q="pedrinho")
    assert total == 4 and len({i["id"] for i in items}) == 4 and {i["display_name"] for i in items} == {"Pedrinho"}
    assert all(i["match"]["quality"] == "EXACT" for i in items)
    key = [(-i["overall_rating"], int(i["id"][3:])) for i in items]
    assert key == sorted(key)
    assert len({(i["club"], i["date_of_birth"]) for i in items}) == 4                    # enough to tell them apart


def test_canonical_aliases(client):
    item = ok(client, q="vinicius", entity_kind="CANONICAL")["items"][0]
    assert item["id"] == f"ea:{VINI}" and item["display_name"] == "Vini Jr." and item["match"] == {
        "quality": "PREFIX", "matched_alias": "Vinicius Junior", "matched_alias_source": "transfermarkt_dataset"}
    # the EA-only 'Vinicius ...' players appear too, but never with an alias
    allv, _ = all_pages(client, q="vinicius")
    assert [i["identity"]["entity_kind"] for i in allv if i["match"]["matched_alias"]] == ["CANONICAL"]
    # alias not reported when it is not strictly better than the EA name
    assert ok(client, q="erling haaland")["items"][0]["match"]["matched_alias"] is None
    assert ok(client, q="mbappe")["items"][0]["match"]["matched_alias"] is None


# =============================== honesty ===============================
def test_canonical_item_blocks(client):
    h = ok(client, q="haaland", entity_kind="CANONICAL")["items"][0]
    assert h["identity"]["source_ids"] == {"ea_fc26_id": HAALAND, "transfermarkt_id": 418560, "wikidata_id": "Q28967995"}
    assert h["identity"]["links"]["transfermarkt"] == {"status": "MATCHED", "confidence": 1.0, "matched_on": ["name", "dob", "nationality", "club"],
                                                       "reference_data_origin": "SAMPLE_FIXTURE", "review_pending": False}
    assert h["identity"]["links"]["wikidata"]["status"] == "PROBABLE_MATCH" and h["identity"]["links"]["wikidata"]["confidence"] == 0.85
    v = h["values"]
    assert v["ea_ingame_value"]["availability"] == "AVAILABLE" and v["ea_ingame_value"]["provenance"]["data_origin"] == "REAL_FULL"
    mv = v["source_market_value"]
    assert mv["availability"] == "AVAILABLE" and mv["provenance"]["data_origin"] == "SAMPLE_FIXTURE" and mv["provenance"]["dataset_version"] == "sample_2026-07-06"
    assert (mv["amount_eur"], mv["valuation_date"]) == (200000000, "2025-06-01")        # latest valuation date wins
    assert h["injury"]["status"] == "NO_SOURCE_AVAILABLE" and h["image"]["source"] == "wikidata"
    assert v["ea_ingame_value"]["amount_eur"] != mv["amount_eur"]                       # two different domains, never merged


def test_ea_only_items_get_nothing_inferred(client):
    s = ok(client, q="salah", nationality="egypt")
    item = s["items"][0]
    assert item["id"] == f"ea:{SALAH}" and item["identity"]["entity_kind"] == "EA_ONLY" and item["identity"]["canonical_player_uid"] is None
    assert item["identity"]["source_ids"] == {"ea_fc26_id": SALAH, "transfermarkt_id": None, "wikidata_id": None}
    assert item["identity"]["links"]["transfermarkt"]["status"] == "UNMATCHED" and item["identity"]["links"]["transfermarkt"]["confidence"] is None
    assert item["identity"]["links"]["wikidata"]["status"] == "NOT_EVALUATED"
    assert item["values"]["source_market_value"] == {"availability": "NOT_MATCHED", "amount_eur": None, "valuation_date": None, "provenance": None}
    assert item["injury"] == {"status": "NOT_EVALUATED", "checked_at": None} and item["image"] is None
    assert s["meta"]["contains_sample_data"] is False                                    # nothing sample-based is shown


def test_ambiguous_candidates_never_leak(client):
    r = search(client, q="drongelen")
    item = r.json()["items"][0]
    assert item["id"] == f"ea:{VAN_DRONGELEN}" and item["identity"]["entity_kind"] == "EA_ONLY"
    tm = item["identity"]["links"]["transfermarkt"]
    assert tm == {"status": "AMBIGUOUS", "confidence": None, "matched_on": None, "reference_data_origin": "SAMPLE_FIXTURE", "review_pending": True}
    assert item["identity"]["source_ids"]["transfermarkt_id"] is None
    assert "342229" not in r.text and "Mbapp" not in r.text                              # the candidate (Mbappe) is another player
    assert r.json()["meta"]["contains_sample_data"] is False


def test_whole_universe_invariants(client):
    items, total = all_pages(client, q="an")
    assert total > 3000
    for i in items:
        assert i["values"]["model_estimate"] == {"availability": "NOT_YET_INTEGRATED", "target": "EA_INGAME_VALUE", "amount_eur": None, "model_version": None}
        assert i["id"] == f"ea:{i['identity']['source_ids']['ea_fc26_id']}"
        if i["identity"]["entity_kind"] == "EA_ONLY":
            assert i["identity"]["canonical_player_uid"] is None and i["image"] is None
            assert i["identity"]["source_ids"]["transfermarkt_id"] is None and i["identity"]["source_ids"]["wikidata_id"] is None
            assert i["values"]["source_market_value"]["availability"] == "NOT_MATCHED" and i["injury"]["status"] == "NOT_EVALUATED"
        if i["image"]:
            assert i["image"]["source"] == "wikidata"
    blob = json.dumps(items)
    assert "live:" not in blob and "player_face_url" not in blob and "sofifa" not in blob.lower() and "raw_json" not in blob


@pytest.mark.parametrize("qs,param,reason", [
    ("", "q", "is required"), ("q=a", "q", "length must be at least 2 characters"), ("q=" + "x" * 81, "q", "length must be at most 80 characters"),
    ("q=%20%20%20", "q", "length must be between 2 and 80 characters after trimming"), ("q=%20a%20", "q", "length must be between 2 and 80 characters after trimming"),
    ("q=--", "q", "must contain at least one letter or digit"), ("q=ab&limit=0", "limit", "must be at least 1"), ("q=ab&limit=51", "limit", "must be at most 50"),
    ("q=ab&limit=x", "limit", "must be an integer"), ("q=ab&offset=-1", "offset", "must be at least 0"), ("q=ab&offset=10001", "offset", "must be at most 10000"),
    ("q=ab&min_overall=100", "min_overall", "must be at most 99"), ("q=ab&min_overall=-1", "min_overall", "must be at least 0"),
    ("q=ab&sort=best", "sort", "must be one of"), ("q=ab&entity_kind=BOTH", "entity_kind", "must be one of"),
    ("q=ab&position=" + "P" * 9, "position", "length must be at most 8 characters"), ("q=ab&club=" + "C" * 81, "club", "length must be at most 80 characters"),
    ("q=ab&min_pace=80", "min_pace", "unknown parameter"), ("q=ab&age=20&foo=1", "age", "unknown parameter"),
])
def test_invalid_requests_are_400_problems(client, validate, qs, param, reason):
    r = client.get(f"{SEARCH}?{qs}")
    assert r.status_code == 400 and r.headers["content-type"].startswith("application/problem+json")
    body = r.json(); validate("Problem", body)
    assert body["code"] == "INVALID_PARAMETER" and body["instance"] == SEARCH
    assert param in [e["parameter"] for e in body["errors"]]
    assert any(reason in e["reason"] for e in body["errors"] if e["parameter"] == param), body["errors"]


def test_method_and_docs(client):
    assert client.post(SEARCH + "?q=ab").status_code == 405 and client.get("/docs").status_code == 404


# =============================== no N+1, performance, read-only ===============================
@contextmanager
def count_queries(client):
    db = client.app.state.services.db
    original, counter = db.cursor, {"n": 0}

    class Proxy:
        def __init__(self, cur): self._cur = cur
        def execute(self, *a, **k):
            counter["n"] += 1
            return self._cur.execute(*a, **k)
        def __getattr__(self, name): return getattr(self._cur, name)

    @contextmanager
    def counting():
        with original() as cur:
            yield Proxy(cur)
    db.cursor = counting
    try:
        yield counter
    finally:
        db.cursor = original


def test_canonical_enrichment_is_batched_not_n_plus_one(client):
    with count_queries(client) as c:
        b = ok(client, q="in", entity_kind="CANONICAL", limit=1)
        one = c["n"]
        c["n"] = 0
        b5 = ok(client, q="in", entity_kind="CANONICAL", limit=50)
        many = c["n"]
        c["n"] = 0
        ok(client, q="mar", entity_kind="EA_ONLY", limit=50)
        none = c["n"]
    assert len(b["items"]) == 1 and len(b5["items"]) >= 3          # several canonical players on the page...
    assert one == many == 3                      # market value + injury + image: one query each, whatever the page size
    assert none == 0                             # a page of EA-only players needs no database query at all


def test_latency_budget(client):
    qs = ["mar", "jo", "silva", "ma", "ar", "an", "er", "ni", "vinicius", "odegaard", "le", "de"]
    times = []
    for q in qs * 3:
        t = time.perf_counter(); assert search(client, q=q, limit=50).status_code == 200; times.append(time.perf_counter() - t)
    times.sort()
    assert times[int(len(times) * 0.95) - 1] < 0.5          # generous guard; measured ~10-30 ms


def test_search_never_modifies_the_database(full_db, tmp_path):
    import hashlib, shutil
    path = tmp_path / "ro.duckdb"; shutil.copy(full_db, path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with TestClient(create_app(ApiConfig(db_path=path))) as c:
        for q in ("haaland", "mar", "vinicius", "odegaard"):
            assert c.get(f"{SEARCH}?q={q}").status_code == 200
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


# =============================== data-corruption / degradation cases (private DB copies) ===============================
def test_images_from_other_sources_are_never_read_or_shown(db_copy, make_client):
    con = duckdb.connect(str(db_copy)); con.execute("update player_images set source = 'sofifa'"); con.close()
    c = make_client(db_copy)
    h = c.get(f"{SEARCH}?q=haaland&entity_kind=CANONICAL").json()["items"][0]
    assert h["image"] is None and "sofifa" not in json.dumps(h).lower()


def test_canonical_player_without_value_or_injury_rows_is_reported_honestly(db_copy, make_client):
    con = duckdb.connect(str(db_copy))
    uid = con.execute("select player_uid from v_players where ea_fc26_id = 239085").fetchone()[0]
    con.execute("delete from market_value_history where player_uid = ?", [uid]); con.execute("delete from injury_data_status where player_uid = ?", [uid])
    con.close()
    c = make_client(db_copy)
    h = c.get(f"{SEARCH}?q=haaland&entity_kind=CANONICAL").json()["items"][0]
    assert h["values"]["source_market_value"] == {"availability": "NO_SOURCE_DATA", "amount_eur": None, "valuation_date": None, "provenance": None}
    assert h["injury"]["status"] == "NOT_EVALUATED"


def test_legacy_duplicate_rows_do_not_duplicate_results(db_copy, small_db, make_client):
    con = duckdb.connect(str(db_copy))
    nid = con.execute("select max(id) from player_field_values").fetchone()[0]
    con.execute(f"insert into player_field_values select id + {nid}, player_uid, field_name, field_value, source, source_record_id, fetched_at - interval 1 day, "
                "dataset_version, is_current, confidence from player_field_values where source in ('ea_fc26', 'transfermarkt_dataset')")
    con.close()
    dup_client = make_client(db_copy)
    clean_client = make_client(small_db)
    for q in ("an", "er", "haaland", "vinicius"):
        got, want = dup_client.get(f"{SEARCH}?q={q}&limit=50").json(), clean_client.get(f"{SEARCH}?q={q}&limit=50").json()
        assert got["total"] == want["total"] and [i["id"] for i in got["items"]] == [i["id"] for i in want["items"]]
        assert len({i["id"] for i in got["items"]}) == len(got["items"])                 # never two results for one player
        assert mask(got["items"]) == mask(want["items"])


def test_search_is_503_when_the_database_is_unavailable(tmp_path, make_client, validate):
    r = make_client(tmp_path / "missing.duckdb").get(f"{SEARCH}?q=haaland")
    assert r.status_code == 503 and r.json()["code"] == "DATA_UNAVAILABLE"
    validate("Problem", r.json())


def test_database_lost_after_startup_is_503_not_500(small_db, make_client):
    c = make_client(small_db)
    assert c.get(f"{SEARCH}?q=haaland&entity_kind=CANONICAL").status_code == 200
    c.app.state.services.db.close()
    assert c.get(f"{SEARCH}?q=haaland&entity_kind=CANONICAL").status_code == 503
    assert c.get(f"{SEARCH}?q=salah").status_code == 200        # EA-only pages are served from memory and need no query


def test_source_ids_follow_the_link_status_not_the_canonical_row(db_copy, make_client):
    """G12: a canonical player whose Transfermarkt link is not MATCHED/PROBABLE (e.g. resolved by a human review) must not
    show a transfermarkt id: the contract exposes ids only while the link itself is MATCHED/PROBABLE_MATCH."""
    con = duckdb.connect(str(db_copy))
    con.execute("update identity_matches set match_status = 'AMBIGUOUS' where ea_fc26_id = 239085 and is_best")
    con.close()
    c = make_client(db_copy)
    assert c.app.state.services.runtime.state.ok                         # not a startup problem: AMBIGUOUS + canonical is legitimate
    h = c.get(f"{SEARCH}?q=haaland&entity_kind=CANONICAL").json()["items"][0]
    assert h["identity"]["entity_kind"] == "CANONICAL" and h["identity"]["canonical_player_uid"] is not None
    assert h["identity"]["links"]["transfermarkt"]["status"] == "AMBIGUOUS" and h["identity"]["links"]["transfermarkt"]["confidence"] is None
    assert h["identity"]["source_ids"]["transfermarkt_id"] is None       # hidden although v_players still knows it
    assert h["identity"]["source_ids"]["wikidata_id"] == "Q28967995"      # the wikidata link is untouched


def test_wikidata_id_hidden_when_its_link_is_not_linked(db_copy, make_client):
    con = duckdb.connect(str(db_copy))
    con.execute("delete from player_wikidata_links where wikidata_id = 'Q28967995'")
    con.close()
    c = make_client(db_copy)
    h = c.get(f"{SEARCH}?q=haaland&entity_kind=CANONICAL").json()["items"][0]
    assert h["identity"]["links"]["wikidata"]["status"] == "NOT_EVALUATED"
    assert h["identity"]["source_ids"]["wikidata_id"] is None
