"""ApiConfig: environment only, no secrets. Separate from the pipeline's PipelineConfig on purpose."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

# Same default as the pipeline's DEFAULT_DB_PATH (a test keeps the two equal) without importing the pipeline.
DEFAULT_DB_PATH = Path(__file__).parents[1] / "data" / "processed" / "unified" / "football_intel.duckdb"
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"", "0", "false", "no", "off"}


@dataclass(frozen=True)
class ApiConfig:
    db_path: Path = DEFAULT_DB_PATH
    cors_origins: tuple[str, ...] = ()      # default: none (CORS deny-by-default)
    log_level: str = "INFO"
    enable_docs: bool = False               # /docs and /openapi.json only when explicitly enabled

    def __post_init__(self):
        if self.log_level.upper() not in logging.getLevelNamesMapping() or self.log_level.upper() == "NOTSET":
            raise ValueError(f"invalid log level: {self.log_level!r}")
        if "*" in self.cors_origins:
            raise ValueError("wildcard CORS origin is not allowed; list explicit origins")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ApiConfig":
        """FOOTBALL_INTEL_DB_PATH (shared with the pipeline), FOOTBALL_INTEL_API_CORS_ORIGINS (comma-separated),
        FOOTBALL_INTEL_API_LOG_LEVEL, FOOTBALL_INTEL_API_ENABLE_DOCS (true/false)."""
        env = os.environ if environ is None else environ
        kwargs: dict = {}
        if v := env.get("FOOTBALL_INTEL_DB_PATH"):
            kwargs["db_path"] = Path(v)
        if v := env.get("FOOTBALL_INTEL_API_CORS_ORIGINS"):
            kwargs["cors_origins"] = tuple(o.strip() for o in v.split(",") if o.strip())
        if v := env.get("FOOTBALL_INTEL_API_LOG_LEVEL"):
            kwargs["log_level"] = v.strip().upper()
        if "FOOTBALL_INTEL_API_ENABLE_DOCS" in env:
            raw = env["FOOTBALL_INTEL_API_ENABLE_DOCS"].strip().lower()
            if raw not in _TRUE | _FALSE:
                raise ValueError(f"FOOTBALL_INTEL_API_ENABLE_DOCS must be true/false, got {raw!r}")
            kwargs["enable_docs"] = raw in _TRUE
        return cls(**kwargs)
