"""The generated OpenAPI of the running app must conform to api_contract/openapi.json (contract wins)."""
import pytest

from api import API_BASE_PATH

IMPLEMENTED = {"/health": "getHealth", "/data-freshness": "getDataFreshness", "/players/search": "searchPlayers",
               "/players/{player_id}": "getPlayer"}


@pytest.fixture
def generated(small_db, make_client):
    return make_client(small_db, enable_docs=True).get("/openapi.json").json()


def test_paths_and_operation_ids_match_the_contract(generated, contract):
    assert {p: item["get"]["operationId"] for p, item in generated["paths"].items()} == \
           {API_BASE_PATH + p: oid for p, oid in IMPLEMENTED.items()}
    for p, oid in IMPLEMENTED.items():
        assert contract["paths"][p]["get"]["operationId"] == oid
        assert list(generated["paths"][API_BASE_PATH + p]) == ["get"]          # GET only


def test_success_response_schemas_are_the_contract_ones(generated, contract):
    for p in IMPLEMENTED:
        want = contract["paths"][p]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
        got = generated["paths"][API_BASE_PATH + p]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
        assert got == want
        assert {"503", "500"} <= set(generated["paths"][API_BASE_PATH + p]["get"]["responses"])
        want = {"200", "400", "503", "500"} if p == "/players/search" else {"200", "400", "404", "503", "500"} if "{player_id}" in p else {"200", "503", "500"}
        assert set(contract["paths"][p]["get"]["responses"]) == want


@pytest.mark.parametrize("name", ["HealthResponse", "DataFreshnessResponse", "SourceFreshness", "ResponseMeta", "Problem", "ProblemError"])
def test_schema_properties_match_the_contract(generated, contract, name):
    got, want = generated["components"]["schemas"][name], contract["components"]["schemas"][name]
    assert set(got["properties"]) == set(want["properties"])
    assert set(want.get("required", [])) <= set(got.get("required", []))                            # everything the contract guarantees is always sent
    assert got.get("additionalProperties") is False                                                 # extra fields can never leak


@pytest.mark.parametrize("name", ["FreshnessRole", "Availability", "DataOrigin"])
def test_generated_enums_equal_contract_enums(generated, contract, name):
    assert generated["components"]["schemas"][name]["enum"] == contract["components"]["schemas"][name]["enum"]


def test_is_live_is_a_constant_false_in_both(generated, contract):
    assert contract["components"]["schemas"]["SourceFreshness"]["properties"]["is_live"]["enum"] == [False]
    assert generated["components"]["schemas"]["SourceFreshness"]["properties"]["is_live"]["const"] is False


def test_contract_x_status_of_implemented_paths(contract):
    # nothing implemented in 4.1 may be a PLANNED/SCHEMA_ONLY operation
    for p in IMPLEMENTED:
        assert contract["paths"][p]["get"]["x-status"] == "IMPLEMENTABLE_TODAY"


def test_player_id_parameter_documents_the_contract_pattern(generated, contract):
    from api.domain.player_id import PLAYER_ID_PATTERN
    got = {x["name"]: x for x in generated["paths"][API_BASE_PATH + "/players/{player_id}"]["get"]["parameters"]}["player_id"]
    assert got["in"] == "path" and got["required"] is True
    assert got["schema"]["pattern"] == PLAYER_ID_PATTERN == contract["components"]["schemas"]["PlayerId"]["pattern"]


@pytest.mark.parametrize("name", ["PlayerProfile", "SearchItem", "Identity", "Links", "LinkState", "SourceIds", "Values", "EaIngameValue",
                                  "SourceMarketValue", "ModelEstimate", "InjuryBlock", "PlayerImage", "SourceProvenance", "SearchResponse"])
def test_player_schemas_have_the_contract_properties(generated, contract, name):
    def flatten(schemas, node):
        props, required = {}, set()
        for part in node.get("allOf", [node]):
            if "$ref" in part:
                p, r = flatten(schemas, schemas[part["$ref"].split("/")[-1]])
            else:
                p, r = dict(part.get("properties", {})), set(part.get("required", []))
            props.update(p); required |= r
        return props, required
    contract_name = {"Links": None}.get(name, name)
    got_props, got_req = flatten(generated["components"]["schemas"], generated["components"]["schemas"][name])
    if contract_name is None:      # `Links` is the inline object `Identity.links` in the contract
        want_props = contract["components"]["schemas"]["Identity"]["properties"]["links"]["properties"]; want_req = set(contract["components"]["schemas"]["Identity"]["properties"]["links"]["required"])
    else:
        want_props, want_req = flatten(contract["components"]["schemas"], contract["components"]["schemas"][contract_name])
    assert set(got_props) == set(want_props), name
    assert want_req <= got_req, (name, want_req - got_req)             # the contract's guarantees are all met by what we always send
