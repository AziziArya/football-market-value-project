from datetime import datetime
from pathlib import Path

import pytest

from ingestion.providers.base import RawRecord
from ingestion.providers.transfermarkt_dataset import TransfermarktDatasetProvider

SAMPLE_DIR = Path(__file__).parents[2] / "data" / "raw" / "transfermarkt_dataset" / "sample_2026-07-06"


@pytest.fixture
def provider():
    return TransfermarktDatasetProvider(dump_dir=SAMPLE_DIR)


def test_health_check_true_when_players_csv_present(provider):
    assert provider.health_check() is True


def test_health_check_false_when_dump_dir_missing(tmp_path):
    p = TransfermarktDatasetProvider(dump_dir=tmp_path / "nope")
    assert p.health_check() is False


def test_health_check_false_when_players_csv_missing(tmp_path):
    (tmp_path / "transfers.csv").write_text("player_id\n1\n")
    p = TransfermarktDatasetProvider(dump_dir=tmp_path)
    assert p.health_check() is False


def test_no_fabrication_when_required_file_missing(tmp_path):
    p = TransfermarktDatasetProvider(dump_dir=tmp_path)
    with pytest.raises(FileNotFoundError):
        list(p.fetch())


def test_available_record_types_detects_all_four(provider):
    assert set(provider.available_record_types()) == {"player", "valuation", "transfer", "appearance"}


def test_available_record_types_partial_dump(tmp_path):
    (tmp_path / "players.csv").write_text("player_id,name\n1,Test Player\n")
    p = TransfermarktDatasetProvider(dump_dir=tmp_path)
    assert p.available_record_types() == ["player"]
    # partial dump must not raise — missing files are skipped, not invented
    records = list(p.fetch())
    assert len(records) == 1
    assert records[0].record_type == "player"


def test_fetch_yields_all_record_types(provider):
    records = list(provider.fetch())
    types_seen = {r.record_type for r in records}
    assert types_seen == {"player", "valuation", "transfer", "appearance"}


def test_fetch_counts_match_fixture(provider):
    records = list(provider.fetch())
    counts = {}
    for r in records:
        counts[r.record_type] = counts.get(r.record_type, 0) + 1
    assert counts == {"player": 5, "valuation": 15, "transfer": 3, "appearance": 6}


def test_dataset_version_defaults_to_folder_name(provider):
    records = list(provider.fetch())
    assert all(r.dataset_version == "sample_2026-07-06" for r in records)


def test_dataset_version_explicit_override(tmp_path):
    (tmp_path / "players.csv").write_text("player_id,name\n1,Test Player\n")
    p = TransfermarktDatasetProvider(dump_dir=tmp_path, dataset_version="2099-01-01")
    records = list(p.fetch())
    assert records[0].dataset_version == "2099-01-01"


def test_player_record_id_is_player_id(provider):
    records = [r for r in provider.fetch() if r.record_type == "player"]
    ids = {r.source_record_id for r in records}
    assert "418560" in ids  # Haaland, real transfermarkt_id


def test_valuation_record_id_is_composite(provider):
    records = [r for r in provider.fetch() if r.record_type == "valuation"]
    assert all(":" in r.source_record_id for r in records)


def test_raw_record_is_immutable(provider):
    record = next(provider.fetch())
    with pytest.raises(Exception):
        record.payload = {}  # frozen dataclass — attribute assignment must fail


def test_all_records_are_raw_record_instances(provider):
    assert all(isinstance(r, RawRecord) for r in provider.fetch())


def test_fetched_at_is_recent_utc(provider):
    record = next(provider.fetch())
    assert record.fetched_at.tzinfo is not None
    assert (datetime.now(record.fetched_at.tzinfo) - record.fetched_at).total_seconds() < 60


def test_no_network_calls_are_made(provider, monkeypatch):
    """Guard against regressions that would turn this into a live scraper."""
    import socket

    def _blocked(*args, **kwargs):
        raise AssertionError("TransfermarktDatasetProvider must never open a network connection")

    monkeypatch.setattr(socket, "socket", _blocked)
    records = list(provider.fetch())
    assert len(records) == 29
