"""Player search (ARCHITECTURE_API.md section 6). Pure: no I/O, no framework.

Quality (on normalized strings): EXACT < PREFIX < TOKEN_PREFIX < SUBSTRING; best of the EA name and, for CANONICAL
players, aliases from other sources (alias reported only when STRICTLY better than the EA name).
Ordering is total (always ends with ea_fc26_id), so pagination is deterministic and stable."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from api.domain.enums import EntityKind, MatchQuality, SearchSort
from api.domain.models import AliasRow, PlayerIndexRow
from api.domain.normalize import normalize

QUALITY_RANK = {MatchQuality.EXACT: 0, MatchQuality.PREFIX: 1, MatchQuality.TOKEN_PREFIX: 2, MatchQuality.SUBSTRING: 3}


def has_distinct_prefix_assignment(q_tokens: Sequence[str], name_tokens: Sequence[str]) -> bool:
    """True iff every query token can be assigned a DIFFERENT name token it is a prefix of (bipartite matching,
    so the answer never depends on token order; plain greedy matching would give wrong answers)."""
    if len(q_tokens) > len(name_tokens):
        return False
    owner: dict[int, int] = {}                     # name-token index -> query-token index

    def assign(qi: int, seen: set[int]) -> bool:
        for ni, token in enumerate(name_tokens):
            if ni in seen or not token.startswith(q_tokens[qi]):
                continue
            seen.add(ni)
            if ni not in owner or assign(owner[ni], seen):
                owner[ni] = qi
                return True
        return False

    return all(assign(i, set()) for i in range(len(q_tokens)))


def match_quality(name_norm: str, name_tokens: Sequence[str], q_norm: str, q_tokens: Sequence[str]) -> MatchQuality | None:
    if name_norm == q_norm:
        return MatchQuality.EXACT
    if name_norm.startswith(q_norm):
        return MatchQuality.PREFIX
    if all(t in name_norm for t in q_tokens):      # cheap necessary condition for the two weaker qualities
        if has_distinct_prefix_assignment(q_tokens, name_tokens):
            return MatchQuality.TOKEN_PREFIX
        if q_norm in name_norm:
            return MatchQuality.SUBSTRING
    return None


@dataclass(frozen=True)
class SearchQuery:
    q_norm: str
    entity_kind: EntityKind | None = None
    position: str | None = None          # casefolded
    nationality: str | None = None       # casefolded
    club: str | None = None              # casefolded
    min_overall: int | None = None
    sort: SearchSort = SearchSort.relevance
    limit: int = 20
    offset: int = 0

    @property
    def q_tokens(self) -> tuple[str, ...]:
        return tuple(self.q_norm.split())


@dataclass(frozen=True)
class SearchHit:
    ea_fc26_id: int
    quality: MatchQuality
    matched_alias: str | None
    matched_alias_source: str | None


@dataclass(frozen=True)
class SearchResult:
    total: int
    hits: tuple[SearchHit, ...]          # the requested page only


@dataclass(frozen=True)
class _Alias:
    raw: str
    norm: str
    tokens: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class _Entry:
    ea_fc26_id: int
    name_norm: str
    name_tokens: tuple[str, ...]
    aliases: tuple[_Alias, ...]
    overall: int
    kind: EntityKind
    position: str | None
    club: str | None
    nationality: str | None


def _fold(value: str | None) -> str | None:
    return value.casefold() if value is not None else None


class SearchIndex:
    """Immutable in-memory index built once at startup from RM1 (+ RM3 aliases)."""

    def __init__(self, rows: Iterable[PlayerIndexRow], aliases: Iterable[AliasRow] = ()):
        by_player: dict[int, list[AliasRow]] = {}
        for a in aliases:
            by_player.setdefault(a.ea_fc26_id, []).append(a)
        entries = []
        for r in rows:
            name = normalize(r.display_name or "")
            seen: set[tuple[str, str]] = set()
            alias_entries = []
            for a in sorted(by_player.get(r.ea_fc26_id, ()), key=lambda x: (x.source, normalize(x.value), x.value)):
                norm = normalize(a.value)
                if not norm or norm == name or (a.source, norm) in seen:
                    continue                     # an alias equal to the EA name can never be strictly better
                seen.add((a.source, norm))
                alias_entries.append(_Alias(a.value, norm, tuple(norm.split()), a.source))
            entries.append(_Entry(r.ea_fc26_id, name, tuple(name.split()), tuple(alias_entries), r.overall_rating, r.entity_kind,
                                  _fold(r.position), _fold(r.club), _fold(r.nationality)))
        self._entries = tuple(entries)

    def __len__(self) -> int:
        return len(self._entries)

    def search(self, query: SearchQuery) -> SearchResult:
        q, tokens = query.q_norm, query.q_tokens
        matches: list[tuple[_Entry, MatchQuality, _Alias | None]] = []
        for e in self._entries:
            if query.entity_kind is not None and e.kind is not query.entity_kind:
                continue
            if query.position is not None and e.position != query.position:
                continue
            if query.nationality is not None and e.nationality != query.nationality:
                continue
            if query.club is not None and e.club != query.club:
                continue
            if query.min_overall is not None and e.overall < query.min_overall:
                continue
            quality = match_quality(e.name_norm, e.name_tokens, q, tokens)
            alias: _Alias | None = None
            best_alias, best_alias_quality = None, None
            for a in e.aliases:                       # aliases are pre-sorted, so ties resolve deterministically
                aq = match_quality(a.norm, a.tokens, q, tokens)
                if aq is not None and (best_alias_quality is None or QUALITY_RANK[aq] < QUALITY_RANK[best_alias_quality]):
                    best_alias, best_alias_quality = a, aq
            if best_alias_quality is not None and (quality is None or QUALITY_RANK[best_alias_quality] < QUALITY_RANK[quality]):
                quality, alias = best_alias_quality, best_alias      # reported only when STRICTLY better than the EA name
            if quality is not None:
                matches.append((e, quality, alias))

        if query.sort is SearchSort.relevance:
            matches.sort(key=lambda m: (QUALITY_RANK[m[1]], -m[0].overall, m[0].ea_fc26_id))
        elif query.sort is SearchSort.overall_desc:
            matches.sort(key=lambda m: (-m[0].overall, m[0].ea_fc26_id))
        else:
            matches.sort(key=lambda m: (m[0].name_norm, m[0].ea_fc26_id))
        page = matches[query.offset: query.offset + query.limit]
        return SearchResult(len(matches), tuple(
            SearchHit(e.ea_fc26_id, quality, a.raw if a else None, a.source if a else None) for e, quality, a in page))
