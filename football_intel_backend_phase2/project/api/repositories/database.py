"""The ONLY database access point of the API: a READ-ONLY DuckDB connection (AD-1, AD-3)."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from api.readmodels.definitions import PING
from api.repositories.exceptions import DatabaseUnavailable, translate_db_errors


class Database:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._con: duckdb.DuckDBPyConnection | None = None

    @translate_db_errors
    def open(self) -> None:
        if not self._path.is_file():
            raise DatabaseUnavailable(f"database file not found: {self._path}")
        self._con = duckdb.connect(str(self._path), read_only=True)   # never read-write

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    @contextmanager
    def cursor(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """One cursor per request/unit of work (AD-3)."""
        if self._con is None:
            raise DatabaseUnavailable("database is not open")
        try:
            cur = self._con.cursor()
        except duckdb.Error as exc:
            raise DatabaseUnavailable(f"cursor: {exc}") from exc
        try:
            yield cur
        finally:
            cur.close()

    def ping(self) -> bool:
        try:
            with self.cursor() as cur:
                return cur.execute(PING).fetchone() == (1,)
        except (DatabaseUnavailable, duckdb.Error):
            return False
