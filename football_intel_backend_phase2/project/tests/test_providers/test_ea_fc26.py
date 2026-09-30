from datetime import datetime

import pytest

from ingestion.providers.base import RawRecord
from ingestion.providers.ea_fc26 import EAFC26Provider


@pytest.fixture
def provider():
    return EAFC26Provider()


def test_health_check_passes_when_csv_present(provider):
    assert provider.health_check() is True


def test_fetch_yields_expected_row_count(provider):
    records = list(provider.fetch())
    # Phase 0 audit confirmed 16,107 rows in fc26_merged_clean.csv
    assert len(records) == 16107


def test_fetch_yields_raw_record_objects(provider):
    records = list(provider.fetch())
    first = records[0]
    assert isinstance(first, RawRecord)
    assert first.source == "ea_fc26"
    assert first.record_type == "player"
    assert isinstance(first.fetched_at, datetime)
    assert first.dataset_version == "2025-09-19"


def test_source_record_id_matches_payload_id(provider):
    records = list(provider.fetch())
    first = records[0]
    assert first.source_record_id == str(first.payload["id"])


def test_value_field_is_labelled_ingame_not_generic(provider):
    """Critical rule from the approved architecture: EA in-game value must
    never be exposed as a bare 'value' — it has to stay distinguishable
    from real market value (source 1) and ML predicted value downstream.
    This provider yields the raw column name as-is (normalizer.py does the
    ingame-labelling rename); this test just locks in that the raw column
    is 'value_eur', so normalizer.py has a stable contract to rename from.
    """
    records = list(provider.fetch())
    assert "value_eur" in records[0].payload
    assert "value_eur_ingame" not in records[0].payload  # renaming is normalizer's job, not this provider's


def test_no_fabrication_when_file_missing(tmp_path):
    missing = EAFC26Provider(csv_path=tmp_path / "does_not_exist.csv")
    assert missing.health_check() is False
    with pytest.raises(FileNotFoundError):
        list(missing.fetch())


def test_known_player_present(provider):
    """Sanity check against a player we already know is in the audited baseline."""
    records = list(provider.fetch())
    ids = {r.source_record_id for r in records}
    # Salah's EA/sofifa-style id, confirmed present during Phase 0 audit
    assert "209331" in ids
