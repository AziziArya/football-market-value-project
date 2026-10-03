"""In-memory RM1 container. Immutable after construction."""
from __future__ import annotations

import collections
from types import MappingProxyType
from typing import Iterable, Mapping

from api.domain.enums import EntityKind
from api.domain.models import PlayerIndexRow


class PlayerIndex:
    def __init__(self, rows: Iterable[PlayerIndexRow]):
        self._rows = tuple(rows)
        by_id: dict[int, PlayerIndexRow] = {}
        for r in self._rows:
            by_id.setdefault(r.ea_fc26_id, r)
        self._by_id: Mapping[int, PlayerIndexRow] = MappingProxyType(by_id)

    def __len__(self) -> int:
        return len(self._rows)

    @property
    def rows(self) -> tuple[PlayerIndexRow, ...]:
        return self._rows

    def get(self, ea_fc26_id: int) -> PlayerIndexRow | None:
        return self._by_id.get(ea_fc26_id)

    def count(self, kind: EntityKind) -> int:
        return sum(1 for r in self._rows if r.entity_kind is kind)

    def duplicate_ids(self) -> set[int]:
        counts = collections.Counter(r.ea_fc26_id for r in self._rows)
        return {i for i, n in counts.items() if n > 1}
