"""
Pipeline configuration.

All paths/toggles the orchestrator needs, in one place, overridable via
environment variables so `scripts/run_ingestion.py` never hardcodes a
path or provider list inline.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "processed" / "unified" / "football_intel.duckdb"
DEFAULT_EA_FC26_CSV_PATH = PROJECT_ROOT / "data" / "raw" / "ea_fc26" / "fc26_merged_clean.csv"
DEFAULT_TRANSFERMARKT_DUMP_DIR = PROJECT_ROOT / "data" / "raw" / "transfermarkt_dataset" / "sample_2026-07-06"
DEFAULT_WIKIDATA_FIXTURE_PATH = PROJECT_ROOT / "data" / "raw" / "wikidata" / "sample" / "players.csv"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "data" / "processed" / "unified" / "reports"

ALL_PROVIDERS = ("ea_fc26", "transfermarkt_dataset", "wikidata")
# enrichment providers are non-blocking: their failure never aborts the
# core pipeline or flips the overall run to success=False — see
# scripts/run_ingestion.py's handling.
ENRICHMENT_PROVIDERS = ("wikidata",)


@dataclass
class PipelineConfig:
    db_path: Path = DEFAULT_DB_PATH
    enabled_providers: tuple[str, ...] = ALL_PROVIDERS
    ea_fc26_csv_path: Path = DEFAULT_EA_FC26_CSV_PATH
    transfermarkt_dump_dir: Path = DEFAULT_TRANSFERMARKT_DUMP_DIR
    transfermarkt_dataset_version: str | None = None  # override; defaults to dump dir's own folder name
    wikidata_fixture_path: Path | None = DEFAULT_WIKIDATA_FIXTURE_PATH
    stop_on_provider_failure: bool = True  # safe default — never proceed to matching on known-incomplete data
    report_dir: Path = DEFAULT_REPORT_DIR

    def __post_init__(self):
        unknown = set(self.enabled_providers) - set(ALL_PROVIDERS)
        if unknown:
            raise ValueError(f"unknown provider(s) in config: {unknown}. Known providers: {ALL_PROVIDERS}")

    @classmethod
    def from_env(cls) -> "PipelineConfig":
        """Build config from environment variables, falling back to defaults
        for anything not set. Every field is independently overridable:
            FOOTBALL_INTEL_DB_PATH
            FOOTBALL_INTEL_PROVIDERS          (comma-separated, e.g. "ea_fc26,transfermarkt_dataset")
            FOOTBALL_INTEL_EA_CSV_PATH
            FOOTBALL_INTEL_TM_DUMP_DIR
            FOOTBALL_INTEL_TM_DATASET_VERSION
            FOOTBALL_INTEL_STOP_ON_FAILURE    ("true"/"false")
            FOOTBALL_INTEL_REPORT_DIR
        """
        kwargs = {}
        if v := os.environ.get("FOOTBALL_INTEL_DB_PATH"):
            kwargs["db_path"] = Path(v)
        if v := os.environ.get("FOOTBALL_INTEL_PROVIDERS"):
            kwargs["enabled_providers"] = tuple(p.strip() for p in v.split(",") if p.strip())
        if v := os.environ.get("FOOTBALL_INTEL_EA_CSV_PATH"):
            kwargs["ea_fc26_csv_path"] = Path(v)
        if v := os.environ.get("FOOTBALL_INTEL_TM_DUMP_DIR"):
            kwargs["transfermarkt_dump_dir"] = Path(v)
        if v := os.environ.get("FOOTBALL_INTEL_TM_DATASET_VERSION"):
            kwargs["transfermarkt_dataset_version"] = v
        if v := os.environ.get("FOOTBALL_INTEL_STOP_ON_FAILURE"):
            kwargs["stop_on_provider_failure"] = v.strip().lower() in ("1", "true", "yes")
        if v := os.environ.get("FOOTBALL_INTEL_REPORT_DIR"):
            kwargs["report_dir"] = Path(v)
        return cls(**kwargs)
