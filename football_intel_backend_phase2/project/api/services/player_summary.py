"""Builds the contract PlayerSummary from RM1/RM2 (in memory) + batch-loaded canonical details.
Honesty rules live here: nothing is inferred for EA_ONLY players; every empty block says WHY (Availability)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from api.domain.enums import Availability, DataOrigin, EntityKind, InjuryStatus
from api.domain.link_rules import LINKED
from api.domain.models import ImageRow, InjuryStateRow, LinkStateRow, MarketValueRow, PlayerIndexRow
from api.domain.source_registry import LINK_REFERENCE_SOURCE, MODEL_INTEGRATED, origin_for
from api.repositories import player_detail_repository
from api.schemas.player import (
    EaIngameValue, Identity, InjuryBlock, LinkState, Links, ModelEstimate, PlayerImage, PlayerSummary,
    SourceIds, SourceMarketValue, SourceProvenance, Values,
)


@dataclass(frozen=True)
class CanonicalDetails:
    market_values: Mapping[str, MarketValueRow]
    injuries: Mapping[str, InjuryStateRow]
    images: Mapping[str, ImageRow]


EMPTY_DETAILS = CanonicalDetails({}, {}, {})


def load_details(cur, player_uids: Sequence[str]) -> CanonicalDetails:
    """At most THREE queries for the whole page, independent of page size (and none when no canonical player is on it)."""
    uids = sorted(set(player_uids))
    if not uids:
        return EMPTY_DETAILS
    return CanonicalDetails(player_detail_repository.latest_market_values(cur, uids),
                            player_detail_repository.injury_states(cur, uids),
                            player_detail_repository.primary_images(cur, uids))


def _seconds(ts):
    return ts.replace(microsecond=0) if ts is not None else None


def _link(row: LinkStateRow) -> LinkState:
    return LinkState(status=row.status, confidence=row.confidence,
                     matched_on=list(row.matched_on) if row.matched_on is not None else None,
                     reference_data_origin=origin_for(LINK_REFERENCE_SOURCE[row.source]), review_pending=row.review_pending)


def build_summary(row: PlayerIndexRow, links: Mapping[str, LinkStateRow], details: CanonicalDetails) -> PlayerSummary:
    tm, wd = links["transfermarkt"], links["wikidata"]
    canonical = row.entity_kind is EntityKind.CANONICAL
    uid = row.canonical_player_uid
    identity = Identity(
        entity_kind=row.entity_kind, canonical_player_uid=uid,
        # source ids are exposed ONLY while the link itself is MATCHED/PROBABLE_MATCH (contract SourceIds)
        source_ids=SourceIds(ea_fc26_id=row.ea_fc26_id,
                             transfermarkt_id=row.transfermarkt_id if tm.status in LINKED else None,
                             wikidata_id=row.wikidata_id if wd.status in LINKED else None),
        links=Links(transfermarkt=_link(tm), wikidata=_link(wd)))

    ea_value = EaIngameValue(
        availability=Availability.AVAILABLE if row.value_eur_ingame is not None else Availability.NO_SOURCE_DATA,
        amount_eur=row.value_eur_ingame,
        provenance=SourceProvenance(source="ea_fc26", dataset_version=row.dataset_version, fetched_at=_seconds(row.fetched_at),
                                    data_origin=origin_for("ea_fc26")))

    mv = details.market_values.get(uid) if canonical else None
    if not canonical:
        market = SourceMarketValue(availability=Availability.NOT_MATCHED)            # never inferred for EA-only players
    elif mv is None:
        market = SourceMarketValue(availability=Availability.NO_SOURCE_DATA)
    else:
        market = SourceMarketValue(
            availability=Availability.AVAILABLE, amount_eur=mv.value_eur, valuation_date=mv.valuation_date,
            provenance=SourceProvenance(source=mv.source, dataset_version=mv.dataset_version,
                                        fetched_at=_seconds(mv.imported_at), data_origin=origin_for(mv.source)))

    model = ModelEstimate(availability=Availability.AVAILABLE if MODEL_INTEGRATED else Availability.NOT_YET_INTEGRATED,
                          target="EA_INGAME_VALUE")                                   # PLANNED block: always empty today

    inj = details.injuries.get(uid) if canonical else None
    injury = (InjuryBlock(status=InjuryStatus(inj.status), checked_at=_seconds(inj.checked_at)) if inj
              else InjuryBlock(status=InjuryStatus.NOT_EVALUATED))                     # EA-only / no row => not evaluated

    img = details.images.get(uid) if canonical else None
    image = PlayerImage(url=img.url, source=img.source, license=img.license, attribution=img.attribution) if img else None

    return PlayerSummary(
        id=f"ea:{row.ea_fc26_id}", display_name=row.display_name or "", position=row.position, club=row.club,
        nationality=row.nationality, date_of_birth=row.date_of_birth, preferred_foot=row.preferred_foot,
        overall_rating=row.overall_rating, potential=row.potential, identity=identity, image=image,
        values=Values(ea_ingame_value=ea_value, source_market_value=market, model_estimate=model), injury=injury)


def contains_sample_data(summary: PlayerSummary) -> bool:
    """True when something SHOWN comes from a sample/fixture source: a market value, an image, or a MATCHED/PROBABLE
    link (its ids). AMBIGUOUS/UNMATCHED statuses show no sample data, so they do not count."""
    prov = summary.values.source_market_value.provenance
    if prov is not None and prov.data_origin is DataOrigin.SAMPLE_FIXTURE:
        return True
    if summary.image is not None and origin_for(summary.image.source) is DataOrigin.SAMPLE_FIXTURE:
        return True
    return any(l.status in LINKED and l.reference_data_origin is DataOrigin.SAMPLE_FIXTURE
               for l in (summary.identity.links.transfermarkt, summary.identity.links.wikidata))
