"""
scripts/benchmark_loaders.py

Compares the OLD per-record loading path (load_ea_attributes called once
per record, each doing its own SELECT+INSERT/UPDATE) against the NEW
batched path (batch_load_ea_attributes called once with all records) —
on the real 16,107-record EA FC26 dataset, same validate()/normalize()
output for both, only the loading strategy differs.

Usage: python3 scripts/benchmark_loaders.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import duckdb

from db.migrate import run_migrations
from ingestion.loader import batch_load_ea_attributes, load_ea_attributes, load_identity_fields
from ingestion.normalizer import normalize
from ingestion.providers.ea_fc26 import EAFC26Provider
from ingestion.validator import validate


def run_old_path(records, db_path: Path) -> float:
    run_migrations(db_path)
    con = duckdb.connect(str(db_path))
    start = time.perf_counter()
    for r in records:
        if not validate(r).is_valid:
            continue
        out = normalize(r)
        load_identity_fields(con, out.identity_fields)  # 1 round trip per record (small list)
        if out.ea_attributes:
            load_ea_attributes(con, out.ea_attributes)  # 2 round trips per record
    elapsed = time.perf_counter() - start
    con.close()
    return elapsed


def run_new_path(records, db_path: Path) -> float:
    run_migrations(db_path)
    con = duckdb.connect(str(db_path))
    start = time.perf_counter()
    all_fields = []
    all_ea = []
    for r in records:
        if not validate(r).is_valid:
            continue
        out = normalize(r)
        all_fields.extend(out.identity_fields)
        if out.ea_attributes:
            all_ea.append(out.ea_attributes)
    load_identity_fields(con, all_fields)          # 1 round trip total
    batch_load_ea_attributes(con, all_ea)           # 2 round trips total
    elapsed = time.perf_counter() - start
    con.close()
    return elapsed


def main():
    provider = EAFC26Provider()
    records = list(provider.fetch())
    print(f"benchmarking against {len(records)} real EA FC26 records\n")

    old_db = Path("/tmp/benchmark_old.duckdb")
    new_db = Path("/tmp/benchmark_new.duckdb")
    old_db.unlink(missing_ok=True)
    new_db.unlink(missing_ok=True)

    old_time = run_old_path(records, old_db)
    print(f"OLD (per-record round trips):  {old_time:.2f}s")

    new_time = run_new_path(records, new_db)
    print(f"NEW (batched):                  {new_time:.2f}s")

    print(f"\nspeedup: {old_time / new_time:.1f}x")

    old_db.unlink(missing_ok=True)
    new_db.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
