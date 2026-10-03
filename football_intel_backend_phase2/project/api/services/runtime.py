"""Startup state of the API: what was verified, and the in-memory read models (RM1/RM2)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from api.domain.models import LinkStateRow
from api.domain.player_index import PlayerIndex
from api.domain.search import SearchIndex


@dataclass(frozen=True)
class RuntimeState:
    status: str                                   # "ok" | "unavailable"  (fail-closed)
    database_reachable: bool
    problems: tuple[str, ...] = ()                # for logs only - never sent to clients
    index: PlayerIndex | None = None              # RM1
    link_states: tuple[LinkStateRow, ...] = ()    # RM2 (flat)
    links_by_player: Mapping[int, Mapping[str, LinkStateRow]] = field(default_factory=dict)   # RM2 by EA id, then source
    search_index: SearchIndex | None = None       # RM1 + RM3
    loaded_at: datetime | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass
class RuntimeHolder:
    state: RuntimeState = field(default_factory=lambda: RuntimeState("unavailable", False, ("not started",)))
