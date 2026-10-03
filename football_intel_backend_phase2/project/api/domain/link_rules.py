"""Link-state derivation (contract LinkState). Pure."""
from __future__ import annotations

from api.domain.enums import MatchStatus

LINKED = (MatchStatus.MATCHED, MatchStatus.PROBABLE_MATCH)


def derive_link_state(status: str | None, confidence: float | None, matched_on: str | None):
    """Returns (status, confidence, matched_on). No stored result -> NOT_EVALUATED (never guessed).
    confidence/matched_on are exposed ONLY for MATCHED/PROBABLE_MATCH: AMBIGUOUS/UNMATCHED rows can carry
    misleading scores (UNMATCHED stores 0.0) or reveal candidates."""
    if status is None:
        return MatchStatus.NOT_EVALUATED, None, None
    st = MatchStatus(status)            # ValueError on an unknown status -> integrity problem, never swallowed
    if st not in LINKED:
        return st, None, None
    signals = tuple(s for s in (matched_on or "").split(",") if s)
    return st, (round(float(confidence), 4) if confidence is not None else None), signals
