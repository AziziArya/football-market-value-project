"""Plain immutable records passed between repositories and services. stdlib only."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from api.domain.enums import EntityKind, MatchStatus


@dataclass(frozen=True)
class PlayerIndexRow:
    """RM1: one row per EA player (read model, ARCHITECTURE_API.md section 5)."""
    ea_fc26_id: int
    display_name: str | None
    position: str | None
    club: str | None
    nationality: str | None
    date_of_birth: str | None
    preferred_foot: str | None
    overall_rating: int
    potential: int
    value_eur_ingame: int | None
    dataset_version: str | None
    fetched_at: datetime | None
    canonical_player_uid: str | None
    transfermarkt_id: int | None
    wikidata_id: str | None

    @property
    def entity_kind(self) -> EntityKind:
        return EntityKind.CANONICAL if self.canonical_player_uid is not None else EntityKind.EA_ONLY


@dataclass(frozen=True)
class LinkStateRow:
    """RM2: link state of one EA player to ONE external source. Candidate ids are never carried."""
    ea_fc26_id: int
    source: str                       # "transfermarkt" | "wikidata"
    status: MatchStatus
    confidence: float | None
    matched_on: tuple[str, ...] | None
    review_pending: bool


@dataclass(frozen=True)
class SourceFreshnessRow:
    source: str
    dataset_version: str | None
    last_fetched_at: datetime | None
    record_count: int


@dataclass(frozen=True)
class AliasRow:
    """RM3: a display_name of a CANONICAL player coming from a non-EA source (search only)."""
    ea_fc26_id: int
    value: str
    source: str


@dataclass(frozen=True)
class MarketValueRow:
    player_uid: str
    value_eur: int
    valuation_date: date
    source: str
    dataset_version: str | None
    imported_at: datetime | None


@dataclass(frozen=True)
class InjuryStateRow:
    player_uid: str
    status: str
    checked_at: datetime | None


@dataclass(frozen=True)
class ImageRow:
    player_uid: str
    url: str
    source: str
    license: str | None
    attribution: str | None
