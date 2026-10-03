"""Repository-level errors. duckdb exceptions never leave the repositories package."""
from __future__ import annotations

import functools

import duckdb


class DatabaseUnavailable(Exception):
    """The database cannot be opened/read. Message is for logs only (may contain paths) - never sent to clients."""


def translate_db_errors(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DatabaseUnavailable:
            raise
        except duckdb.Error as exc:
            raise DatabaseUnavailable(f"{fn.__name__}: {exc}") from exc
    return wrapper
