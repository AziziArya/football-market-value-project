from pathlib import Path

import pytest

from ingestion.providers.base import RawRecord
from ingestion.providers.wikidata import WikidataProvider

FIXTURE_PATH = Path(__file__).parents[2] / "data" / "raw" / "wikidata" / "sample" / "players.csv"


@pytest.fixture
def provider():
    return WikidataProvider(fixture_path=FIXTURE_PATH)


def test_health_check_true_with_fixture(provider):
    assert provider.health_check() is True


def test_health_check_false_without_fixture_or_live_mode():
    p = WikidataProvider(fixture_path=None)
    assert p.health_check() is False  # honest — live mode not implemented here, never fake True


def test_health_check_false_missing_fixture_file(tmp_path):
    p = WikidataProvider(fixture_path=tmp_path / "nope.csv")
    assert p.health_check() is False


def test_fetch_raises_clearly_when_unusable(tmp_path):
    p = WikidataProvider(fixture_path=tmp_path / "nope.csv")
    with pytest.raises(FileNotFoundError, match="not network-reachable"):
        list(p.fetch())


def test_fetch_yields_5_records(provider):
    records = list(provider.fetch())
    assert len(records) == 5
    assert all(isinstance(r, RawRecord) for r in records)
    assert all(r.source == "wikidata" and r.record_type == "player" for r in records)


def test_dataset_version_is_live_tagged(provider):
    records = list(provider.fetch())
    assert all(r.dataset_version.startswith("live:") for r in records)


def test_real_verified_haaland_record(provider):
    records = {r.source_record_id: r for r in provider.fetch()}
    haaland = records["Q28967995"]
    assert haaland.payload["name"] == "Erling Haaland"
    assert haaland.payload["date_of_birth"] == "2000-07-21"
    assert haaland.payload["country_of_citizenship"] == "Norway"
    assert haaland.payload["image_attribution"] == "Bryan Berlin"


def test_illustrative_records_have_no_image_data(provider):
    """Rows 2-5 in the fixture are explicitly illustrative-only for
    identity fields; image fields must stay NULL, never guessed."""
    records = {r.source_record_id: r for r in provider.fetch()}
    for qid in ("Q00000001", "Q00000002", "Q00000003", "Q00000004"):
        payload = records[qid].payload
        assert payload.get("image_filename") is None
        assert payload.get("image_license") is None
        assert payload.get("image_attribution") is None


def test_no_network_calls_are_made(provider, monkeypatch):
    import socket

    def _blocked(*args, **kwargs):
        raise AssertionError("WikidataProvider (fixture mode) must never open a network connection")

    monkeypatch.setattr(socket, "socket", _blocked)
    records = list(provider.fetch())
    assert len(records) == 5
