"""
Review queue for AMBIGUOUS match results.

This module NEVER decides a match on its own. It only:
  1. collects AMBIGUOUS MatchResults for a human to look at
  2. presents them with full explainability (signals, matched_on, all
     candidates considered — not just the top one)
  3. records a human's decision as an explicit, separate action

Resolving an item does not silently write into the players table —
the caller must explicitly pass the resolution to
matching/loader.py::create_player_records(). This module just tracks
"what did the human decide", nothing more.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from matching.schema import MatchCandidate, MatchResult


@dataclass(frozen=True)
class ReviewDecision:
    query_source: str
    query_source_record_id: str
    chosen_candidate: MatchCandidate | None   # None means "reviewer says: no match, keep unmatched"
    reviewer_note: str
    decided_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ReviewQueue:
    """In-memory queue of AMBIGUOUS results. A persistence layer (loader.py)
    can snapshot .pending() into identity_matches for durability, but the
    queue itself doesn't require a DB to be usable/testable."""

    def __init__(self):
        self._items: list[MatchResult] = []
        self._decisions: dict[str, ReviewDecision] = {}

    def ingest(self, results: list[MatchResult]) -> int:
        """Add every AMBIGUOUS result to the queue. Returns count added.
        Non-ambiguous results are ignored here (they don't need review)."""
        added = 0
        for result in results:
            if result.best is not None and result.best.status == "AMBIGUOUS":
                self._items.append(result)
                added += 1
        return added

    def pending(self) -> list[MatchResult]:
        """Ambiguous items with no recorded decision yet."""
        return [r for r in self._items if self._key(r) not in self._decisions]

    def resolved(self) -> list[ReviewDecision]:
        return list(self._decisions.values())

    def resolve(
        self,
        result: MatchResult,
        chosen_candidate: MatchCandidate | None,
        reviewer_note: str,
    ) -> ReviewDecision:
        """Record a human decision. chosen_candidate must be one of
        result.all_candidates (or None, meaning 'confirm unmatched') —
        never an arbitrary fabricated candidate."""
        if chosen_candidate is not None and chosen_candidate not in result.all_candidates:
            raise ValueError(
                "chosen_candidate must be one of the candidates that were actually "
                "scored for this query — cannot resolve to a candidate that was never considered"
            )
        decision = ReviewDecision(
            query_source=result.query.source,
            query_source_record_id=result.query.source_record_id,
            chosen_candidate=chosen_candidate,
            reviewer_note=reviewer_note,
        )
        self._decisions[self._key(result)] = decision
        return decision

    @staticmethod
    def _key(result: MatchResult) -> str:
        return f"{result.query.source}:{result.query.source_record_id}"
