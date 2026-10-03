"""
Persistence for ingestion/normalizer.py output — the 5 normalized types
that stayed in-memory-only through Phase 1: player_field_values,
ea_fc26_attributes, market_value_history, transfers, appearances.

Same rules as matching/loader.py:
  - append-only for historical/time-series data (player_field_values,
    market_value_history, transfers, appearances) — a new fetch never
    deletes or edits a prior row, it just adds one.
  - ea_fc26_attributes is upserted by ea_fc26_id (there is exactly one
    "current" EA snapshot row per player per model_version — see below),
    never silently overwritten across DIFFERENT dataset_versions.
  - every row keeps source + dataset_version/fetched_at.
  - conflicting values from different sources are never merged — they're
    just different rows in player_field_values, distinguished by `source`.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from urllib.parse import quote

import duckdb
import pandas as pd

from ingestion.normalizer import (
    AppearanceRecord,
    EAAttributesRecord,
    IdentityFieldValue,
    ImageRecord,
    MarketValueRecord,
    TransferRecord,
)


def _next_id(con: duckdb.DuckDBPyConnection, table: str) -> int:
    return con.execute(f"select coalesce(max(id), 0) + 1 from {table}").fetchone()[0]


def _bulk_insert(con: duckdb.DuckDBPyConnection, table: str, columns: list[str], rows: list[tuple]) -> None:
    """Real bulk insert via DuckDB's DataFrame registration + INSERT...SELECT.
    `executemany()` in DuckDB's Python driver is NOT vectorized — it is
    effectively one execute() per row (measured: ~1ms/row, so 96,642 rows
    took 106s). Registering a DataFrame and doing a single INSERT...SELECT
    uses DuckDB's actual vectorized engine and is orders of magnitude
    faster. Found and fixed during Phase 2.3 benchmarking."""
    if not rows:
        return
    df = pd.DataFrame(rows, columns=columns)
    con.register("_bulk_insert_tmp", df)
    try:
        col_list = ", ".join(columns)
        con.execute(f"insert into {table} ({col_list}) select {col_list} from _bulk_insert_tmp")
    finally:
        con.unregister("_bulk_insert_tmp")


def _drop_already_loaded(con: duckdb.DuckDBPyConnection, fields: list[IdentityFieldValue]) -> list[IdentityFieldValue]:
    """Idempotency guard (G9). A field is "already loaded" when player_field_values already holds a row with the
    same logical key (source, source_record_id, field_name, field_value, dataset_version). Re-ingesting the same
    dataset therefore adds nothing; a CHANGED value (same or new dataset_version) has a different key and is
    appended as before, so the table stays append-only and nothing is ever deleted or edited.
    Duplicates inside one batch are collapsed too. `fetched_at` and `is_current` are deliberately NOT part of the
    key (fetched_at differs on every run; is_current is never a selection criterion)."""
    if not fields:
        return []
    sources = sorted({f.source for f in fields})
    marks = ", ".join("?" for _ in sources)
    seen = set(con.execute(
        f"select source, source_record_id, field_name, field_value, dataset_version from player_field_values where source in ({marks})",
        sources,
    ).fetchall())
    fresh = []
    for f in fields:
        key = (f.source, f.source_record_id, f.field_name, f.field_value, f.dataset_version)
        if key in seen:
            continue
        seen.add(key)
        fresh.append(f)
    return fresh


def load_identity_fields(con: duckdb.DuckDBPyConnection, fields: list[IdentityFieldValue]) -> int:
    """Append-only AND idempotent: a (source, source_record_id, field_name, field_value, dataset_version)
    combination is stored once; re-running the same dataset adds no rows (see _drop_already_loaded). Two sources
    disagreeing on a field (e.g. EA says nationality=Brazil, transfermarkt says Portugal) means TWO rows here,
    never one overwritten by the other. player_uid is left NULL until matching assigns one (see
    backfill_player_uid below). Returns the number of rows actually written."""
    fields = _drop_already_loaded(con, fields)
    if not fields:
        return 0
    next_id = _next_id(con, "player_field_values")
    rows = [
        (next_id + i, None, f.field_name, f.field_value, f.source, f.source_record_id, f.fetched_at, f.dataset_version, True, None)
        for i, f in enumerate(fields)
    ]
    _bulk_insert(
        con, "player_field_values",
        ["id", "player_uid", "field_name", "field_value", "source", "source_record_id", "fetched_at", "dataset_version", "is_current", "confidence"],
        rows,
    )
    return len(rows)


def load_ea_attributes(con: duckdb.DuckDBPyConnection, record: EAAttributesRecord) -> str:
    """Upsert by ea_fc26_id — there is one 'current baseline' row per EA
    player, since fc26_merged_clean.csv is itself a single frozen
    snapshot (dataset_version='2025-09-19'), not a time series. A row is
    only ever replaced by a NEWER dataset_version of the SAME source;
    replacing with an older/equal version is refused rather than silently
    applied, to avoid clobbering newer data with a stale re-run.

    Note: dataset_version (which EA snapshot this came from) and
    model_version (which ML model produced predicted_value_eur) are
    deliberately separate columns — conflating them was a bug caught
    while writing this function (see migration 0005)."""
    existing = con.execute(
        "select dataset_version from ea_fc26_attributes where ea_fc26_id = ?", [record.ea_fc26_id]
    ).fetchone()

    if existing is not None:
        existing_version = existing[0]
        if existing_version is not None and record.dataset_version is not None and record.dataset_version <= existing_version:
            return "skipped_not_newer"
        con.execute(
            "update ea_fc26_attributes set overall_rating=?, potential=?, value_eur_ingame=?, dataset_version=? "
            "where ea_fc26_id=?",
            [record.overall_rating, record.potential, record.value_eur_ingame, record.dataset_version, record.ea_fc26_id],
        )
        return "updated"

    con.execute(
        "insert into ea_fc26_attributes (ea_fc26_id, player_uid, overall_rating, potential, value_eur_ingame, predicted_value_eur, model_version, dataset_version, raw_json) "
        "values (?, NULL, ?, ?, ?, NULL, NULL, ?, NULL)",
        [record.ea_fc26_id, record.overall_rating, record.potential, record.value_eur_ingame, record.dataset_version],
    )
    return "inserted"


def load_market_value(con: duckdb.DuckDBPyConnection, record: MarketValueRecord) -> int:
    """Append-only — every valuation date is a distinct historical fact,
    never overwritten. Duplicate-detection keys on
    (player_id_in_source, valuation_date, source, dataset_version), so
    an idempotent re-run of the same dump doesn't duplicate rows, while
    two different players sharing a value+date by coincidence are never
    confused with each other."""
    dup = con.execute(
        "select 1 from market_value_history "
        "where player_id_in_source = ? and valuation_date = ? and source = ? and dataset_version = ?",
        [record.player_id_in_source, record.valuation_date, record.source, record.dataset_version],
    ).fetchone()
    if dup is not None:
        return 0
    next_id = _next_id(con, "market_value_history")
    con.execute(
        "insert into market_value_history (id, player_uid, player_id_in_source, value_eur, valuation_date, source, dataset_version, imported_at) "
        "values (?, NULL, ?, ?, ?, ?, ?, ?)",
        [next_id, record.player_id_in_source, record.value_eur, record.valuation_date, record.source, record.dataset_version, datetime.now(timezone.utc)],
    )
    return 1


def load_transfer(con: duckdb.DuckDBPyConnection, record: TransferRecord) -> int:
    dup = con.execute(
        "select 1 from transfers where player_id_in_source = ? and transfer_date is not distinct from ? and source = ?",
        [record.player_id_in_source, record.transfer_date, record.source],
    ).fetchone()
    if dup is not None:
        return 0
    next_id = _next_id(con, "transfers")
    con.execute(
        "insert into transfers (id, player_uid, player_id_in_source, transfer_date, from_club, to_club, fee_eur, is_loan, source, dataset_version) "
        "values (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
        [next_id, record.player_id_in_source, record.transfer_date, record.from_club, record.to_club, record.fee_eur, record.is_loan, record.source, record.dataset_version],
    )
    return 1


def load_appearance(con: duckdb.DuckDBPyConnection, record: AppearanceRecord) -> int:
    dup = con.execute(
        "select 1 from appearances where player_id_in_source = ? and game_date is not distinct from ? and source = ?",
        [record.player_id_in_source, record.game_date, record.source],
    ).fetchone()
    if dup is not None:
        return 0
    next_id = _next_id(con, "appearances")
    con.execute(
        "insert into appearances (id, player_uid, player_id_in_source, game_date, competition, minutes_played, goals, assists, source, dataset_version) "
        "values (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
        [next_id, record.player_id_in_source, record.game_date, record.competition, record.minutes_played, record.goals, record.assists, record.source, record.dataset_version],
    )
    return 1


def batch_load_ea_attributes(con: duckdb.DuckDBPyConnection, records: list[EAAttributesRecord]) -> dict[str, int]:
    """Batched equivalent of load_ea_attributes(): one SELECT for all
    existing rows, partition in Python, two executemany calls total
    (insert bucket, update bucket) regardless of how many records —
    versus 2 round trips PER record in the single-record version."""
    counts = {"inserted": 0, "updated": 0, "skipped_not_newer": 0}
    if not records:
        return counts

    existing = dict(con.execute("select ea_fc26_id, dataset_version from ea_fc26_attributes").fetchall())

    to_insert, to_update = [], []
    for r in records:
        existing_version = existing.get(r.ea_fc26_id)
        if existing_version is None:
            to_insert.append(r)
        elif r.dataset_version is not None and r.dataset_version <= existing_version:
            counts["skipped_not_newer"] += 1
        else:
            to_update.append(r)

    if to_insert:
        rows = [
            (r.ea_fc26_id, r.overall_rating, r.potential, r.value_eur_ingame, r.dataset_version)
            for r in to_insert
        ]
        _bulk_insert(
            con, "ea_fc26_attributes",
            ["ea_fc26_id", "overall_rating", "potential", "value_eur_ingame", "dataset_version"],
            rows,
        )
        counts["inserted"] = len(to_insert)

    if to_update:
        rows = [
            (r.overall_rating, r.potential, r.value_eur_ingame, r.dataset_version, r.ea_fc26_id)
            for r in to_update
        ]
        con.executemany(
            "update ea_fc26_attributes set overall_rating=?, potential=?, value_eur_ingame=?, dataset_version=? where ea_fc26_id=?",
            rows,
        )
        counts["updated"] = len(to_update)

    return counts


def batch_load_market_values(con: duckdb.DuckDBPyConnection, records: list[MarketValueRecord]) -> int:
    """Batched equivalent of load_market_value(): one SELECT for existing
    dedup keys, dedupe against both the DB and duplicates within this
    same batch, one executemany for the remainder."""
    if not records:
        return 0
    existing = set(con.execute(
        "select player_id_in_source, valuation_date, source, dataset_version from market_value_history"
    ).fetchall())

    seen_in_batch = set()
    to_insert = []
    next_id = _next_id(con, "market_value_history")
    for r in records:
        key = (r.player_id_in_source, r.valuation_date, r.source, r.dataset_version)
        if key in existing or key in seen_in_batch:
            continue
        seen_in_batch.add(key)
        to_insert.append(r)

    if to_insert:
        now = datetime.now(timezone.utc)
        rows = [
            (next_id + i, r.player_id_in_source, r.value_eur, r.valuation_date, r.source, r.dataset_version, now)
            for i, r in enumerate(to_insert)
        ]
        _bulk_insert(
            con, "market_value_history",
            ["id", "player_id_in_source", "value_eur", "valuation_date", "source", "dataset_version", "imported_at"],
            rows,
        )
    return len(to_insert)


def batch_load_transfers(con: duckdb.DuckDBPyConnection, records: list[TransferRecord]) -> int:
    if not records:
        return 0
    existing = set(con.execute(
        "select player_id_in_source, transfer_date, source from transfers"
    ).fetchall())

    seen_in_batch = set()
    to_insert = []
    next_id = _next_id(con, "transfers")
    for r in records:
        key = (r.player_id_in_source, r.transfer_date, r.source)
        if key in existing or key in seen_in_batch:
            continue
        seen_in_batch.add(key)
        to_insert.append(r)

    if to_insert:
        rows = [
            (next_id + i, r.player_id_in_source, r.transfer_date, r.from_club, r.to_club, r.fee_eur, r.is_loan, r.source, r.dataset_version)
            for i, r in enumerate(to_insert)
        ]
        _bulk_insert(
            con, "transfers",
            ["id", "player_id_in_source", "transfer_date", "from_club", "to_club", "fee_eur", "is_loan", "source", "dataset_version"],
            rows,
        )
    return len(to_insert)


def batch_load_appearances(con: duckdb.DuckDBPyConnection, records: list[AppearanceRecord]) -> int:
    if not records:
        return 0
    existing = set(con.execute(
        "select player_id_in_source, game_date, source from appearances"
    ).fetchall())

    seen_in_batch = set()
    to_insert = []
    next_id = _next_id(con, "appearances")
    for r in records:
        key = (r.player_id_in_source, r.game_date, r.source)
        if key in existing or key in seen_in_batch:
            continue
        seen_in_batch.add(key)
        to_insert.append(r)

    if to_insert:
        rows = [
            (next_id + i, r.player_id_in_source, r.game_date, r.competition, r.minutes_played, r.goals, r.assists, r.source, r.dataset_version)
            for i, r in enumerate(to_insert)
        ]
        _bulk_insert(
            con, "appearances",
            ["id", "player_id_in_source", "game_date", "competition", "minutes_played", "goals", "assists", "source", "dataset_version"],
            rows,
        )
    return len(to_insert)


def backfill_player_uid(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Fills in player_uid on rows that were loaded before a player_uid
    existed (matching runs after ingestion in the pipeline). Matches
    ea_fc26_attributes by ea_fc26_id, player_field_values by
    (source, source_record_id), and market_value_history/transfers/
    appearances by (source, player_id_in_source) — all joined against the
    `players` table. Never invents a player_uid — rows with no
    corresponding `players` row stay NULL (honest: 'not yet matched'),
    they are not dropped."""
    con.execute(
        "update ea_fc26_attributes set player_uid = p.player_uid "
        "from players p where ea_fc26_attributes.ea_fc26_id = p.ea_fc26_id and ea_fc26_attributes.player_uid is null"
    )
    con.execute(
        "update player_field_values set player_uid = p.player_uid "
        "from players p where player_field_values.source = 'ea_fc26' "
        "and player_field_values.source_record_id = cast(p.ea_fc26_id as varchar) "
        "and player_field_values.player_uid is null"
    )
    con.execute(
        "update player_field_values set player_uid = p.player_uid "
        "from players p where player_field_values.source = 'transfermarkt_dataset' "
        "and player_field_values.source_record_id = cast(p.transfermarkt_id as varchar) "
        "and player_field_values.player_uid is null"
    )
    con.execute(
        "update market_value_history set player_uid = p.player_uid "
        "from players p where market_value_history.source = 'transfermarkt_dataset' "
        "and market_value_history.player_id_in_source = cast(p.transfermarkt_id as varchar) "
        "and market_value_history.player_uid is null"
    )
    con.execute(
        "update transfers set player_uid = p.player_uid "
        "from players p where transfers.source = 'transfermarkt_dataset' "
        "and transfers.player_id_in_source = cast(p.transfermarkt_id as varchar) "
        "and transfers.player_uid is null"
    )
    con.execute(
        "update appearances set player_uid = p.player_uid "
        "from players p where appearances.source = 'transfermarkt_dataset' "
        "and appearances.player_id_in_source = cast(p.transfermarkt_id as varchar) "
        "and appearances.player_uid is null"
    )

    counts = {}
    for table in ("ea_fc26_attributes", "player_field_values", "market_value_history", "transfers", "appearances"):
        counts[table] = con.execute(f"select count(*) from {table} where player_uid is not null").fetchone()[0]
    return counts


def load_identity_fields_for_player(con: duckdb.DuckDBPyConnection, player_uid: str, fields: list[IdentityFieldValue]) -> int:
    """Same append-only + idempotent contract as load_identity_fields(), but for the
    enrichment path (wikidata) where player_uid is already known at
    insert time — no need to wait for backfill_player_uid()."""
    fields = _drop_already_loaded(con, fields)
    if not fields:
        return 0
    next_id = _next_id(con, "player_field_values")
    rows = [
        (next_id + i, player_uid, f.field_name, f.field_value, f.source, f.source_record_id, f.fetched_at, f.dataset_version, True, None)
        for i, f in enumerate(fields)
    ]
    _bulk_insert(
        con, "player_field_values",
        ["id", "player_uid", "field_name", "field_value", "source", "source_record_id", "fetched_at", "dataset_version", "is_current", "confidence"],
        rows,
    )
    return len(rows)


def load_player_image(con: duckdb.DuckDBPyConnection, player_uid: str, record: ImageRecord) -> str:
    """Enrichment-time loader (runs AFTER matching, so player_uid is
    already known — unlike the pre-match loaders above). Never fabricates
    license/attribution: whatever ImageRecord carries (possibly None) is
    stored as-is. is_primary is only set True if this player has no
    existing image row yet — never overwrites an existing primary."""
    if record.image_filename is None:
        return "skipped_no_image"

    # Commons filenames contain spaces/special characters: encode so the stored URL is valid
    # (spaces -> underscores, the Commons convention, then percent-encode the rest).
    encoded_name = quote(record.image_filename.replace(" ", "_"))
    image_url = f"https://commons.wikimedia.org/wiki/Special:FilePath/{encoded_name}"

    duplicate = con.execute(
        "select 1 from player_images where player_uid = ? and image_url = ? and source = ?",
        [player_uid, image_url, record.source],
    ).fetchone()
    if duplicate is not None:
        return "skipped_duplicate"  # idempotent re-run: never insert the same image twice

    existing_count = con.execute(
        "select count(*) from player_images where player_uid = ?", [player_uid]
    ).fetchone()[0]
    is_primary = existing_count == 0
    next_id = _next_id(con, "player_images")
    con.execute(
        "insert into player_images (id, player_uid, image_url, source, license, attribution, is_primary) "
        "values (?, ?, ?, ?, ?, ?, ?)",
        [next_id, player_uid, image_url, record.source, record.license, record.attribution, is_primary],
    )
    return "inserted"
