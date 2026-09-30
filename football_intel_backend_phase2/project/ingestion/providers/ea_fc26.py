"""
EA FC26 provider.

Wraps the EXISTING, already-validated baseline dataset
(fc26_merged_clean.csv — produced by the original university notebook,
100% reproducibility-checked in Phase 0 audit against the 4 raw EA/FC26 files).

This provider is READ-ONLY:
- never writes to the source csv
- never recomputes anything the notebook already computed
- the notebook's `value_eur` is exposed as `value_eur_ingame`, never as
  a generic 'value' field, so it can never be confused with real market
  value (source 1) or ML predicted value downstream.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pandas as pd

from ingestion.providers.base import Provider, RawRecord

DEFAULT_CSV_PATH = Path(__file__).parents[2] / "data" / "raw" / "ea_fc26" / "fc26_merged_clean.csv"


class EAFC26Provider(Provider):
    """Source 5 in the approved architecture: existing EA FC26 dataset."""

    name = "ea_fc26"

    def __init__(self, csv_path: Path = DEFAULT_CSV_PATH):
        self.csv_path = csv_path

    def health_check(self) -> bool:
        return self.csv_path.exists() and self.csv_path.stat().st_size > 0

    def fetch(self, **kwargs) -> Iterator[RawRecord]:
        if not self.health_check():
            raise FileNotFoundError(
                f"EA FC26 baseline csv not found at {self.csv_path}. "
                "This provider never fabricates data — refusing to yield records."
            )

        fetched_at = datetime.now(timezone.utc)
        # dataset_version = the file's own snapshot date, taken from the notebook's
        # documented export date, not a guess. Kept explicit rather than inferred
        # from mtime, since mtime changes on every copy/clone.
        dataset_version = "2025-09-19"  # matches FC26_20250921.csv naming / notebook narrative

        df = pd.read_csv(self.csv_path, low_memory=False)

        for _, row in df.iterrows():
            payload = {k: (None if pd.isna(v) else v) for k, v in row.items()}
            yield RawRecord(
                source=self.name,
                source_record_id=str(payload["id"]),
                record_type="player",
                payload=payload,
                fetched_at=fetched_at,
                dataset_version=dataset_version,
            )

    def row_count(self) -> int:
        """Convenience for tests/reporting — does not load full frame twice
        beyond what pandas needs."""
        return sum(1 for _ in open(self.csv_path, encoding="utf-8")) - 1
