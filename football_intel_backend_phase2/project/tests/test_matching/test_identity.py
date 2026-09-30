from datetime import date

import pytest

from matching.identity import match_all, match_one, score_pair, text_similarity, normalize_text
from matching.schema import IdentityInput, MatchThresholds


def ea(name, dob=None, nat=None, club=None, ext="1"):
    return IdentityInput(source="ea_fc26", source_record_id=ext, external_id=ext,
                          display_name=name, date_of_birth=dob, nationality=nat, club=club, position=None)


def tm(name, dob=None, nat=None, club=None, ext="100"):
    return IdentityInput(source="transfermarkt_dataset", source_record_id=ext, external_id=ext,
                          display_name=name, date_of_birth=dob, nationality=nat, club=club, position=None)


# ═══════════════════════════════════════════════════════════
# normalization / similarity primitives
# ═══════════════════════════════════════════════════════════

def test_normalize_text_strips_accents_and_case():
    assert normalize_text("Kylián MBAPPÉ") == "kylian mbappe"


def test_text_similarity_exact_match_is_1():
    assert text_similarity("Liverpool", "Liverpool") == 1.0


def test_text_similarity_empty_is_0():
    assert text_similarity("", "Liverpool") == 0.0
    assert text_similarity(None, "Liverpool") == 0.0


# ═══════════════════════════════════════════════════════════
# EXACT MATCH
# ═══════════════════════════════════════════════════════════

def test_exact_match_all_signals_agree():
    a = ea("Erling Haaland", date(2000, 7, 21), "Norway", "Manchester City")
    b = tm("Erling Haaland", date(2000, 7, 21), "Norway", "Manchester City")
    c = score_pair(a, b)
    assert c.status == "MATCHED"
    assert c.confidence >= 0.90
    assert set(c.matched_on) == {"name", "dob", "nationality", "club"}


def test_exact_match_preserves_both_source_ids():
    a = ea("Test Player", date(2000, 1, 1), ext="209331")
    b = tm("Test Player", date(2000, 1, 1), ext="418560")
    c = score_pair(a, b)
    assert c.ea_fc26_id == "209331"
    assert c.transfermarkt_id == "418560"


# ═══════════════════════════════════════════════════════════
# PROBABLE MATCH — strong signals but one weaker (recent transfer / nickname)
# ═══════════════════════════════════════════════════════════

def test_probable_match_when_club_differs_due_to_transfer():
    a = ea("Jude Bellingham", date(2003, 6, 29), "England", "Borussia Dortmund")
    b = tm("Jude Bellingham", date(2003, 6, 29), "England", "Real Madrid")  # transferred since EA snapshot
    c = score_pair(a, b)
    assert c.status == "PROBABLE_MATCH"
    assert "club" not in c.matched_on
    assert "name" in c.matched_on and "dob" in c.matched_on


def test_probable_match_nickname_vs_full_name():
    """Real case found in our own data: EA calls him 'Vini Jr.', the
    transfermarkt-style source calls him 'Vinicius Junior'."""
    a = ea("Vini Jr.", date(2000, 7, 12), "Brazil", "Real Madrid")
    b = tm("Vinicius Junior", date(2000, 7, 12), "Brazil", "Real Madrid")
    c = score_pair(a, b)
    assert c.status in ("PROBABLE_MATCH", "MATCHED")
    assert c.confidence >= 0.75
    assert "dob" in c.matched_on and "club" in c.matched_on


# ═══════════════════════════════════════════════════════════
# AMBIGUOUS
# ═══════════════════════════════════════════════════════════

def test_ambiguous_two_close_candidates_forces_downgrade():
    query = ea("John Smith", date(1999, 1, 1), "England", "Club A")
    cand1 = tm("John Smith", date(1999, 1, 1), "England", "Club B", ext="201")
    cand2 = tm("John Smith", date(1999, 1, 1), "England", "Club C", ext="202")
    result = match_one(query, [cand1, cand2])
    assert result.best.status == "AMBIGUOUS"
    assert result.best.reason == "multiple_close_candidates"
    assert len(result.all_candidates) == 2
    # regression guard: all_candidates[0] must be the SAME (downgraded) object
    # as `best` — a prior bug had these disagree in status after downgrade
    assert result.all_candidates[0].status == "AMBIGUOUS"
    assert result.all_candidates[0] == result.best


def test_ambiguous_dob_conflict_with_high_name_similarity():
    """Same name, same nationality, same club, but DOB disagrees —
    could be a data-entry error, needs a human, not an auto-decision."""
    a = ea("Carlos Silva", None, "Brazil", "Club X")
    a = ea("Carlos Silva", date(1995, 5, 5), "Brazil", "Club X")
    b = tm("Carlos Silva", date(1995, 5, 6), "Brazil", "Club X")  # one-day-off DOB, everything else matches
    c = score_pair(a, b)
    assert c.status == "AMBIGUOUS"
    assert c.reason == "dob_conflict_despite_high_similarity_elsewhere"


# ═══════════════════════════════════════════════════════════
# UNMATCHED
# ═══════════════════════════════════════════════════════════

def test_unmatched_completely_different_player():
    a = ea("Mohamed Salah", date(1992, 6, 15), "Egypt", "Liverpool")
    b = tm("Random Nobody", date(1988, 3, 3), "Iceland", "Some Club")
    c = score_pair(a, b)
    assert c.status == "UNMATCHED"


def test_unmatched_when_no_candidates_at_all():
    query = ea("Nobody Special", date(2000, 1, 1))
    result = match_one(query, [])
    assert result.best.status == "UNMATCHED"
    assert result.best.reason == "no_candidates_available"
    assert result.all_candidates == []


# ═══════════════════════════════════════════════════════════
# CONFLICTING IDENTITIES — same name, different person (homonym)
# ═══════════════════════════════════════════════════════════

def test_conflicting_identity_same_name_different_dob_and_nationality_stays_unmatched():
    a = ea("Diego Costa", date(1988, 10, 7), "Spain", "Atletico Madrid")
    b = tm("Diego Costa", date(2001, 2, 14), "Brazil", "Some Youth Club")  # homonym, clearly different person
    c = score_pair(a, b)
    assert c.status == "UNMATCHED"
    assert c.reason == "dob_conflict"


def test_conflicting_identity_never_silently_merges():
    """Even when a conflict is flagged, both original source ids must
    remain visible on the candidate — nothing is deleted."""
    a = ea("Diego Costa", date(1988, 10, 7), ext="111")
    b = tm("Diego Costa", date(2001, 2, 14), ext="999")
    c = score_pair(a, b)
    assert c.ea_fc26_id == "111"
    assert c.transfermarkt_id == "999"


# ═══════════════════════════════════════════════════════════
# configurable thresholds — not hardcoded deep in logic
# ═══════════════════════════════════════════════════════════

def test_thresholds_are_configurable():
    a = ea("Some Player", date(2000, 1, 1), "England", "Club Alpha")
    b = tm("Some Player", date(2000, 1, 1), "England", "Club Omega")
    default_result = score_pair(a, b)
    assert default_result.status == "MATCHED"  # ~0.94 under default thresholds

    strict = MatchThresholds(matched=0.99, probable=0.95, ambiguous=0.55)
    strict_result = score_pair(a, b, strict)
    assert strict_result.status == "AMBIGUOUS"  # same pair, stricter thresholds -> downgraded
    assert strict_result.confidence == default_result.confidence  # score itself doesn't change, only classification


def test_thresholds_reject_bad_weights():
    with pytest.raises(ValueError):
        MatchThresholds(weight_name=0.5, weight_dob=0.5, weight_nationality=0.5, weight_club=0.5)


# ═══════════════════════════════════════════════════════════
# match_all — batch matching with blocking
# ═══════════════════════════════════════════════════════════

def test_match_all_finds_correct_pairs_with_blocking():
    ea_list = [
        ea("Erling Haaland", date(2000, 7, 21), "Norway", "Manchester City", ext="1"),
        ea("Kylian Mbappe", date(1998, 12, 20), "France", "Real Madrid", ext="2"),
    ]
    tm_list = [
        tm("Erling Haaland", date(2000, 7, 21), "Norway", "Manchester City", ext="101"),
        tm("Kylian Mbappe", date(1998, 12, 20), "France", "Real Madrid", ext="102"),
        tm("Totally Different Guy", date(1970, 1, 1), "Iceland", "Nowhere FC", ext="103"),
    ]
    results = match_all(ea_list, tm_list)
    assert len(results) == 2
    haaland_result = [r for r in results if r.query.external_id == "1"][0]
    assert haaland_result.best.status == "MATCHED"
    assert haaland_result.best.transfermarkt_id == "101"


def test_match_all_blocking_survives_nickname_vs_full_name():
    """Regression test: caught during real-data validation. Last-token
    blocking ('jr' vs 'junior') used to hide this true match entirely —
    match_all() must still find it via first-token-prefix blocking."""
    ea_list = [ea("Vini Jr.", date(2000, 7, 12), "Brazil", "Real Madrid", ext="238794")]
    tm_list = [
        tm("Vinicius Junior", date(2000, 7, 12), "Brazil", "Real Madrid", ext="371998"),
        tm("Totally Different Guy", date(1970, 1, 1), "Iceland", "Nowhere FC", ext="999"),
    ]
    results = match_all(ea_list, tm_list)
    assert len(results) == 1
    assert results[0].best.status in ("PROBABLE_MATCH", "MATCHED")
    assert results[0].best.transfermarkt_id == "371998"


def test_multi_key_blocking_catches_match_even_when_name_prefix_diverges():
    """Name is transliterated so differently that the name-prefix block
    would miss it entirely — but nationality+DOB+club blocks catch it."""
    ea_list = [ea("Zh. Dinho", date(1990, 3, 3), "Argentina", "River Plate", ext="1")]
    tm_list = [tm("José Ricardo", date(1990, 3, 3), "Argentina", "River Plate", ext="500")]
    # name-prefix keys ('zho' vs 'jos') don't overlap at all — must be caught by
    # nationality/birth-year/club blocks instead
    from matching.identity import block_by_name_prefix
    assert block_by_name_prefix(ea_list[0]) != block_by_name_prefix(tm_list[0])

    results = match_all(ea_list, tm_list)
    assert results[0].best.transfermarkt_id == "500"
    assert results[0].best.status != "UNMATCHED"


def test_multi_key_blocking_does_not_change_classification_only_recall():
    """More candidates considered must not loosen thresholds — a clearly
    wrong candidate found via a shared nationality block must still be
    correctly classified UNMATCHED, not forced into a false merge."""
    ea_list = [ea("Erling Haaland", date(2000, 7, 21), "Norway", "Manchester City", ext="1")]
    tm_list = [tm("Completely Different Norwegian", date(1975, 1, 1), "Norway", "Rosenborg", ext="777")]
    results = match_all(ea_list, tm_list)
    assert results[0].best.status == "UNMATCHED"  # shared nationality block ≠ shared identity


def test_custom_blocking_fns_are_swappable():
    """blocking_fns is a parameter, not buried logic — a caller can pass
    a reduced or custom set without touching identity.py."""
    from matching.identity import block_by_name_prefix
    ea_list = [ea("Erling Haaland", date(2000, 7, 21), "Norway", "Manchester City", ext="1")]
    tm_list = [tm("Erling Haaland", date(2000, 7, 21), "Norway", "Manchester City", ext="101")]
    results = match_all(ea_list, tm_list, blocking_fns=(block_by_name_prefix,))
    assert results[0].best.status == "MATCHED"
