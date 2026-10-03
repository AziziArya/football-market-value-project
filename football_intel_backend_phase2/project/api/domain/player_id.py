"""Public player id (decision D2/G13): ONLY `ea:<ea_fc26_id>`. Stable across rebuilds and across later matching.
`p:<uuid>` is NOT an API identifier: it is never accepted and never returned (the uuid is random per database build)."""
from __future__ import annotations

import re

# Canonical form: no leading zeros, at most 9 digits (one URL per player). Same pattern as the contract's PlayerId.
PLAYER_ID_PATTERN = r"^ea:(0|[1-9][0-9]{0,8})$"
_RE = re.compile(PLAYER_ID_PATTERN)


def parse_player_id(raw: str) -> int | None:
    """EA id for a well-formed public id, else None. fullmatch (not match): '$' would accept a trailing newline."""
    return int(raw[3:]) if _RE.fullmatch(raw) else None


def format_player_id(ea_fc26_id: int) -> str:
    return f"ea:{ea_fc26_id}"
