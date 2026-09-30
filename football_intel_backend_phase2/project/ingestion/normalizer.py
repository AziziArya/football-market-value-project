"""
Normalization layer.

Takes VALID RawRecord objects (already passed ingestion/validator.py) and
converts source-specific field names into the canonical schema defined in
db/migrations/0001_init.sql — while keeping full provenance and NEVER
merging/overwriting values from different sources.

Player identity resolution (which records belong to the same real person)
is matching/identity.py's job (Step 1.5), not this module's. Everything
here is keyed by (source, source_record_id) — no player_uid exists yet.

HARD RULES enforced by this file (see tests for guards):
  1. EA FC26's `value_eur` becomes `value_eur_ingame` — it NEVER appears
     under a generic "value" or "player_value" key anywhere downstream.
  2. Transfermarkt market values only ever land in MarketValueRecord
     (-> market_value_history table), never mixed with EA's field.
  3. No injury normalization exists here at all, because no injury
     provider exists yet. There is nothing to fabricate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ingestion.providers.base import RawRecord

FORBIDDEN_GENERIC_FIELD_NAMES = {"value", "player_value", "market_value"}


@dataclass(frozen=True)
class IdentityFieldValue:
    """Maps 1:1 onto the player_field_values table (player_uid filled in later by matching)."""

    source: str
    source_record_id: str
    field_name: str
    field_value: str
    fetched_at: datetime
    dataset_version: str | None


@dataclass(frozen=True)
class EAAttributesRecord:
    """Maps onto ea_fc26_attributes. value_eur_ingame is explicit and final —
    this is the ONLY place EA's in-game value is allowed to live."""

    ea_fc26_id: int
    overall_rating: int | None
    potential: int | None
    value_eur_ingame: int
    source: str = "ea_fc26"
    dataset_version: str | None = None


@dataclass(frozen=True)
class MarketValueRecord:
    """Maps onto market_value_history. Historical/static — never 'live'."""

    source_record_id: str
    player_id_in_source: str
    value_eur: int
    valuation_date: date
    source: str
    dataset_version: str


@dataclass(frozen=True)
class TransferRecord:
    source_record_id: str
    player_id_in_source: str
    transfer_date: date | None
    from_club: str | None
    to_club: str | None
    fee_eur: int | None
    is_loan: bool | None
    source: str
    dataset_version: str | None


@dataclass(frozen=True)
class AppearanceRecord:
    source_record_id: str
    player_id_in_source: str
    game_date: date | None
    competition: str | None
    minutes_played: int | None
    goals: int | None
    assists: int | None
    source: str
    dataset_version: str | None


@dataclass(frozen=True)
class ImageRecord:
    """Maps onto player_images. license/attribution stay None rather than
    guessed when Wikidata/Commons doesn't supply them — never fabricated."""

    source_record_id: str  # QID
    image_filename: str | None
    license: str | None
    attribution: str | None
    source: str = "wikidata"
    dataset_version: str | None = None


@dataclass(frozen=True)
class NormalizationResult:
    identity_fields: list[IdentityFieldValue]
    ea_attributes: EAAttributesRecord | None = None
    market_value: MarketValueRecord | None = None
    transfer: TransferRecord | None = None
    appearance: AppearanceRecord | None = None
    image: ImageRecord | None = None


# ═══════════════════════════════════════════════════════════
# canonical identity field mapping per source
# (raw_field_name -> canonical_field_name), player-type records only.
# EA's value_eur is deliberately EXCLUDED here — it is handled solely
# by normalize_ea_attributes(), never as a generic identity field.
# ═══════════════════════════════════════════════════════════
IDENTITY_FIELD_MAP: dict[str, dict[str, str]] = {
    "ea_fc26": {
        "player_name": "display_name",
        "nationality": "nationality",
        "team": "club",
        "birthdate": "date_of_birth",
        "position": "position",
        "preferred_foot": "preferred_foot",
    },
    "transfermarkt_dataset": {
        "name": "display_name",
        "country_of_citizenship": "nationality",
        "current_club_name": "club",
        "date_of_birth": "date_of_birth",
        "sub_position": "position",
    },
    "wikidata": {
        "name": "display_name",
        "country_of_citizenship": "nationality",
        "date_of_birth": "date_of_birth",
        # deliberately no club/position mapping — Wikidata isn't queried
        # for those fields per the approved priority-field list
    },
}


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def normalize_identity_fields(record: RawRecord) -> list[IdentityFieldValue]:
    field_map = IDENTITY_FIELD_MAP.get(record.source, {})
    results: list[IdentityFieldValue] = []
    for raw_name, canonical_name in field_map.items():
        if canonical_name in FORBIDDEN_GENERIC_FIELD_NAMES:
            raise AssertionError(
                f"canonical field name {canonical_name!r} is forbidden — "
                "value fields must go through their dedicated record types"
            )
        if raw_name in record.payload and record.payload[raw_name] is not None:
            results.append(IdentityFieldValue(
                source=record.source,
                source_record_id=record.source_record_id,
                field_name=canonical_name,
                field_value=str(record.payload[raw_name]),
                fetched_at=record.fetched_at,
                dataset_version=record.dataset_version,
            ))
    return results


def normalize_ea_attributes(record: RawRecord) -> EAAttributesRecord:
    assert record.source == "ea_fc26" and record.record_type == "player"
    p = record.payload
    return EAAttributesRecord(
        ea_fc26_id=int(p["id"]),
        overall_rating=int(p["overall_rating"]) if p.get("overall_rating") is not None else None,
        potential=int(p["potential"]) if p.get("potential") is not None else None,
        value_eur_ingame=int(p["value_eur"]),  # explicit rename — the ONLY place this happens
        dataset_version=record.dataset_version,
    )


def normalize_market_value(record: RawRecord) -> MarketValueRecord:
    assert record.source == "transfermarkt_dataset" and record.record_type == "valuation"
    p = record.payload
    valuation_date = _to_date(p["date"])
    if valuation_date is None:
        raise ValueError(f"could not parse valuation date from {p['date']!r}")
    return MarketValueRecord(
        source_record_id=record.source_record_id,
        player_id_in_source=str(p["player_id"]),
        value_eur=int(p["market_value_in_eur"]),
        valuation_date=valuation_date,
        source=record.source,
        dataset_version=record.dataset_version or "unknown",
    )


def normalize_transfer(record: RawRecord) -> TransferRecord:
    assert record.source == "transfermarkt_dataset" and record.record_type == "transfer"
    p = record.payload
    fee = p.get("transfer_fee")
    return TransferRecord(
        source_record_id=record.source_record_id,
        player_id_in_source=str(p["player_id"]),
        transfer_date=_to_date(p.get("transfer_date")),
        from_club=p.get("from_club_name"),
        to_club=p.get("to_club_name"),
        fee_eur=int(fee) if fee is not None else None,
        is_loan=bool(p["is_loan"]) if p.get("is_loan") is not None else None,
        source=record.source,
        dataset_version=record.dataset_version,
    )


def normalize_appearance(record: RawRecord) -> AppearanceRecord:
    assert record.source == "transfermarkt_dataset" and record.record_type == "appearance"
    p = record.payload
    return AppearanceRecord(
        source_record_id=record.source_record_id,
        player_id_in_source=str(p["player_id"]),
        game_date=_to_date(p.get("game_date")),
        competition=p.get("competition"),
        minutes_played=int(p["minutes_played"]) if p.get("minutes_played") is not None else None,
        goals=int(p["goals"]) if p.get("goals") is not None else None,
        assists=int(p["assists"]) if p.get("assists") is not None else None,
        source=record.source,
        dataset_version=record.dataset_version,
    )


def normalize_wikidata_image(record: RawRecord) -> ImageRecord:
    assert record.source == "wikidata" and record.record_type == "player"
    p = record.payload
    return ImageRecord(
        source_record_id=record.source_record_id,
        image_filename=p.get("image_filename"),
        license=p.get("image_license"),        # None stays None — never guessed
        attribution=p.get("image_attribution"),  # None stays None — never guessed
        dataset_version=record.dataset_version,
    )


_DISPATCH = {
    ("ea_fc26", "player"): "ea_player",
    ("transfermarkt_dataset", "player"): "tm_player",
    ("transfermarkt_dataset", "valuation"): "tm_valuation",
    ("transfermarkt_dataset", "transfer"): "tm_transfer",
    ("transfermarkt_dataset", "appearance"): "tm_appearance",
    ("wikidata", "player"): "wikidata_player",
}


def normalize(record: RawRecord) -> NormalizationResult:
    """Single entry point. Dispatches by (source, record_type).
    Raises for unknown combinations rather than guessing."""
    kind = _DISPATCH.get((record.source, record.record_type))
    if kind is None:
        raise NotImplementedError(
            f"no normalizer registered for source={record.source!r} record_type={record.record_type!r}"
        )

    if kind == "ea_player":
        return NormalizationResult(
            identity_fields=normalize_identity_fields(record),
            ea_attributes=normalize_ea_attributes(record),
        )
    if kind == "tm_player":
        return NormalizationResult(identity_fields=normalize_identity_fields(record))
    if kind == "tm_valuation":
        return NormalizationResult(identity_fields=[], market_value=normalize_market_value(record))
    if kind == "tm_transfer":
        return NormalizationResult(identity_fields=[], transfer=normalize_transfer(record))
    if kind == "tm_appearance":
        return NormalizationResult(identity_fields=[], appearance=normalize_appearance(record))
    if kind == "wikidata_player":
        return NormalizationResult(
            identity_fields=normalize_identity_fields(record),
            image=normalize_wikidata_image(record),
        )

    raise NotImplementedError(kind)  # pragma: no cover — unreachable given _DISPATCH
