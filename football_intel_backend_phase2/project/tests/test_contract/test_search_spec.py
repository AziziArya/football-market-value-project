"""Executable SPEC for ARCHITECTURE_API.md section 6 (search normalization / match quality).
The reference functions below live in the TEST only; the future api/domain implementation must satisfy the same cases.
No API code exists."""
import collections
import itertools
import unicodedata
from pathlib import Path

import pandas as pd
import pytest

EA_CSV = Path(__file__).parents[2] / "data" / "raw" / "ea_fc26" / "fc26_merged_clean.csv"
FOLD = {"ø": "o", "ł": "l", "ı": "i", "æ": "ae", "œ": "oe", "ð": "d", "þ": "th", "đ": "d", "ħ": "h"}


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).casefold()
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat == "Mn" or cat == "Cf":
            continue                                   # combining marks / soft hyphen: deleted
        ch = FOLD.get(ch, ch)
        out.append(" " if cat[0] in "PSZC" else ch)   # punctuation, symbols, separators, controls -> space
    return " ".join("".join(out).split())


QUALITY = ["EXACT", "PREFIX", "TOKEN_PREFIX", "SUBSTRING"]


def has_assignment(q_tokens, name_tokens):
    """ORACLE (brute force, obviously correct): each query token is a prefix of a DIFFERENT name token."""
    return any(all(name_tokens[i].startswith(t) for t, i in zip(q_tokens, perm))
               for perm in itertools.permutations(range(len(name_tokens)), len(q_tokens)))


def quality(name: str, q: str):
    n, q = normalize(name), normalize(q)
    if n == q:
        return "EXACT"
    if n.startswith(q):
        return "PREFIX"
    if has_assignment(q.split(), n.split()):
        return "TOKEN_PREFIX"
    return "SUBSTRING" if q in n else None


@pytest.mark.parametrize("raw,expected", [
    ("Martin Ødegaard", "martin odegaard"),
    ("Dagur Dan Þór\u00adhalls\u00adson", "dagur dan thorhallsson"),   # soft hyphen deleted, thorn folded
    ("N'Golo Kanté", "n golo kante"),
    ("Vini Jr.", "vini jr"),
    ("Pierre-Emile Højbjerg", "pierre emile hojbjerg"),
    ("Pascal Groß", "pascal gross"),
    ("  Łukasz   Fabiański ", "lukasz fabianski"),
])
def test_normalization_golden(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize("name,q,expected", [
    ("Vini Jr.", "vini jr", "EXACT"),
    ("Vini Jr.", "vini", "PREFIX"),
    ("Vini Jr.", "jr vini", "TOKEN_PREFIX"),
    ("Rick van Drongelen", "dron", "TOKEN_PREFIX"),
    ("Rick van Drongelen", "ngel", "SUBSTRING"),
    ("Erling Haaland", "xyz", None),
    ("Al Al", "al al al", None),                      # a name token is used at most once
    ("Ab A", "a ab", "TOKEN_PREFIX"),                 # greedy left-to-right matching would wrongly reject this
])
def test_match_quality_golden(name, q, expected):
    assert quality(name, q) == expected


def test_quality_order_is_the_contract_order():
    assert QUALITY == ["EXACT", "PREFIX", "TOKEN_PREFIX", "SUBSTRING"]


@pytest.fixture(scope="module")
def names():
    return pd.read_csv(EA_CSV, low_memory=False)["player_name"].astype(str).tolist()


def test_real_data_nfkd_alone_is_insufficient_and_spec_is_sufficient(names):
    def nfkd_only(s):
        return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    assert len(names) == 16107
    assert sum(1 for n in names if not nfkd_only(n).isascii()) == 314   # figure quoted in ARCHITECTURE_API.md
    normalized = [normalize(n) for n in names]
    assert all(n and n.isascii() for n in normalized)                    # 0 residual, none empty


def test_real_data_duplicate_names_are_not_identifiers(names):
    assert sum(1 for v in collections.Counter(n.casefold() for n in names).values() if v > 1) == 124
    assert sum(1 for v in collections.Counter(normalize(n) for n in names).values() if v > 1) == 128


def test_ordering_is_total_when_ea_id_is_the_last_key():
    rows = [(0, -80, 2), (0, -80, 1), (1, -90, 3)]     # (quality rank, -overall, ea_id)
    assert sorted(rows) == [(0, -80, 1), (0, -80, 2), (1, -90, 3)]
