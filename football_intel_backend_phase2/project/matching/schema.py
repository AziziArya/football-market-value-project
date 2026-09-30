"""
Data models for the identity matching engine.

Kept separate from identity.py (the scoring logic) so the shapes are
easy to inspect/import independently — e.g. by the review queue and by
tests — without pulling in the scoring implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone


VALID_STATUSES = {"MATCHED", "PROBABLE_MATCH", "AMBIGUOUS", "UNMATCHED"}


@dataclass(frozen=True)
class IdentityInput:
    """Minimal identity signals extracted from one source, for one player.
    Built by identity.py's from_ea_record()/from_transfermarkt_record()
    adapters — kept separate from normalizer.py's canonical output because
    matching needs a flatter, comparison-ready shape."""

    source: str
    source_record_id: str
    external_id: str           # the source's own native id (ea_fc26_id / transfermarkt_id / wikidata_id)
    display_name: str
    date_of_birth: date | None
    nationality: str | None
    club: str | None
    position: str | None


@dataclass(frozen=True)
class MatchThresholds:
    """Configurable, not buried in scoring logic. Pass a custom instance
    to match_one()/match_all() to tune behaviour without touching code."""

    matched: float = 0.90
    probable: float = 0.75
    ambiguous: float = 0.55
    ambiguity_margin: float = 0.05     # top-2 candidates closer than this -> forced AMBIGUOUS
    dob_conflict_review_floor: float = 0.75  # non-dob score above this, despite dob conflict -> AMBIGUOUS not UNMATCHED

    # per-signal weights, must sum to 1.0 (checked in __post_init__)
    weight_name: float = 0.40
    weight_dob: float = 0.35
    weight_nationality: float = 0.10
    weight_club: float = 0.15

    def __post_init__(self):
        total = self.weight_name + self.weight_dob + self.weight_nationality + self.weight_club
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"weights must sum to 1.0, got {total}")


@dataclass(frozen=True)
class MatchCandidate:
    """One scored (EA, other-source) pair. Never mutated after creation;
    a status change means creating a new one (e.g. margin-downgrade)."""

    ea_fc26_id: str | None
    transfermarkt_id: str | None
    wikidata_id: str | None
    confidence: float
    signals: dict[str, float]      # per-signal sub-scores, 0..1, for explainability
    matched_on: list[str]          # signal names that positively contributed
    status: str
    reason: str                    # short human-readable explanation

    def __post_init__(self):
        if self.status not in VALID_STATUSES:
            raise ValueError(f"invalid status {self.status!r}, must be one of {VALID_STATUSES}")


@dataclass(frozen=True)
class MatchResult:
    """Full outcome for one query player: the chosen best candidate (if any)
    plus every candidate considered, so nothing is thrown away silently."""

    query: IdentityInput
    best: MatchCandidate | None
    all_candidates: list[MatchCandidate] = field(default_factory=list)
    matched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
