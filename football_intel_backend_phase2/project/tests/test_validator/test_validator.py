from datetime import datetime, timezone

import pytest

from ingestion.providers.base import RawRecord
from ingestion.validator import validate, validate_batch


def _record(source, record_type, payload):
    return RawRecord(
        source=source,
        source_record_id="1",
        record_type=record_type,
        payload=payload,
        fetched_at=datetime.now(timezone.utc),
        dataset_version="test",
    )


def test_valid_ea_player_passes():
    r = _record("ea_fc26", "player", {"id": 1, "player_name": "Test", "overall_rating": 80, "value_eur": 1_000_000})
    result = validate(r)
    assert result.is_valid is True
    assert result.errors == []


def test_missing_required_field_fails():
    r = _record("ea_fc26", "player", {"id": 1, "player_name": "Test"})  # missing overall_rating, value_eur
    result = validate(r)
    assert result.is_valid is False
    assert any("overall_rating" in e for e in result.errors)
    assert any("value_eur" in e for e in result.errors)


def test_out_of_range_overall_rating_fails():
    r = _record("ea_fc26", "player", {"id": 1, "player_name": "Test", "overall_rating": 150, "value_eur": 1000})
    result = validate(r)
    assert result.is_valid is False
    assert any("out of range" in e for e in result.errors)


def test_wrong_type_fails():
    r = _record("ea_fc26", "player", {"id": "not-a-number", "player_name": "Test", "overall_rating": 80, "value_eur": 1000})
    result = validate(r)
    assert result.is_valid is False
    assert any("failed type check" in e for e in result.errors)


def test_unknown_schema_rejected_not_silently_passed():
    r = _record("some_future_source", "player", {"id": 1})
    result = validate(r)
    assert result.is_valid is False
    assert "no validation schema registered" in result.errors[0]


def test_validation_never_mutates_payload():
    payload = {"id": 1, "player_name": "Test", "overall_rating": 200, "value_eur": 1000}
    original = dict(payload)
    r = _record("ea_fc26", "player", payload)
    validate(r)
    assert r.payload == original


def test_transfermarkt_valuation_valid():
    r = _record("transfermarkt_dataset", "valuation", {"player_id": 418560, "date": "2025-06-01", "market_value_in_eur": 200_000_000})
    result = validate(r)
    assert result.is_valid is True


def test_transfermarkt_valuation_zero_value_fails():
    r = _record("transfermarkt_dataset", "valuation", {"player_id": 418560, "date": "2025-06-01", "market_value_in_eur": 0})
    result = validate(r)
    assert result.is_valid is False


def test_optional_field_absent_is_fine():
    r = _record("transfermarkt_dataset", "appearance", {"player_id": 1, "game_date": "2025-08-16"})
    result = validate(r)
    assert result.is_valid is True


def test_validate_batch_yields_result_per_record():
    records = [
        _record("ea_fc26", "player", {"id": 1, "player_name": "A", "overall_rating": 80, "value_eur": 1000}),
        _record("ea_fc26", "player", {"id": 2, "player_name": "B"}),  # invalid
    ]
    results = list(validate_batch(records))
    assert len(results) == 2
    assert results[0].is_valid is True
    assert results[1].is_valid is False
