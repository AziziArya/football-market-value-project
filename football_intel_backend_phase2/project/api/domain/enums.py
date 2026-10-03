"""Contract enums (a test compares every one with api_contract/openapi.json). stdlib only."""
from enum import Enum


class DataOrigin(str, Enum):
    REAL_FULL = "REAL_FULL"
    SAMPLE_FIXTURE = "SAMPLE_FIXTURE"
    UNKNOWN = "UNKNOWN"


class Availability(str, Enum):
    AVAILABLE = "AVAILABLE"
    NOT_MATCHED = "NOT_MATCHED"
    NO_SOURCE_DATA = "NO_SOURCE_DATA"
    NOT_YET_INTEGRATED = "NOT_YET_INTEGRATED"


class MatchStatus(str, Enum):
    MATCHED = "MATCHED"
    PROBABLE_MATCH = "PROBABLE_MATCH"
    AMBIGUOUS = "AMBIGUOUS"
    UNMATCHED = "UNMATCHED"
    NOT_EVALUATED = "NOT_EVALUATED"


class EntityKind(str, Enum):
    CANONICAL = "CANONICAL"
    EA_ONLY = "EA_ONLY"


class FreshnessRole(str, Enum):
    PLAYER_ATTRIBUTES = "PLAYER_ATTRIBUTES"
    MARKET_VALUE = "MARKET_VALUE"
    ENRICHMENT = "ENRICHMENT"
    INJURY = "INJURY"
    MODEL = "MODEL"


class ProblemCode(str, Enum):
    INVALID_PARAMETER = "INVALID_PARAMETER"
    INVALID_PLAYER_ID = "INVALID_PLAYER_ID"
    PLAYER_NOT_FOUND = "PLAYER_NOT_FOUND"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class MatchQuality(str, Enum):
    EXACT = "EXACT"
    PREFIX = "PREFIX"
    TOKEN_PREFIX = "TOKEN_PREFIX"
    SUBSTRING = "SUBSTRING"


class SearchSort(str, Enum):
    relevance = "relevance"
    overall_desc = "overall_desc"
    name_asc = "name_asc"


class InjuryStatus(str, Enum):
    NO_SOURCE_AVAILABLE = "NO_SOURCE_AVAILABLE"
    CONFIRMED_NO_INJURIES = "CONFIRMED_NO_INJURIES"
    HAS_RECORDS = "HAS_RECORDS"
    NOT_EVALUATED = "NOT_EVALUATED"
