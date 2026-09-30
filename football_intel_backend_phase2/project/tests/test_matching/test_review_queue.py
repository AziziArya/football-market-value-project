from datetime import date

import pytest

from matching.identity import match_one
from matching.review_queue import ReviewQueue
from matching.schema import IdentityInput


def ea(name, dob, nat, club, ext="1"):
    return IdentityInput(source="ea_fc26", source_record_id=ext, external_id=ext,
                          display_name=name, date_of_birth=dob, nationality=nat, club=club, position=None)


def tm(name, dob, nat, club, ext="100"):
    return IdentityInput(source="transfermarkt_dataset", source_record_id=ext, external_id=ext,
                          display_name=name, date_of_birth=dob, nationality=nat, club=club, position=None)


def _ambiguous_result():
    query = ea("John Smith", date(1999, 1, 1), "England", "Club A")
    cand1 = tm("John Smith", date(1999, 1, 1), "England", "Club B", ext="201")
    cand2 = tm("John Smith", date(1999, 1, 1), "England", "Club C", ext="202")
    return match_one(query, [cand1, cand2])


def test_ingest_only_collects_ambiguous():
    matched = match_one(
        ea("Erling Haaland", date(2000, 7, 21), "Norway", "Manchester City"),
        [tm("Erling Haaland", date(2000, 7, 21), "Norway", "Manchester City")],
    )
    ambiguous = _ambiguous_result()

    q = ReviewQueue()
    added = q.ingest([matched, ambiguous])
    assert added == 1
    assert len(q.pending()) == 1
    assert q.pending()[0].query.display_name == "John Smith"


def test_resolve_requires_candidate_from_the_actual_result():
    q = ReviewQueue()
    result = _ambiguous_result()
    q.ingest([result])

    fake_candidate = match_one(
        ea("Someone Else", date(1980, 1, 1), "France", "Nowhere"), []
    ).best

    with pytest.raises(ValueError):
        q.resolve(result, fake_candidate, "trying to sneak in an uninvestigated candidate")


def test_resolve_removes_item_from_pending():
    q = ReviewQueue()
    result = _ambiguous_result()
    q.ingest([result])
    assert len(q.pending()) == 1

    chosen = result.all_candidates[0]
    decision = q.resolve(result, chosen, "checked transfermarkt manually, candidate 201 is correct")

    assert len(q.pending()) == 0
    assert len(q.resolved()) == 1
    assert decision.chosen_candidate == chosen
    assert decision.reviewer_note.startswith("checked")


def test_resolve_can_confirm_no_match():
    q = ReviewQueue()
    result = _ambiguous_result()
    q.ingest([result])
    decision = q.resolve(result, None, "neither candidate is actually this player")
    assert decision.chosen_candidate is None
    assert len(q.pending()) == 0


def test_queue_never_auto_resolves_on_ingest():
    q = ReviewQueue()
    result = _ambiguous_result()
    q.ingest([result])
    # nothing should be in `resolved()` until a human explicitly calls resolve()
    assert q.resolved() == []
