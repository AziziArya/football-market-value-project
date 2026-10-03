"""Pydantic models mirroring api_contract/openapi.json (hand-written; a test diffs them against the contract)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ResponseMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: str
    generated_at: datetime
    contains_sample_data: bool


class ProblemError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parameter: str
    reason: str


class Problem(BaseModel):
    """RFC 9457 problem details; `errors` is omitted (not null) when there are none."""
    model_config = ConfigDict(extra="forbid")
    type: str
    title: str
    status: int
    code: str
    detail: str | None = None
    instance: str | None = None
    errors: list[ProblemError] | None = None
