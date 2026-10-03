from __future__ import annotations

from pydantic import ConfigDict

from api.domain.enums import EntityKind, MatchQuality, SearchSort  # noqa: F401  (re-exported for routers)
from api.schemas.common import ResponseMeta
from api.schemas.player import PlayerSummary, _Model


class SearchMatch(_Model):
    quality: MatchQuality
    matched_alias: str | None = None
    matched_alias_source: str | None = None


class SearchItem(PlayerSummary):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)
    match: SearchMatch


class SearchResponse(_Model):
    items: list[SearchItem]
    total: int
    limit: int
    offset: int
    next_offset: int | None = None
    query_normalized: str
    meta: ResponseMeta
