"""
Identity matching engine.

Scores candidate pairs of players across sources using name + DOB +
nationality + club (position is a secondary/tie-break signal only, per
requirements). Never merges destructively — every candidate considered
is kept in MatchResult.all_candidates, and only MATCHED/PROBABLE_MATCH
results are meant to populate `players` directly; AMBIGUOUS goes to the
review queue; UNMATCHED means "no player record created from this pair".

Design is explainable by construction: every MatchCandidate carries its
per-signal sub-scores (`signals`) and which signals contributed
(`matched_on`), so a human reviewing an AMBIGUOUS case sees exactly why.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Iterable

from ingestion.providers.base import RawRecord
from matching.schema import IdentityInput, MatchCandidate, MatchResult, MatchThresholds

DEFAULT_THRESHOLDS = MatchThresholds()


# ═══════════════════════════════════════════════════════════
# normalization helpers
# ═══════════════════════════════════════════════════════════

def normalize_text(value: str | None) -> str:
    """Lowercase, strip accents/diacritics, drop non-letters, collapse whitespace."""
    if not value:
        return ""
    n = unicodedata.normalize("NFKD", value)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[^a-zA-Z\s]", " ", n)
    n = re.sub(r"\s+", " ", n).strip().lower()
    return n


def text_similarity(a: str | None, b: str | None) -> float:
    """0..1 similarity between two free-text strings (names, club names, nationalities)."""
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def dob_signal(a: date | None, b: date | None) -> tuple[float, bool]:
    """Returns (score, conflict). score=1.0 if exact match, 0.0 if either
    missing (no penalty, no bonus — just no signal). conflict=True only
    when BOTH are present and they differ — that's a strong "probably not
    the same person" signal, handled specially by classify()."""
    if a is None or b is None:
        return 0.0, False
    if a == b:
        return 1.0, False
    return 0.0, True


# ═══════════════════════════════════════════════════════════
# adapters: raw provider records -> IdentityInput
# ═══════════════════════════════════════════════════════════

def _parse_ea_birthdate(raw: str | None) -> date | None:
    if not raw:
        return None
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(str(raw), fmt).date()
        except ValueError:
            continue
    return None


def _parse_tm_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def from_ea_record(record: RawRecord) -> IdentityInput:
    assert record.source == "ea_fc26" and record.record_type == "player"
    p = record.payload
    return IdentityInput(
        source="ea_fc26",
        source_record_id=record.source_record_id,
        external_id=str(p["id"]),
        display_name=str(p.get("player_name") or ""),
        date_of_birth=_parse_ea_birthdate(p.get("birthdate")),
        nationality=p.get("nationality"),
        club=p.get("team"),
        position=p.get("position"),
    )


def from_transfermarkt_record(record: RawRecord) -> IdentityInput:
    assert record.source == "transfermarkt_dataset" and record.record_type == "player"
    p = record.payload
    return IdentityInput(
        source="transfermarkt_dataset",
        source_record_id=record.source_record_id,
        external_id=str(p["player_id"]),
        display_name=str(p.get("name") or ""),
        date_of_birth=_parse_tm_date(p.get("date_of_birth")),
        nationality=p.get("country_of_citizenship"),
        club=p.get("current_club_name"),
        position=p.get("sub_position"),
    )


def from_wikidata_record(record: RawRecord) -> IdentityInput:
    """Wikidata carries no club/position per the approved priority-field
    list — left None, never guessed. score_pair() already handles missing
    club/position gracefully (0 contribution, no penalty)."""
    assert record.source == "wikidata" and record.record_type == "player"
    p = record.payload
    return IdentityInput(
        source="wikidata",
        source_record_id=record.source_record_id,
        external_id=record.source_record_id,  # QID itself is the external id
        display_name=str(p.get("name") or ""),
        date_of_birth=_parse_tm_date(p.get("date_of_birth")),  # same YYYY-MM-DD shape
        nationality=p.get("country_of_citizenship"),
        club=None,
        position=None,
    )


# ═══════════════════════════════════════════════════════════
# scoring
# ═══════════════════════════════════════════════════════════

def score_pair(a: IdentityInput, b: IdentityInput, thresholds: MatchThresholds = DEFAULT_THRESHOLDS) -> MatchCandidate:
    name_score = text_similarity(a.display_name, b.display_name)
    dob_score, dob_conflict = dob_signal(a.date_of_birth, b.date_of_birth)
    nat_score = text_similarity(a.nationality, b.nationality)
    club_score = text_similarity(a.club, b.club)

    signals = {"name": name_score, "dob": dob_score, "nationality": nat_score, "club": club_score}

    weighted_non_dob = (
        name_score * thresholds.weight_name
        + nat_score * thresholds.weight_nationality
        + club_score * thresholds.weight_club
    )
    # rescale non-dob weights to their own 0..1 range, for use when dob is
    # unavailable/conflicting and we need a "how strong is everything else" figure
    non_dob_weight_total = thresholds.weight_name + thresholds.weight_nationality + thresholds.weight_club
    non_dob_score = weighted_non_dob / non_dob_weight_total if non_dob_weight_total > 0 else 0.0

    total_score = weighted_non_dob + dob_score * thresholds.weight_dob

    matched_on = []
    if name_score >= 0.5:
        matched_on.append("name")
    if dob_score == 1.0:
        matched_on.append("dob")
    if nat_score >= 0.8:
        matched_on.append("nationality")
    if club_score >= 0.8:
        matched_on.append("club")

    status, reason = _classify(total_score, non_dob_score, dob_conflict, thresholds)

    return MatchCandidate(
        ea_fc26_id=a.external_id if a.source == "ea_fc26" else (b.external_id if b.source == "ea_fc26" else None),
        transfermarkt_id=a.external_id if a.source == "transfermarkt_dataset" else (b.external_id if b.source == "transfermarkt_dataset" else None),
        wikidata_id=a.external_id if a.source == "wikidata" else (b.external_id if b.source == "wikidata" else None),
        confidence=round(total_score, 4),
        signals=signals,
        matched_on=matched_on,
        status=status,
        reason=reason,
    )


def _classify(
    total_score: float,
    non_dob_score: float,
    dob_conflict: bool,
    thresholds: MatchThresholds,
) -> tuple[str, str]:
    if dob_conflict:
        # DOB present on both sides and disagrees — strong "different person"
        # signal, UNLESS everything else (name/nationality/club) agrees so
        # strongly it's worth a human's eyes (e.g. a data-entry DOB typo).
        if non_dob_score >= thresholds.dob_conflict_review_floor:
            return "AMBIGUOUS", "dob_conflict_despite_high_similarity_elsewhere"
        return "UNMATCHED", "dob_conflict"

    if total_score >= thresholds.matched:
        return "MATCHED", "high_confidence_multi_signal_match"
    if total_score >= thresholds.probable:
        return "PROBABLE_MATCH", "moderate_confidence_match"
    if total_score >= thresholds.ambiguous:
        return "AMBIGUOUS", "low_confidence_needs_review"
    return "UNMATCHED", "insufficient_similarity"


# ═══════════════════════════════════════════════════════════
# blocking (candidate generation) — keeps full pairwise comparison
# from being O(n*m) at real scale. swappable: pass a different
# blocking_key_fn to match_all() without touching scoring logic.
# ═══════════════════════════════════════════════════════════

def block_by_name_prefix(identity: IdentityInput) -> str | None:
    """First 3 letters of the FIRST name token. Survives nickname/suffix
    variation ('Vini Jr.' vs 'Vinicius Junior') that broke last-token
    blocking — see the regression test for that real bug."""
    tokens = normalize_text(identity.display_name).split()
    if not tokens:
        return None
    return tokens[0][:3] if len(tokens[0]) >= 3 else tokens[0]


def block_by_nationality(identity: IdentityInput) -> str | None:
    """Catches cases where name normalization diverges a lot (transliteration,
    heavily different nicknames) but nationality is recorded consistently."""
    key = normalize_text(identity.nationality)
    return key or None


def block_by_birth_year(identity: IdentityInput) -> str | None:
    """DOB is often the most reliable field across sources even when name
    and club differ significantly."""
    if identity.date_of_birth is None:
        return None
    return f"year:{identity.date_of_birth.year}"


def block_by_club(identity: IdentityInput) -> str | None:
    """Catches cases where a nickname AND a transliterated nationality both
    diverge, but current club is recorded the same way in both sources."""
    key = normalize_text(identity.club)
    return key or None


DEFAULT_BLOCKING_FNS = (block_by_name_prefix, block_by_nationality, block_by_birth_year, block_by_club)


def default_blocking_key(identity: IdentityInput) -> str:
    """Kept for backward compatibility / single-key use cases (e.g. tests
    that want one deterministic key). match_all() uses DEFAULT_BLOCKING_FNS
    (the union of all four) by default, not just this one."""
    return block_by_name_prefix(identity) or "?"


def match_one(
    query: IdentityInput,
    candidates: Iterable[IdentityInput],
    thresholds: MatchThresholds = DEFAULT_THRESHOLDS,
) -> MatchResult:
    scored = [score_pair(query, c, thresholds) for c in candidates]
    if not scored:
        empty_candidate = MatchCandidate(
            ea_fc26_id=query.external_id if query.source == "ea_fc26" else None,
            transfermarkt_id=query.external_id if query.source == "transfermarkt_dataset" else None,
            wikidata_id=query.external_id if query.source == "wikidata" else None,
            confidence=0.0,
            signals={},
            matched_on=[],
            status="UNMATCHED",
            reason="no_candidates_available",
        )
        return MatchResult(query=query, best=empty_candidate, all_candidates=[])

    scored.sort(key=lambda c: c.confidence, reverse=True)
    best = scored[0]

    # ambiguity-margin rule: if the runner-up is nearly as good, we can't be
    # sure which is right — downgrade even an otherwise-confident match.
    if len(scored) > 1 and best.status in ("MATCHED", "PROBABLE_MATCH"):
        runner_up = scored[1]
        if (best.confidence - runner_up.confidence) < thresholds.ambiguity_margin:
            downgraded = MatchCandidate(
                ea_fc26_id=best.ea_fc26_id,
                transfermarkt_id=best.transfermarkt_id,
                wikidata_id=best.wikidata_id,
                confidence=best.confidence,
                signals=best.signals,
                matched_on=best.matched_on,
                status="AMBIGUOUS",
                reason="multiple_close_candidates",
            )
            scored[0] = downgraded  # keep all_candidates[0] consistent with `best`
            best = downgraded

    return MatchResult(query=query, best=best, all_candidates=scored)


def build_identity_input_for_player(con, player_uid: str) -> IdentityInput:
    """Reconstructs an IdentityInput for an already-canonical player (a row
    in `players`), so wikidata enrichment can reuse score_pair()/match_one()
    unchanged rather than inventing separate matching logic. Prefers
    ea_fc26-sourced field values, falling back to transfermarkt_dataset's
    when EA doesn't have a field — never fabricates a value neither has."""
    row = con.execute(
        "select player_uid, ea_fc26_id, display_name from players where player_uid = ?", [player_uid]
    ).fetchone()
    if row is None:
        raise ValueError(f"no player with player_uid {player_uid!r}")
    _, ea_fc26_id, display_name = row

    def _field(field_name: str) -> str | None:
        for source in ("ea_fc26", "transfermarkt_dataset"):
            result = con.execute(
                "select field_value from player_field_values where player_uid = ? and source = ? and field_name = ? limit 1",
                [player_uid, source, field_name],
            ).fetchone()
            if result is not None:
                return result[0]
        return None

    dob_str = _field("date_of_birth")
    dob = _parse_ea_birthdate(dob_str) or _parse_tm_date(dob_str)

    return IdentityInput(
        source="ea_fc26",  # tags this as the canonical-player side for score_pair's id assignment
        source_record_id=str(ea_fc26_id) if ea_fc26_id is not None else player_uid,
        external_id=str(ea_fc26_id) if ea_fc26_id is not None else player_uid,
        display_name=display_name,
        date_of_birth=dob,
        nationality=_field("nationality"),
        club=_field("club"),
        position=_field("position"),
    )


def match_all(
    ea_identities: list[IdentityInput],
    other_identities: list[IdentityInput],
    thresholds: MatchThresholds = DEFAULT_THRESHOLDS,
    blocking_fns=DEFAULT_BLOCKING_FNS,
) -> list[MatchResult]:
    """Match every EA identity against candidates from another source.

    Uses MULTI-KEY blocking: a candidate is considered if it shares ANY
    one of several blocking keys with the query (name-prefix OR
    nationality OR birth-year OR club), not just one. This increases
    recall — a true match only needs to agree on one of these — while
    leaving classification completely untouched (more candidates
    considered is not the same as looser matching; score_pair() and the
    confidence thresholds still decide the outcome, so this cannot by
    itself create a false merge).

    Pass a custom `blocking_fns` tuple to tune blocking without touching
    scoring logic at all."""
    # one index per blocking function: {(fn_index, key): [candidates]}
    indexes: list[dict[str, list[IdentityInput]]] = [defaultdict(list) for _ in blocking_fns]
    for other in other_identities:
        for fn_index, fn in enumerate(blocking_fns):
            key = fn(other)
            if key is not None:
                indexes[fn_index][key].append(other)

    results = []
    for ea in ea_identities:
        candidates_by_id: dict[str, IdentityInput] = {}
        for fn_index, fn in enumerate(blocking_fns):
            key = fn(ea)
            if key is None:
                continue
            for candidate in indexes[fn_index].get(key, []):
                candidates_by_id[candidate.external_id] = candidate
        results.append(match_one(ea, list(candidates_by_id.values()), thresholds))
    return results
