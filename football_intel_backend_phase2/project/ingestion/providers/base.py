"""
Provider abstraction. Every data source implements this interface.
Core app (matching/, ml/, api/) never imports a concrete provider directly —
it only ever consumes RawRecord objects yielded by fetch(), after they've
passed through ingestion/validator.py and ingestion/normalizer.py.

Adding a 7th data source later = one new file here, zero changes anywhere else.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class RawRecord:
    """One untouched record from a source, before validation/normalization."""

    source: str                       # e.g. 'ea_fc26', 'transfermarkt_dataset', 'wikidata'
    source_record_id: str             # id of this record within its own source
    record_type: str                  # 'player' | 'valuation' | 'transfer' | 'appearance' | 'image'
    payload: dict[str, Any]           # raw fields, source's own naming, untouched
    fetched_at: datetime
    dataset_version: str | None = None   # for static dumps (e.g. '2026-07-06' snapshot date)


class Provider(ABC):
    """Abstract base every concrete provider must implement."""

    name: str

    @abstractmethod
    def fetch(self, **kwargs) -> Iterator[RawRecord]:
        """Yield RawRecord objects. No validation or normalization here —
        that is validator.py / normalizer.py's job, kept separate so a
        provider can be tested in isolation from the rest of the pipeline."""
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> bool:
        """True if the source is currently usable (file present / API reachable)."""
        raise NotImplementedError


@dataclass
class StaticDatasetProvider(Provider):
    """
    Base for sources that are a static, already-fetched dump living on disk
    (source 1: transfermarkt_dataset). No network calls happen at runtime —
    fetch() only reads local files. This is what keeps us off the "live
    scraping" path entirely for this source.
    """

    dump_dir: Path = field(default_factory=Path)

    def health_check(self) -> bool:
        return self.dump_dir.exists() and any(self.dump_dir.iterdir())


class APIProvider(Provider):
    """
    Base for sources reached over the network at runtime
    (wikidata, thesportsdb, football_data). Concrete subclasses must
    respect rate_limit_per_min and never bypass auth/robots restrictions.
    """

    base_url: str
    rate_limit_per_min: int = 30

    @abstractmethod
    def _request(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        raise NotImplementedError
