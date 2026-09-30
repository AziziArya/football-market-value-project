"""
Injury data status population.

No injury provider exists (see Phase 0/1 audits — no free, legally-clean,
structured injury source has been approved). This module's ENTIRE job is
to record that fact honestly for every player, so the API/UI layer can
show "no injury data available" instead of either fabricating an injury
history or wrongly implying "confirmed healthy".

This is intentionally the simplest loader in the codebase, kept in its
own file so it's trivial to audit in isolation. If a real injury
provider is ever added, a NEW function should populate
CONFIRMED_NO_INJURIES / HAS_RECORDS from real data — this function must
never be changed to guess either of those from absence of data.
"""
from __future__ import annotations

from datetime import datetime, timezone

import duckdb


def mark_no_injury_source_available(con: duckdb.DuckDBPyConnection) -> int:
    """For every player in `players` that doesn't yet have an
    injury_data_status row, insert NO_SOURCE_AVAILABLE. Idempotent —
    running twice doesn't duplicate or change existing rows (a player
    that already has HAS_RECORDS or CONFIRMED_NO_INJURIES from some
    future real source is left untouched, never downgraded)."""
    now = datetime.now(timezone.utc)
    rows = con.execute(
        "select player_uid from players p "
        "where not exists (select 1 from injury_data_status s where s.player_uid = p.player_uid)"
    ).fetchall()
    if not rows:
        return 0
    con.executemany(
        "insert into injury_data_status (player_uid, status, checked_at) values (?, 'NO_SOURCE_AVAILABLE', ?)",
        [(r[0], now) for r in rows],
    )
    return len(rows)
