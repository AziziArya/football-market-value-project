"""
Tiny migration runner for the DuckDB-backed unified database.

No framework (Alembic is overkill for DuckDB + a handful of SQL files).
Applies any file in db/migrations/*.sql whose filename version isn't
already recorded in schema_migrations, in filename order.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DEFAULT_DB_PATH = Path(__file__).parents[1] / "data" / "processed" / "unified" / "football_intel.duckdb"


def _applied_versions(con: duckdb.DuckDBPyConnection) -> set[str]:
    exists = con.execute(
        "select count(*) from information_schema.tables where table_name = 'schema_migrations'"
    ).fetchone()[0]
    if not exists:
        return set()
    rows = con.execute("select version from schema_migrations").fetchall()
    return {r[0] for r in rows}


def run_migrations(db_path: Path = DEFAULT_DB_PATH) -> list[str]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        applied = _applied_versions(con)
        applied_now = []
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = sql_file.stem
            if version in applied:
                continue
            sql = sql_file.read_text(encoding="utf-8")
            con.execute("BEGIN")
            try:
                con.execute(sql)
                con.execute(
                    "insert into schema_migrations (version, applied_at) values (?, ?)",
                    [version, datetime.now(timezone.utc)],
                )
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
            applied_now.append(version)
        return applied_now
    finally:
        con.close()


if __name__ == "__main__":
    applied = run_migrations()
    if applied:
        print(f"applied migrations: {applied}")
    else:
        print("no pending migrations")
    sys.exit(0)
