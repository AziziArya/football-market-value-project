from __future__ import annotations

from api.readmodels.definitions import (
    INV_EA_ATTRIBUTE_COUNT, INV_ML_COLUMNS_IN_USE, LIST_OBJECTS, REQUIRED_OBJECTS,
)
from api.repositories.exceptions import translate_db_errors


@translate_db_errors
def missing_objects(cur) -> tuple[str, ...]:
    present = {r[0] for r in cur.execute(LIST_OBJECTS).fetchall()}
    return tuple(o for o in REQUIRED_OBJECTS if o not in present)


@translate_db_errors
def ea_attribute_count(cur) -> int:
    return cur.execute(INV_EA_ATTRIBUTE_COUNT).fetchone()[0]


@translate_db_errors
def ml_columns_in_use(cur) -> int:
    return cur.execute(INV_ML_COLUMNS_IN_USE).fetchone()[0]
