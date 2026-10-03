import pandas as pd
import pytest

from api import CONTRACT_VERSION
from api.domain import enums
from api.domain.enums import DataOrigin, EntityKind, MatchStatus
from api.domain.link_rules import derive_link_state
from api.domain.models import PlayerIndexRow
from api.domain.normalize import normalize
from api.domain.player_index import PlayerIndex
from api.domain.source_registry import SOURCES, is_live, origin_for
from tests.test_contract.test_search_spec import EA_CSV, normalize as reference_normalize


# ---- contract drift ---------------------------------------------------------------------------
@pytest.mark.parametrize("enum_cls,schema", [
    (enums.DataOrigin, "DataOrigin"), (enums.Availability, "Availability"), (enums.MatchStatus, "MatchStatus"),
    (enums.EntityKind, "EntityKind"), (enums.FreshnessRole, "FreshnessRole"), (enums.ProblemCode, "ProblemCode"),
])
def test_domain_enums_equal_contract_enums(contract, enum_cls, schema):
    assert [m.value for m in enum_cls] == contract["components"]["schemas"][schema]["enum"]


def test_contract_version_constant_equals_contract(contract):
    assert CONTRACT_VERSION == contract["info"]["version"]


# ---- normalization: same function as the executable spec ---------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("Martin Ødegaard", "martin odegaard"), ("Dagur Dan Þór\u00adhalls\u00adson", "dagur dan thorhallsson"),
    ("N'Golo Kanté", "n golo kante"), ("Vini Jr.", "vini jr"), ("  Łukasz   Fabiański ", "lukasz fabianski"),
    ("Pascal Groß", "pascal gross"), ("", ""),
])
def test_normalize_goldens(raw, expected):
    assert normalize(raw) == expected


def test_normalize_equals_reference_on_all_16107_real_names():
    names = pd.read_csv(EA_CSV, low_memory=False)["player_name"].astype(str).tolist()
    assert len(names) == 16107
    assert all(normalize(n) == reference_normalize(n) for n in names)
    assert all(normalize(n) and normalize(n).isascii() for n in names)


def test_normalize_is_idempotent_and_keeps_other_scripts():
    assert normalize(normalize("Ødegaard  O'Neil")) == normalize("Ødegaard  O'Neil")
    assert normalize("Мбаппе") == "мбаппе"          # letters of any script are kept (never dropped to empty)


# ---- source registry --------------------------------------------------------------------------
def test_registry_origins_and_liveness():
    assert origin_for("ea_fc26") is DataOrigin.REAL_FULL
    assert origin_for("transfermarkt_dataset") is DataOrigin.SAMPLE_FIXTURE
    assert origin_for("wikidata") is DataOrigin.SAMPLE_FIXTURE
    assert origin_for("some_new_source") is DataOrigin.UNKNOWN and origin_for(None) is DataOrigin.UNKNOWN
    assert not any(is_live(s.name) for s in SOURCES) and not is_live("unknown")


# ---- link rules --------------------------------------------------------------------------------
def test_link_state_exposes_confidence_only_for_linked_statuses():
    assert derive_link_state("MATCHED", 1.0, "name,dob") == (MatchStatus.MATCHED, 1.0, ("name", "dob"))
    assert derive_link_state("PROBABLE_MATCH", 0.85123456, "name") == (MatchStatus.PROBABLE_MATCH, 0.8512, ("name",))
    assert derive_link_state("UNMATCHED", 0.0, "") == (MatchStatus.UNMATCHED, None, None)       # 0.0 would mislead
    assert derive_link_state("AMBIGUOUS", 0.55, "dob") == (MatchStatus.AMBIGUOUS, None, None)   # may reveal a candidate
    assert derive_link_state(None, None, None) == (MatchStatus.NOT_EVALUATED, None, None)
    with pytest.raises(ValueError):
        derive_link_state("SOMETHING_ELSE", 1.0, "")


# ---- PlayerIndex -------------------------------------------------------------------------------
def _row(i, uid=None):
    return PlayerIndexRow(i, f"P{i}", None, None, None, None, None, 80, 85, 1000, "v", None, uid, None, None)


def test_player_index_basics_and_duplicates():
    idx = PlayerIndex([_row(1), _row(2, "u"), _row(2, "u")])
    assert len(idx) == 3 and idx.duplicate_ids() == {2}
    assert idx.get(1).entity_kind is EntityKind.EA_ONLY and idx.get(2).entity_kind is EntityKind.CANONICAL
    assert idx.get(99) is None and idx.count(EntityKind.EA_ONLY) == 1
