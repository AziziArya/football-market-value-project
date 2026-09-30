"""
Transfermarkt dataset provider — source 1 in the approved architecture.

This is a STATIC HISTORICAL SOURCE ONLY:
- reads a local dump of the CC0-1.0-licensed dcaribou/transfermarkt-datasets
  project (https://github.com/dcaribou/transfermarkt-datasets)
- NO live scraping of transfermarkt.com — this class makes zero network
  calls, ever. `health_check()` only looks at local files.
- the dump is treated as a frozen snapshot: `dataset_version` is the
  snapshot date, not "now". Downstream code must never describe this data
  as "live Transfermarkt data".

Expected files in `dump_dir` (real column names, matching the source
project's own dbt models — see data/raw/transfermarkt_dataset/*/README.md):
    players.csv            player_id, name, date_of_birth,
                            country_of_citizenship, sub_position,
                            current_club_name
    player_valuations.csv  player_id, date, market_value_in_eur
    transfers.csv           player_id, transfer_date, from_club_name,
                            to_club_name, transfer_fee, is_loan
    appearances.csv         player_id, game_date, competition,
                            minutes_played, goals, assists

Any subset of these files may be present; missing files are skipped
(logged, not fabricated) rather than raising, since a partial dump is
still useful — but health_check() requires at least players.csv.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pandas as pd

from ingestion.providers.base import RawRecord, StaticDatasetProvider

REQUIRED_FILE = "players.csv"
KNOWN_FILES = {
    "players.csv": "player",
    "player_valuations.csv": "valuation",
    "transfers.csv": "transfer",
    "appearances.csv": "appearance",
}


@dataclass
class TransfermarktDatasetProvider(StaticDatasetProvider):
    """Source 1: static CC0 Transfermarkt-derived dataset."""

    name: str = "transfermarkt_dataset"
    dataset_version: str | None = None  # e.g. '2026-07-06'; defaults to dump_dir folder name

    def _version(self) -> str:
        return self.dataset_version or self.dump_dir.name

    def health_check(self) -> bool:
        return (self.dump_dir / REQUIRED_FILE).exists()

    def fetch(self, **kwargs) -> Iterator[RawRecord]:
        if not self.health_check():
            raise FileNotFoundError(
                f"Transfermarkt dataset not found at {self.dump_dir} "
                f"(missing required {REQUIRED_FILE}). Refusing to fabricate records — "
                "see this folder's README.md for how to obtain the real dump."
            )

        fetched_at = datetime.now(timezone.utc)
        version = self._version()

        for filename, record_type in KNOWN_FILES.items():
            file_path = self.dump_dir / filename
            if not file_path.exists():
                continue  # partial dump is fine; we don't invent missing files

            df = pd.read_csv(file_path)
            for idx, row in df.iterrows():
                payload = {k: (None if pd.isna(v) else v) for k, v in row.items()}
                source_record_id = self._record_id(record_type, payload, idx)
                yield RawRecord(
                    source=self.name,
                    source_record_id=source_record_id,
                    record_type=record_type,
                    payload=payload,
                    fetched_at=fetched_at,
                    dataset_version=version,
                )

    @staticmethod
    def _record_id(record_type: str, payload: dict, row_idx: int) -> str:
        player_id = payload.get("player_id")
        if record_type == "player":
            return str(player_id)
        if record_type == "valuation":
            return f"{player_id}:{payload.get('date')}"
        if record_type == "transfer":
            return f"{player_id}:{payload.get('transfer_date')}:{row_idx}"
        if record_type == "appearance":
            return f"{player_id}:{payload.get('game_date')}:{row_idx}"
        return f"{record_type}:{row_idx}"

    def available_record_types(self) -> list[str]:
        """Which of the 4 known files are actually present in this dump."""
        return [rt for fn, rt in KNOWN_FILES.items() if (self.dump_dir / fn).exists()]
