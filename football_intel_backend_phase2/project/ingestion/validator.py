"""
Validation layer.

Sits between providers and the normalizer. Pure functions only —
never mutates a RawRecord, never fabricates a missing value. A record
that fails validation is reported with reasons and left out of the
normalized output; it is never silently dropped or silently "fixed".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator

from ingestion.providers.base import RawRecord


@dataclass(frozen=True)
class FieldSpec:
    required: bool = False
    type_check: Callable[[Any], bool] | None = None
    range_check: tuple[float, float] | None = None  # inclusive (min, max), numeric fields only


@dataclass(frozen=True)
class ValidationSpec:
    fields: dict[str, FieldSpec] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationResult:
    record: RawRecord
    is_valid: bool
    errors: list[str]


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_string(v: Any) -> bool:
    return isinstance(v, str) and len(v.strip()) > 0


# ═══════════════════════════════════════════════════════════
# per-(source, record_type) schemas
# only fields we actively check are listed — unknown extra fields
# in the payload are ignored by the validator (that's fine; they
# just won't be picked up by the normalizer either unless mapped).
# ═══════════════════════════════════════════════════════════
SCHEMA_REGISTRY: dict[tuple[str, str], ValidationSpec] = {
    ("ea_fc26", "player"): ValidationSpec(fields={
        "id": FieldSpec(required=True, type_check=_is_number),
        "player_name": FieldSpec(required=True, type_check=_is_string),
        "overall_rating": FieldSpec(required=True, type_check=_is_number, range_check=(1, 99)),
        "value_eur": FieldSpec(required=True, type_check=_is_number, range_check=(0, 1_000_000_000)),
        "age": FieldSpec(required=False, type_check=_is_number, range_check=(14, 55)),
    }),
    ("transfermarkt_dataset", "player"): ValidationSpec(fields={
        "player_id": FieldSpec(required=True, type_check=_is_number),
        "name": FieldSpec(required=True, type_check=_is_string),
    }),
    ("transfermarkt_dataset", "valuation"): ValidationSpec(fields={
        "player_id": FieldSpec(required=True, type_check=_is_number),
        "date": FieldSpec(required=True, type_check=_is_string),
        "market_value_in_eur": FieldSpec(required=True, type_check=_is_number, range_check=(1, 1_000_000_000)),
    }),
    ("transfermarkt_dataset", "transfer"): ValidationSpec(fields={
        "player_id": FieldSpec(required=True, type_check=_is_number),
        "transfer_date": FieldSpec(required=True, type_check=_is_string),
    }),
    ("transfermarkt_dataset", "appearance"): ValidationSpec(fields={
        "player_id": FieldSpec(required=True, type_check=_is_number),
        "game_date": FieldSpec(required=True, type_check=_is_string),
        "minutes_played": FieldSpec(required=False, type_check=_is_number, range_check=(0, 120)),
    }),
    ("wikidata", "player"): ValidationSpec(fields={
        "qid": FieldSpec(required=True, type_check=_is_string),
        "name": FieldSpec(required=True, type_check=_is_string),
        # date_of_birth, country_of_citizenship, image_filename, image_license,
        # image_attribution are all OPTIONAL and unchecked here — Wikidata
        # coverage is inconsistent per-item, and an absent field must stay
        # NULL downstream, never inferred.
    }),
}


def validate(record: RawRecord) -> ValidationResult:
    """Validate a single record. Never mutates record.payload."""
    key = (record.source, record.record_type)
    spec = SCHEMA_REGISTRY.get(key)
    errors: list[str] = []

    if spec is None:
        errors.append(f"no validation schema registered for source={record.source!r} record_type={record.record_type!r}")
        return ValidationResult(record=record, is_valid=False, errors=errors)

    for field_name, field_spec in spec.fields.items():
        present = field_name in record.payload and record.payload[field_name] is not None
        if field_spec.required and not present:
            errors.append(f"missing required field: {field_name}")
            continue
        if not present:
            continue  # optional and absent — fine

        value = record.payload[field_name]
        if field_spec.type_check is not None and not field_spec.type_check(value):
            errors.append(f"field {field_name!r} failed type check (got {type(value).__name__}: {value!r})")
            continue
        if field_spec.range_check is not None:
            low, high = field_spec.range_check
            if not (low <= value <= high):
                errors.append(f"field {field_name!r} value {value!r} out of range [{low}, {high}]")

    return ValidationResult(record=record, is_valid=len(errors) == 0, errors=errors)


def validate_batch(records: Iterable[RawRecord]) -> Iterator[ValidationResult]:
    for record in records:
        yield validate(record)
