from datetime import date, datetime, timezone

import pytest

from ingestion.providers.base import RawRecord
from ingestion.normalizer import (
    FORBIDDEN_GENERIC_FIELD_NAMES,
    IdentityFieldValue,
    normalize,
    normalize_appearance,
    normalize_ea_attributes,
    normalize_identity_fields,
    normalize_market_value,
    normalize_transfer,
)


def _record(source, record_type, payload, source_record_id="1"):
    return RawRecord(
        source=source,
        source_record_id=source_record_id,
        record_type=record_type,
        payload=payload,
        fetched_at=datetime.now(timezone.utc),
        dataset_version="test-version",
    )


# ═══════════════════════════════════════════════════════════
# HARD RULE #1 — EA value never becomes a generic field name
# ═══════════════════════════════════════════════════════════

def test_ea_value_becomes_value_eur_ingame():
    r = _record("ea_fc26", "player", {"id": 1, "overall_rating": 90, "potential": 91, "value_eur": 150_000_000})
    result = normalize_ea_attributes(r)
    assert result.value_eur_ingame == 150_000_000
    assert result.ea_fc26_id == 1


def test_forbidden_field_names_never_appear_in_identity_fields():
    r = _record("ea_fc26", "player", {
        "id": 1, "player_name": "Salah", "nationality": "Egypt", "team": "Liverpool",
        "value_eur": 100_000_000,  # must NOT leak into identity fields
    })
    identity_fields = normalize_identity_fields(r)
    field_names = {f.field_name for f in identity_fields}
    assert field_names.isdisjoint(FORBIDDEN_GENERIC_FIELD_NAMES)
    assert "value_eur" not in field_names


def test_full_normalize_ea_player_never_produces_generic_value_key():
    r = _record("ea_fc26", "player", {
        "id": 1, "player_name": "Haaland", "nationality": "Norway", "team": "Manchester City",
        "overall_rating": 91, "potential": 94, "value_eur": 200_000_000,
    })
    result = normalize(r)
    all_field_names = {f.field_name for f in result.identity_fields}
    assert all_field_names.isdisjoint(FORBIDDEN_GENERIC_FIELD_NAMES)
    assert result.ea_attributes.value_eur_ingame == 200_000_000


# ═══════════════════════════════════════════════════════════
# HARD RULE #2 — transfermarkt market value stays in its own record type
# ═══════════════════════════════════════════════════════════

def test_transfermarkt_valuation_normalizes_to_market_value_record():
    r = _record("transfermarkt_dataset", "valuation",
                {"player_id": 418560, "date": "2025-06-01", "market_value_in_eur": 200_000_000},
                source_record_id="418560:2025-06-01")
    mv = normalize_market_value(r)
    assert mv.value_eur == 200_000_000
    assert mv.valuation_date == date(2025, 6, 1)
    assert mv.player_id_in_source == "418560"
    assert mv.source == "transfermarkt_dataset"


def test_ea_and_transfermarkt_values_never_collide_in_same_output():
    """Two different sources' notions of 'value' must never merge into one field."""
    ea_result = normalize(_record("ea_fc26", "player",
        {"id": 1, "player_name": "X", "overall_rating": 80, "potential": 80, "value_eur": 50_000_000}))
    tm_result = normalize(_record("transfermarkt_dataset", "valuation",
        {"player_id": 1, "date": "2025-01-01", "market_value_in_eur": 70_000_000},
        source_record_id="1:2025-01-01"))

    assert ea_result.ea_attributes.value_eur_ingame == 50_000_000
    assert ea_result.market_value is None
    assert tm_result.market_value.value_eur == 70_000_000
    assert tm_result.ea_attributes is None
    # different numbers, different tables — never the same field


# ═══════════════════════════════════════════════════════════
# identity fields: conflicting values from different sources both kept
# ═══════════════════════════════════════════════════════════

def test_conflicting_nationality_from_two_sources_both_preserved():
    ea_record = _record("ea_fc26", "player", {"id": 1, "player_name": "X", "nationality": "Brazil", "team": "A"})
    tm_record = _record("transfermarkt_dataset", "player",
                         {"player_id": 100, "name": "X", "country_of_citizenship": "Portugal"},
                         source_record_id="100")

    ea_fields = normalize_identity_fields(ea_record)
    tm_fields = normalize_identity_fields(tm_record)

    ea_nat = [f for f in ea_fields if f.field_name == "nationality"][0]
    tm_nat = [f for f in tm_fields if f.field_name == "nationality"][0]

    assert ea_nat.field_value == "Brazil"
    assert tm_nat.field_value == "Portugal"
    assert ea_nat.source == "ea_fc26"
    assert tm_nat.source == "transfermarkt_dataset"
    # both exist simultaneously — normalizer never overwrites one with the other


def test_identity_field_carries_full_provenance():
    r = _record("ea_fc26", "player", {"id": 1, "player_name": "Test", "nationality": "Egypt", "team": "Liverpool"})
    fields = normalize_identity_fields(r)
    assert all(isinstance(f, IdentityFieldValue) for f in fields)
    for f in fields:
        assert f.source == "ea_fc26"
        assert f.source_record_id == "1"
        assert f.dataset_version == "test-version"
        assert f.fetched_at == r.fetched_at


# ═══════════════════════════════════════════════════════════
# transfer / appearance normalization
# ═══════════════════════════════════════════════════════════

def test_normalize_transfer():
    r = _record("transfermarkt_dataset", "transfer", {
        "player_id": 581678, "transfer_date": "2023-06-01",
        "from_club_name": "Borussia Dortmund", "to_club_name": "Real Madrid",
        "transfer_fee": 103_000_000, "is_loan": False,
    }, source_record_id="581678:2023-06-01:0")
    t = normalize_transfer(r)
    assert t.from_club == "Borussia Dortmund"
    assert t.to_club == "Real Madrid"
    assert t.fee_eur == 103_000_000
    assert t.is_loan is False
    assert t.transfer_date == date(2023, 6, 1)


def test_normalize_appearance():
    r = _record("transfermarkt_dataset", "appearance", {
        "player_id": 418560, "game_date": "2025-08-16", "competition": "Premier League",
        "minutes_played": 90, "goals": 2, "assists": 0,
    }, source_record_id="418560:2025-08-16:0")
    a = normalize_appearance(r)
    assert a.goals == 2
    assert a.minutes_played == 90
    assert a.game_date == date(2025, 8, 16)


def test_normalize_transfer_missing_optional_fields_not_fabricated():
    r = _record("transfermarkt_dataset", "transfer",
                {"player_id": 1, "transfer_date": "2025-01-01"},
                source_record_id="1:2025-01-01:0")
    t = normalize_transfer(r)
    assert t.fee_eur is None
    assert t.is_loan is None
    assert t.from_club is None


# ═══════════════════════════════════════════════════════════
# dispatcher
# ═══════════════════════════════════════════════════════════

def test_normalize_dispatches_correctly_by_source_and_type():
    r = _record("transfermarkt_dataset", "appearance", {
        "player_id": 1, "game_date": "2025-08-16", "minutes_played": 90, "goals": 1, "assists": 0,
    }, source_record_id="1:2025-08-16:0")
    result = normalize(r)
    assert result.appearance is not None
    assert result.market_value is None
    assert result.transfer is None
    assert result.ea_attributes is None


def test_normalize_unknown_combination_raises_not_implemented():
    r = _record("thesportsdb", "player", {"id": "1"})
    with pytest.raises(NotImplementedError):
        normalize(r)


# ═══════════════════════════════════════════════════════════
# no injury fabrication — no injury normalizer exists at all
# ═══════════════════════════════════════════════════════════

def test_no_injury_normalization_function_exists():
    import ingestion.normalizer as normalizer_module
    assert not hasattr(normalizer_module, "normalize_injury")
    assert not any("injury" in name.lower() for name in dir(normalizer_module))


# ═══════════════════════════════════════════════════════════
# Phase 2.6 — wikidata
# ═══════════════════════════════════════════════════════════

def test_wikidata_identity_fields_never_touch_club_or_position():
    """Wikidata isn't queried for club/position per the approved
    priority-field list — must never appear in its identity fields."""
    r = _record("wikidata", "player", {
        "qid": "Q28967995", "name": "Erling Haaland",
        "date_of_birth": "2000-07-21", "country_of_citizenship": "Norway",
    }, source_record_id="Q28967995")
    fields = normalize_identity_fields(r)
    field_names = {f.field_name for f in fields}
    assert field_names == {"display_name", "nationality", "date_of_birth"}
    assert "club" not in field_names and "position" not in field_names


def test_wikidata_image_license_and_attribution_stay_none_when_absent():
    from ingestion.normalizer import normalize_wikidata_image
    r = _record("wikidata", "player", {
        "qid": "Q00000001", "name": "Kylian Mbappe",
        "image_filename": None, "image_license": None, "image_attribution": None,
    }, source_record_id="Q00000001")
    image = normalize_wikidata_image(r)
    assert image.image_filename is None
    assert image.license is None       # never guessed/defaulted
    assert image.attribution is None   # never guessed/defaulted


def test_wikidata_image_carries_real_verified_fields_through_unchanged():
    from ingestion.normalizer import normalize_wikidata_image
    r = _record("wikidata", "player", {
        "qid": "Q28967995", "name": "Erling Haaland",
        "image_filename": "Erling Haaland Morocco v Norway 7 June 2026-51.jpg",
        "image_license": None, "image_attribution": "Bryan Berlin",
    }, source_record_id="Q28967995")
    image = normalize_wikidata_image(r)
    assert image.image_filename == "Erling Haaland Morocco v Norway 7 June 2026-51.jpg"
    assert image.attribution == "Bryan Berlin"
    assert image.license is None  # genuinely wasn't available — stays None, not fabricated


def test_wikidata_never_overwrites_ea_or_transfermarkt_identity_fields():
    """Same non-destructive guarantee as the EA-vs-transfermarkt conflict
    test — a third source disagreeing must add a new row, not replace."""
    ea_field = normalize_identity_fields(_record("ea_fc26", "player", {
        "id": 1, "player_name": "Erling Haaland", "nationality": "Norway", "team": "Manchester City",
    }))
    wikidata_field = normalize_identity_fields(_record("wikidata", "player", {
        "qid": "Q28967995", "name": "Erling Haaland", "country_of_citizenship": "Norway",
    }, source_record_id="Q28967995"))

    ea_nat = [f for f in ea_field if f.field_name == "nationality"][0]
    wd_nat = [f for f in wikidata_field if f.field_name == "nationality"][0]
    assert ea_nat.source == "ea_fc26"
    assert wd_nat.source == "wikidata"
    # both exist as independent rows — normalizer has no way to merge them even if it wanted to


def test_normalize_wikidata_full_dispatch_produces_identity_and_image():
    r = _record("wikidata", "player", {
        "qid": "Q28967995", "name": "Erling Haaland", "date_of_birth": "2000-07-21",
        "country_of_citizenship": "Norway",
        "image_filename": "Erling Haaland Morocco v Norway 7 June 2026-51.jpg",
        "image_license": None, "image_attribution": "Bryan Berlin",
    }, source_record_id="Q28967995")
    result = normalize(r)
    assert len(result.identity_fields) == 3
    assert result.image is not None
    assert result.image.attribution == "Bryan Berlin"
    assert result.ea_attributes is None
    assert result.market_value is None
