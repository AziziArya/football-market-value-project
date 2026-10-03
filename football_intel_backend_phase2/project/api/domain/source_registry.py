"""Server-side source registry: the ONLY place that decides data_origin / liveness (decision D8).
dataset_version text is never parsed for origin. Change an entry deliberately when real data arrives."""
from __future__ import annotations

from dataclasses import dataclass

from api.domain.enums import DataOrigin, FreshnessRole


@dataclass(frozen=True)
class SourceInfo:
    name: str
    role: FreshnessRole
    data_origin: DataOrigin
    live_capable: bool = False       # True only once a provider really queries an external endpoint


# Order = order of the data-freshness response.
SOURCES: tuple[SourceInfo, ...] = (
    SourceInfo("ea_fc26", FreshnessRole.PLAYER_ATTRIBUTES, DataOrigin.REAL_FULL),
    SourceInfo("transfermarkt_dataset", FreshnessRole.MARKET_VALUE, DataOrigin.SAMPLE_FIXTURE),
    SourceInfo("wikidata", FreshnessRole.ENRICHMENT, DataOrigin.SAMPLE_FIXTURE),
)
_BY_NAME = {s.name: s for s in SOURCES}

# Capabilities that do not exist yet (honest constants until their own steps).
INJURY_SOURCE_INTEGRATED = False
MODEL_INTEGRATED = False


def origin_for(source: str | None) -> DataOrigin:
    info = _BY_NAME.get(source or "")
    return info.data_origin if info else DataOrigin.UNKNOWN      # unknown is NEVER presented as real


def is_live(source: str | None) -> bool:
    info = _BY_NAME.get(source or "")
    return bool(info and info.live_capable)


# Decision D7: player images may only come from Wikidata/Commons. Any other source (e.g. SoFIFA face URLs) is never read.
IMAGE_SOURCES: tuple[str, ...] = ("wikidata",)
# LinkState source names -> registry names (used for `reference_data_origin`).
LINK_REFERENCE_SOURCE = {"transfermarkt": "transfermarkt_dataset", "wikidata": "wikidata"}
