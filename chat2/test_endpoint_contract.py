"""Step 3.2 - endpoint contract checks. Pure documentation tests: NO API exists; nothing here imports one."""
import json
import re
from pathlib import Path

import pytest

CONTRACT = Path(__file__).parents[2] / "api_contract" / "openapi.json"

EXPECTED_OPERATIONS = {
    ("GET", "/health"): "getHealth",
    ("GET", "/data-freshness"): "getDataFreshness",
    ("GET", "/players/search"): "searchPlayers",
    ("GET", "/players/{player_id}"): "getPlayer",
    ("GET", "/players/{player_id}/market-value"): "getPlayerMarketValue",
    ("GET", "/players/{player_id}/ea-attributes"): "getPlayerEaAttributes",
    ("GET", "/players/{player_id}/injuries"): "getPlayerInjuries",
    ("GET", "/players/{player_id}/lineage"): "getPlayerLineage",
}


@pytest.fixture(scope="module")
def spec():
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def _refs(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref":
                yield v
            else:
                yield from _refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _refs(v)


def _resolve(spec, ref):
    assert ref.startswith("#/")
    node = spec
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def test_exact_endpoint_list_and_read_only(spec):
    ops = {(m.upper(), path): o["operationId"] for path, item in spec["paths"].items() for m, o in item.items()}
    assert ops == EXPECTED_OPERATIONS
    assert all(m == "GET" for m, _ in ops)            # no write endpoints in this contract
    assert not any("/admin" in p for _, p in ops)     # admin endpoints deferred (need an auth design)
    assert len(set(ops.values())) == len(ops)


def test_all_refs_resolve(spec):
    for r in set(_refs(spec)):
        _resolve(spec, r)


def test_every_operation_has_200_and_problem_errors(spec):
    for path, item in spec["paths"].items():
        for o in item.values():
            assert "200" in o["responses"]
            assert o["x-status"] in ("IMPLEMENTABLE_TODAY", "SCHEMA_ONLY", "PLANNED")
            for code, r in o["responses"].items():
                if code != "200":
                    resp = _resolve(spec, r["$ref"])
                    assert "application/problem+json" in resp["content"]
            if "{player_id}" in path:
                assert {"400", "404", "503", "500"} <= set(o["responses"])


def test_player_id_path_parameter_and_pattern(spec):
    pat = re.compile(spec["components"]["schemas"]["PlayerId"]["pattern"])
    assert pat.match("ea:209331") and pat.match("p:3943921e-6d70-4677-acb2-bceb4988cf07")
    assert not pat.match("209331") and not pat.match("ea:") and not pat.match("x:1") and not pat.match("ea:1 OR 1=1")
    for path, item in spec["paths"].items():
        if "{player_id}" in path:
            assert item["get"]["parameters"][0]["$ref"].endswith("PlayerIdPath")


def test_search_parameters_have_bounds(spec):
    params = {_resolve(spec, p["$ref"])["name"]: _resolve(spec, p["$ref"])
              for p in spec["paths"]["/players/search"]["get"]["parameters"]}
    assert set(params) == {"q", "entity_kind", "position", "nationality", "club", "min_overall", "sort", "limit", "offset"}
    assert params["q"]["required"] is True and params["q"]["schema"]["minLength"] == 2
    assert params["limit"]["schema"]["maximum"] == 50
    assert params["offset"]["schema"]["maximum"] == 10000
    assert params["sort"]["schema"]["enum"] == ["relevance", "overall_desc", "name_asc"]
    # only filters the DB can actually serve (no height/age/pace/market-value filters)
    assert "min_overall" in params and not {"min_pace", "age", "max_value"} & set(params)


def test_planned_fields_are_nullable_or_availability_tagged(spec):
    sch = spec["components"]["schemas"]
    for name, s in sch.items():
        for pname, p in s.get("properties", {}).items():
            if p.get("x-status") == "PLANNED":
                t = p.get("type")
                assert (isinstance(t, list) and "null" in t), f"{name}.{pname} is PLANNED but not nullable"
        if s.get("x-status") == "PLANNED":
            assert "availability" in s["required"], f"{name} PLANNED block needs an availability"
    # model block: explicit target, today always NOT_YET_INTEGRATED/null
    ex = spec["components"]["examples"]
    for e in ex.values():
        for v in _find(e["value"], "model_estimate"):
            assert v == {"availability": "NOT_YET_INTEGRATED", "target": "EA_INGAME_VALUE", "amount_eur": None, "model_version": None}
    assert sch["ModelEstimate"]["properties"]["target"]["enum"] == ["EA_INGAME_VALUE"]


def _find(node, key):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            else:
                yield from _find(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from _find(v, key)


def test_examples_validate_against_schemas(spec):
    jsonschema = pytest.importorskip("jsonschema")
    # map each example to the schema of the response it is attached to
    attached = {}
    for path, item in spec["paths"].items():
        for o in item.values():
            mt = o["responses"]["200"]["content"]["application/json"]
            for name in mt.get("examples", {}):
                attached[name] = mt["schema"]["$ref"].split("/")[-1]
    attached["problem_invalid_q"] = attached["problem_not_found"] = "Problem"
    assert set(attached) == set(spec["components"]["examples"])
    for name, schema in attached.items():
        wrapper = {"$ref": f"#/components/schemas/{schema}", "components": spec["components"]}
        jsonschema.Draft202012Validator(wrapper).validate(spec["components"]["examples"][name]["value"])


def test_example_honesty_invariants(spec):
    ex = {k: v["value"] for k, v in spec["components"]["examples"].items()}
    text = json.dumps(ex)
    assert '"live:' not in text and "player_face_url" not in text and "sofifa" not in text.lower()
    for key in ("profile_ea_only_salah", "profile_ea_only_ambiguous"):
        p = ex[key]
        assert p["identity"]["entity_kind"] == "EA_ONLY" and p["identity"]["canonical_player_uid"] is None
        assert p["identity"]["source_ids"]["transfermarkt_id"] is None and p["identity"]["source_ids"]["wikidata_id"] is None
        assert p["values"]["source_market_value"] == {"availability": "NOT_MATCHED", "amount_eur": None, "valuation_date": None, "provenance": None}
        assert p["injury"]["status"] == "NOT_EVALUATED" and p["image"] is None
        assert p["identity"]["links"]["wikidata"]["status"] == "NOT_EVALUATED"
        assert [x["source"] for x in p["provenance"]] == ["ea_fc26"]
    amb = ex["profile_ea_only_ambiguous"]["identity"]["links"]["transfermarkt"]
    assert amb["status"] == "AMBIGUOUS" and amb["review_pending"] is True and amb["confidence"] is None and amb["matched_on"] is None
    hl = ex["profile_canonical_haaland"]
    assert hl["id"] == "ea:239085" and hl["identity"]["entity_kind"] == "CANONICAL"
    assert hl["values"]["source_market_value"]["provenance"]["data_origin"] == "SAMPLE_FIXTURE"
    assert hl["meta"]["contains_sample_data"] is True
    assert hl["image"]["source"] == "wikidata"
    assert hl["injury"]["status"] == "NO_SOURCE_AVAILABLE"      # 'no source', never 'no injuries'
    assert ex["market_value_ea_only"]["points"] == [] and ex["market_value_ea_only"]["availability"] == "NOT_MATCHED"
    pts = ex["market_value_haaland"]["points"]
    assert [p["valuation_date"] for p in pts] == sorted(p["valuation_date"] for p in pts)
    assert ex["market_value_haaland"]["latest"] == pts[-1]
    assert ex["ea_attributes_salah"]["detailed_attributes"] == {"availability": "NOT_YET_INTEGRATED", "attributes": None}
    assert all(s["is_live"] is False for s in ex["data_freshness_today"]["sources"])
    roles = {s["role"]: s for s in ex["data_freshness_today"]["sources"]}
    assert roles["INJURY"]["availability"] == "NO_SOURCE_DATA" and roles["MODEL"]["availability"] == "NOT_YET_INTEGRATED"
    assert roles["ENRICHMENT"]["data_origin"] == "SAMPLE_FIXTURE" and roles["ENRICHMENT"]["dataset_version"].startswith("fixture:")
    lin = {f["field_name"]: f for f in ex["lineage_vini"]["fields"]}
    assert {v["source"] for v in lin["display_name"]["values"]} == {"ea_fc26", "transfermarkt_dataset"}  # conflict kept, not merged
    assert ex["injuries_canonical"]["records"] == [] and ex["injuries_ea_only"]["records"] == []


def test_no_endpoint_exposes_non_wikidata_images_or_internal_details(spec):
    sch = spec["components"]["schemas"]
    assert sch["PlayerImage"]["properties"]["source"]["enum"] == ["wikidata"]
    names = {p for s in sch.values() for p in s.get("properties", {})}
    assert not names & {"sql", "stack", "traceback", "db_path", "player_face_url", "raw_json", "candidates"}
