"""PlayerSummary and its blocks (contract components PlayerSummary / PlayerCore). Reused by later endpoints."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from api.domain.enums import Availability, DataOrigin, EntityKind, InjuryStatus, MatchStatus
from api.domain.player_id import PLAYER_ID_PATTERN  # noqa: F401  (re-exported for routers)
from api.schemas.common import ResponseMeta


class _Model(BaseModel):
    # every field is always serialized (null included), so the generated OpenAPI must list all of them as required
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class SourceProvenance(_Model):
    source: str
    dataset_version: str | None = None
    fetched_at: datetime | None = None
    data_origin: DataOrigin


class LinkState(_Model):
    status: MatchStatus
    confidence: float | None = None
    matched_on: list[str] | None = None
    reference_data_origin: DataOrigin
    review_pending: bool = False


class Links(_Model):
    transfermarkt: LinkState
    wikidata: LinkState


class SourceIds(_Model):
    ea_fc26_id: int
    transfermarkt_id: int | None = None
    wikidata_id: str | None = None


class Identity(_Model):
    entity_kind: EntityKind
    canonical_player_uid: str | None = None
    source_ids: SourceIds
    links: Links


class EaIngameValue(_Model):
    availability: Availability
    amount_eur: int | None = None
    provenance: SourceProvenance | None = None


class SourceMarketValue(_Model):
    availability: Availability
    amount_eur: int | None = None
    valuation_date: date | None = None
    provenance: SourceProvenance | None = None


class ModelEstimate(_Model):
    availability: Availability
    target: Literal["EA_INGAME_VALUE"]
    amount_eur: int | None = None
    model_version: str | None = None


class Values(_Model):
    ea_ingame_value: EaIngameValue
    source_market_value: SourceMarketValue
    model_estimate: ModelEstimate


class InjuryBlock(_Model):
    status: InjuryStatus
    checked_at: datetime | None = None


class PlayerImage(_Model):
    url: str
    source: Literal["wikidata"]
    license: str | None = None
    attribution: str | None = None


class PlayerSummary(_Model):
    id: str
    display_name: str
    position: str | None = None
    club: str | None = None
    nationality: str | None = None
    date_of_birth: str | None = None
    preferred_foot: str | None = None
    overall_rating: int
    potential: int
    identity: Identity
    image: PlayerImage | None = None
    values: Values
    injury: InjuryBlock


class PlayerProfile(PlayerSummary):
    """getPlayer response: the SAME PlayerSummary as search, plus provenance and meta."""
    provenance: list[SourceProvenance]
    meta: ResponseMeta
