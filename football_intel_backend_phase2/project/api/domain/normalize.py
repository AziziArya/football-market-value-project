"""Search-text normalization (ARCHITECTURE_API.md section 6). One function for index AND query.
Executable spec / reference: tests/test_contract/test_search_spec.py (an equivalence test runs on all EA names)."""
from __future__ import annotations

import unicodedata

# Letters NFKD cannot decompose (casefold already maps 'ß' -> 'ss').
_FOLD = {"ø": "o", "ł": "l", "ı": "i", "æ": "ae", "œ": "oe", "ð": "d", "þ": "th", "đ": "d", "ħ": "h"}


def normalize(text: str) -> str:
    """NFKD + casefold; delete combining marks and format chars (e.g. soft hyphen); fold non-decomposable
    letters; punctuation/symbol/separator/control -> space; collapse whitespace. Keeps letters/digits of any script."""
    out: list[str] = []
    for ch in unicodedata.normalize("NFKD", text).casefold():
        cat = unicodedata.category(ch)
        if cat == "Mn" or cat == "Cf":
            continue
        ch = _FOLD.get(ch, ch)
        out.append(" " if cat[0] in "PSZC" else ch)
    return " ".join("".join(out).split())
