"""Pure search domain: differential test against an independent brute-force oracle + hand-built index cases."""
import itertools
import random

import pytest

from api.domain.enums import EntityKind, MatchQuality, SearchSort
from api.domain.models import AliasRow, PlayerIndexRow
from api.domain.search import SearchIndex, SearchQuery, has_distinct_prefix_assignment, match_quality
from tests.test_contract.test_search_spec import has_assignment as oracle_assignment, quality as oracle_quality


def test_assignment_equals_bruteforce_oracle_on_random_token_sets():
    rng = random.Random(4242)
    alphabet = ["a", "ab", "abc", "b", "bc", "c", "ca", "x"]
    for _ in range(3000):
        name = [rng.choice(alphabet) for _ in range(rng.randint(1, 5))]
        q = [rng.choice(["a", "ab", "b", "c", "bc", "abc", "z"]) for _ in range(rng.randint(1, 4))]
        assert has_distinct_prefix_assignment(q, name) == oracle_assignment(q, name), (q, name)


@pytest.mark.parametrize("name,q", [
    ("Vini Jr.", "vini jr"), ("Vini Jr.", "vini"), ("Vini Jr.", "jr vini"), ("Rick van Drongelen", "dron"), ("Rick van Drongelen", "ngel"),
    ("Erling Haaland", "xyz"), ("Al Al", "al al al"), ("Ab A", "a ab"), ("Martin Ødegaard", "odeg mar"), ("Kylian Mbappé", "mbappe"),
    ("Pierre-Emile Højbjerg", "emile pierre"), ("O'Neil", "o neil"),
])
def test_match_quality_equals_oracle(name, q):
    from api.domain.normalize import normalize
    n, qn = normalize(name), normalize(q)
    got = match_quality(n, n.split(), qn, qn.split())
    assert (got.value if got else None) == oracle_quality(name, q)


def _row(i, name, overall=80, kind_uid=None, pos="ST", club="Club", nat="Nat"):
    return PlayerIndexRow(i, name, pos, club, nat, None, None, overall, overall + 2, 1000, "v", None, kind_uid, None, None)


ROWS = [_row(5, "Anna Smith", 70), _row(3, "Anna Smith", 70), _row(4, "Anna Smith", 90), _row(1, "Smith", 60, pos="GK", club="Zed FC", nat="Peru"),
        _row(2, "John Anna", 85, kind_uid="u2"), _row(6, "Joanna Silva", 75, kind_uid="u6", pos="CB")]
ALIASES = [AliasRow(2, "Johnny Annabel", "src_b"), AliasRow(2, "john anna", "src_a"), AliasRow(6, "Anna Silva", "src_a")]


def ids(res):
    return [h.ea_fc26_id for h in res.hits]


def test_relevance_order_is_quality_then_overall_then_id():
    idx = SearchIndex(ROWS, ALIASES)
    res = idx.search(SearchQuery("anna smith"))
    assert ids(res) == [4, 3, 5]                                   # EXACT; same name -> overall desc, then ea id asc (3 before 5)
    res = idx.search(SearchQuery("anna"))
    # EA 6 'Joanna Silva' only CONTAINS 'anna' (SUBSTRING) but its alias 'Anna Silva' is a PREFIX match, and the reported
    # quality is the best one - so it ranks with the PREFIX group (by overall desc: 90, 75, 70, 70), ahead of TOKEN_PREFIX.
    assert [(h.ea_fc26_id, h.quality) for h in res.hits] == [
        (4, MatchQuality.PREFIX), (6, MatchQuality.PREFIX), (3, MatchQuality.PREFIX), (5, MatchQuality.PREFIX),
        (2, MatchQuality.TOKEN_PREFIX)]
    assert res.total == 5


def test_alias_reported_only_when_strictly_better():
    idx = SearchIndex(ROWS, ALIASES)
    h = {x.ea_fc26_id: x for x in idx.search(SearchQuery("anna")).hits}
    assert (h[6].quality, h[6].matched_alias, h[6].matched_alias_source) == (MatchQuality.PREFIX, "Anna Silva", "src_a")   # SUBSTRING -> PREFIX
    assert h[2].matched_alias is None                                # EA name 'John Anna' TOKEN_PREFIX; aliases do not beat it
    # alias equal to the EA name (case/accents aside) is never reported
    assert {x.ea_fc26_id: x.matched_alias for x in idx.search(SearchQuery("john anna")).hits}[2] is None


def test_filters_are_anded_and_casefolded():
    idx = SearchIndex(ROWS, ALIASES)
    assert ids(idx.search(SearchQuery("anna", entity_kind=EntityKind.CANONICAL))) == [6, 2]
    assert ids(idx.search(SearchQuery("anna", entity_kind=EntityKind.EA_ONLY))) == [4, 3, 5]
    assert ids(idx.search(SearchQuery("smith", position="gk"))) == [1]                    # callers pass casefolded values
    assert ids(idx.search(SearchQuery("smith", nationality="peru", club="zed fc"))) == [1]
    assert idx.search(SearchQuery("smith", nationality="peru", club="other")).total == 0
    assert ids(idx.search(SearchQuery("anna", min_overall=85))) == [4, 2]


def test_sorts_and_pagination_are_total_and_stable():
    idx = SearchIndex(ROWS, ALIASES)
    assert ids(idx.search(SearchQuery("anna", sort=SearchSort.overall_desc))) == [4, 2, 6, 3, 5]
    # name_asc sorts by the normalized EA name (aliases are never a sort key): anna smith (3,4,5) < joanna silva (6) < john anna (2)
    assert ids(idx.search(SearchQuery("anna", sort=SearchSort.name_asc))) == [3, 4, 5, 6, 2]
    full = idx.search(SearchQuery("anna", limit=50)).hits
    pages = []
    for off in range(0, 6):
        pages += list(idx.search(SearchQuery("anna", limit=1, offset=off)).hits)
    assert pages[:5] == list(full) and len(pages) == 5 and pages[5:] == []
    assert idx.search(SearchQuery("anna", limit=2, offset=4)).total == 5
    assert idx.search(SearchQuery("anna", limit=2, offset=99)).hits == ()


def test_every_ordering_ends_with_ea_id_so_ties_cannot_reorder():
    rows = [_row(i, "Same Name", 80) for i in (9, 7, 8, 1, 3)]
    for sort in SearchSort:
        got = ids(SearchIndex(rows).search(SearchQuery("same name", sort=sort)))
        assert got == sorted(got) == [1, 3, 7, 8, 9]
    shuffled = list(rows); random.Random(1).shuffle(shuffled)
    assert ids(SearchIndex(shuffled).search(SearchQuery("same name"))) == [1, 3, 7, 8, 9]       # input order is irrelevant


def test_index_with_no_aliases_and_no_match():
    assert SearchIndex(ROWS).search(SearchQuery("zzz")).total == 0
    assert len(SearchIndex(ROWS)) == len(ROWS)
