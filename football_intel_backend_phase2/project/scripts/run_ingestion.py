"""
scripts/run_ingestion.py

Single reproducible entrypoint replacing manual interactive runs of:
    provider fetch → validate → normalize → identity matching → persistence

Usage:
    python3 scripts/run_ingestion.py
    python3 scripts/run_ingestion.py --providers ea_fc26
    python3 scripts/run_ingestion.py --db-path /tmp/test.duckdb --continue-on-error

Design rule enforced here: this file imports concrete providers ONLY in
PROVIDER_REGISTRY, and only calls them through the Provider ABC
(health_check(), fetch()). Everything else — validation, normalization,
matching, loading — reuses the exact same functions already built and
tested in ingestion/*, matching/* with zero provider-specific branching.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import duckdb

from config.settings import ENRICHMENT_PROVIDERS, PipelineConfig
from db.migrate import run_migrations
from ingestion.injury_loader import mark_no_injury_source_available
from ingestion.loader import (
    backfill_player_uid,
    batch_load_appearances,
    batch_load_ea_attributes,
    batch_load_market_values,
    batch_load_transfers,
    load_identity_fields,
)
from ingestion.normalizer import normalize
from ingestion.providers.base import Provider
from ingestion.providers.ea_fc26 import EAFC26Provider
from ingestion.providers.transfermarkt_dataset import TransfermarktDatasetProvider
from ingestion.providers.wikidata import WikidataProvider
from ingestion.validator import validate
from matching.identity import from_ea_record, from_transfermarkt_record, match_all
from matching.loader import create_player_records, persist_identity_matches
from matching.review_persistence import persist_review_queue
from matching.wikidata_enrichment import enrich_players_with_wikidata

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("run_ingestion")


# ═══════════════════════════════════════════════════════════
# provider registry — the ONLY place provider classes are named
# ═══════════════════════════════════════════════════════════

def _build_ea_fc26(config: PipelineConfig) -> Provider:
    return EAFC26Provider(csv_path=config.ea_fc26_csv_path)


def _build_transfermarkt_dataset(config: PipelineConfig) -> Provider:
    return TransfermarktDatasetProvider(
        dump_dir=config.transfermarkt_dump_dir,
        dataset_version=config.transfermarkt_dataset_version,
    )


def _build_wikidata(config: PipelineConfig) -> Provider:
    return WikidataProvider(fixture_path=config.wikidata_fixture_path)


PROVIDER_REGISTRY = {
    "ea_fc26": _build_ea_fc26,
    "transfermarkt_dataset": _build_transfermarkt_dataset,
    "wikidata": _build_wikidata,
}


class ProviderIngestionError(Exception):
    """Raised (and caught) when a provider fails during health_check() or fetch().
    Never lets a raw traceback stand in for a clear, attributed error."""

    def __init__(self, provider_name: str, original: Exception):
        self.provider_name = provider_name
        self.original = original
        super().__init__(f"provider {provider_name!r} failed: {original}")


# ═══════════════════════════════════════════════════════════
# report
# ═══════════════════════════════════════════════════════════

@dataclass
class IngestionReport:
    started_at: str
    finished_at: str | None = None
    success: bool = False
    fetched_per_source: dict[str, int] = field(default_factory=dict)
    valid_per_source: dict[str, int] = field(default_factory=dict)
    invalid_per_source: dict[str, int] = field(default_factory=dict)
    normalized_counts: dict[str, int] = field(default_factory=dict)  # identity_fields, ea_attributes, market_value, transfer, appearance
    matching_counts: dict[str, int] = field(default_factory=dict)    # MATCHED/PROBABLE_MATCH/AMBIGUOUS/UNMATCHED
    db_row_counts_after: dict[str, int] = field(default_factory=dict)
    provider_errors: dict[str, str] = field(default_factory=dict)
    enrichment_counts: dict[str, int] = field(default_factory=dict)
    enrichment_errors: dict[str, str] = field(default_factory=dict)  # non-blocking — never affects `success`

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, report_dir: Path) -> Path:
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = report_dir / f"ingestion_report_{timestamp}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    def print_summary(self):
        log.info("═" * 60)
        log.info("INGESTION REPORT — success=%s", self.success)
        for source in self.fetched_per_source:
            log.info(
                "  %-24s fetched=%-6d valid=%-6d invalid=%-6d",
                source, self.fetched_per_source.get(source, 0),
                self.valid_per_source.get(source, 0), self.invalid_per_source.get(source, 0),
            )
        if self.normalized_counts:
            log.info("  normalized: %s", self.normalized_counts)
        if self.matching_counts:
            log.info("  matching:   %s", self.matching_counts)
        if self.db_row_counts_after:
            log.info("  db rows:    %s", self.db_row_counts_after)
        if self.provider_errors:
            log.error("  provider errors: %s", self.provider_errors)
        if self.enrichment_counts:
            log.info("  enrichment: %s", self.enrichment_counts)
        if self.enrichment_errors:
            log.warning("  enrichment errors (non-blocking): %s", self.enrichment_errors)
        log.info("═" * 60)


# ═══════════════════════════════════════════════════════════
# per-provider ingestion (fetch → validate → normalize → load)
# ═══════════════════════════════════════════════════════════

def _ingest_ea_fc26(records: list, con: duckdb.DuckDBPyConnection, report: IngestionReport):
    report.fetched_per_source["ea_fc26"] = len(records)
    valid, invalid = 0, 0
    all_identity_fields = []
    all_ea_attributes = []
    for r in records:
        result = validate(r)
        if not result.is_valid:
            invalid += 1
            continue
        valid += 1
        out = normalize(r)
        all_identity_fields.extend(out.identity_fields)
        if out.ea_attributes:
            all_ea_attributes.append(out.ea_attributes)

    n_fields = load_identity_fields(con, all_identity_fields)
    ea_counts = batch_load_ea_attributes(con, all_ea_attributes)
    report.normalized_counts["identity_fields"] = report.normalized_counts.get("identity_fields", 0) + n_fields
    report.normalized_counts["ea_attributes"] = report.normalized_counts.get("ea_attributes", 0) + ea_counts["inserted"] + ea_counts["updated"]

    report.valid_per_source["ea_fc26"] = valid
    report.invalid_per_source["ea_fc26"] = invalid
    log.info("ea_fc26: fetched=%d valid=%d invalid=%d | ea_attributes=%s", len(records), valid, invalid, ea_counts)


def _ingest_transfermarkt_dataset(records: list, con: duckdb.DuckDBPyConnection, report: IngestionReport):
    report.fetched_per_source["transfermarkt_dataset"] = len(records)
    valid, invalid = 0, 0
    all_identity_fields = []
    all_market_values = []
    all_transfers = []
    all_appearances = []
    for r in records:
        result = validate(r)
        if not result.is_valid:
            invalid += 1
            continue
        valid += 1
        out = normalize(r)
        all_identity_fields.extend(out.identity_fields)
        if out.market_value:
            all_market_values.append(out.market_value)
        if out.transfer:
            all_transfers.append(out.transfer)
        if out.appearance:
            all_appearances.append(out.appearance)

    n_fields = load_identity_fields(con, all_identity_fields)
    n_mv = batch_load_market_values(con, all_market_values)
    n_tr = batch_load_transfers(con, all_transfers)
    n_ap = batch_load_appearances(con, all_appearances)

    report.normalized_counts["identity_fields"] = report.normalized_counts.get("identity_fields", 0) + n_fields
    report.normalized_counts["market_value"] = report.normalized_counts.get("market_value", 0) + n_mv
    report.normalized_counts["transfer"] = report.normalized_counts.get("transfer", 0) + n_tr
    report.normalized_counts["appearance"] = report.normalized_counts.get("appearance", 0) + n_ap

    report.valid_per_source["transfermarkt_dataset"] = valid
    report.invalid_per_source["transfermarkt_dataset"] = invalid
    log.info("transfermarkt_dataset: fetched=%d valid=%d invalid=%d", len(records), valid, invalid)


INGEST_FUNCTIONS = {
    "ea_fc26": _ingest_ea_fc26,
    "transfermarkt_dataset": _ingest_transfermarkt_dataset,
}


# ═══════════════════════════════════════════════════════════
# orchestration
# ═══════════════════════════════════════════════════════════

def _run_wikidata_enrichment(config: PipelineConfig, con: duckdb.DuckDBPyConnection, report: IngestionReport) -> None:
    """Non-blocking enrichment step (runs only after core matching + players exist).

    All matching/linking logic lives in matching/wikidata_enrichment.py and
    reuses the existing match_one()/DEFAULT_THRESHOLDS — nothing is
    reimplemented here. Its DB writes run in ONE transaction of their own:
    a mid-enrichment failure rolls back only the enrichment; the core
    EA/transfermarkt data was already committed and is never touched.
    Errors propagate to the caller, which records them in
    report.enrichment_errors without affecting `success`."""
    provider = PROVIDER_REGISTRY["wikidata"](config)
    if not provider.health_check():
        raise RuntimeError("health_check returned False (fixture missing or live endpoint unreachable)")

    records = list(provider.fetch())  # reads only — nothing to roll back if this fails
    valid_records = [r for r in records if validate(r).is_valid]
    report.fetched_per_source["wikidata"] = len(records)
    report.valid_per_source["wikidata"] = len(valid_records)
    report.invalid_per_source["wikidata"] = len(records) - len(valid_records)

    con.execute("BEGIN")
    try:
        counts = enrich_players_with_wikidata(con, valid_records)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    report.enrichment_counts.update(counts)
    log.info("wikidata enrichment: %s", counts)


def run_pipeline(config: PipelineConfig) -> IngestionReport:
    report = IngestionReport(started_at=datetime.now(timezone.utc).isoformat())
    log.info("starting ingestion pipeline | db=%s | providers=%s", config.db_path, config.enabled_providers)

    applied = run_migrations(config.db_path)
    if applied:
        log.info("applied migrations: %s", applied)

    con = duckdb.connect(str(config.db_path))
    ea_raw_records = []
    tm_raw_records = []

    core_provider_names = [p for p in config.enabled_providers if p not in ENRICHMENT_PROVIDERS]
    enrichment_provider_names = [p for p in config.enabled_providers if p in ENRICHMENT_PROVIDERS]

    try:
        for provider_name in core_provider_names:
            builder = PROVIDER_REGISTRY.get(provider_name)
            if builder is None:
                raise ProviderIngestionError(provider_name, ValueError("no registry entry for this provider"))

            provider = builder(config)
            log.info("provider %r: health_check...", provider_name)
            try:
                healthy = provider.health_check()
            except Exception as e:  # health_check itself must never crash the pipeline uncaught
                healthy = False
                report.provider_errors[provider_name] = f"health_check raised: {e}"

            if not healthy and provider_name not in report.provider_errors:
                report.provider_errors[provider_name] = "health_check returned False (source unavailable)"

            if provider_name in report.provider_errors:
                log.error("provider %r unhealthy: %s", provider_name, report.provider_errors[provider_name])
                if config.stop_on_provider_failure:
                    log.error("stop_on_provider_failure=True — aborting before matching/persistence")
                    report.finished_at = datetime.now(timezone.utc).isoformat()
                    report.success = False
                    return report
                continue

            try:
                con.execute("BEGIN")
                fetched_records = list(provider.fetch())
                INGEST_FUNCTIONS[provider_name](fetched_records, con, report)
                con.execute("COMMIT")
                if provider_name == "ea_fc26":
                    ea_raw_records = fetched_records
                elif provider_name == "transfermarkt_dataset":
                    tm_raw_records = [r for r in fetched_records if r.record_type == "player"]
            except Exception as e:
                con.execute("ROLLBACK")
                report.provider_errors[provider_name] = str(e)
                log.error("provider %r failed mid-ingestion, rolled back: %s", provider_name, e)
                if config.stop_on_provider_failure:
                    report.finished_at = datetime.now(timezone.utc).isoformat()
                    report.success = False
                    return report
                continue

        # matching only runs if both sides are present — otherwise every
        # EA player would trivially be UNMATCHED, which isn't a meaningful run
        if ea_raw_records and tm_raw_records:
            log.info("running identity matching: %d EA identities vs %d transfermarkt identities",
                      len(ea_raw_records), len(tm_raw_records))
            ea_identities = [from_ea_record(r) for r in ea_raw_records]
            tm_identities = [from_transfermarkt_record(r) for r in tm_raw_records]
            results = match_all(ea_identities, tm_identities)

            for r in results:
                report.matching_counts[r.best.status] = report.matching_counts.get(r.best.status, 0) + 1

            persist_identity_matches(con, results)
            create_player_records(con, results)
            n_review = persist_review_queue(con, results)
            log.info("review_queue: %d new pending item(s)", n_review)
            backfill_counts = backfill_player_uid(con)
            log.info("backfill_player_uid: %s", backfill_counts)
        else:
            log.warning("skipping matching — need both ea_fc26 and transfermarkt_dataset records loaded this run")

        for provider_name in enrichment_provider_names:
            try:
                _run_wikidata_enrichment(config, con, report)
            except Exception as e:
                # enrichment is NEVER allowed to abort the pipeline or affect
                # `success` — core data already persisted above is unaffected
                report.enrichment_errors[provider_name] = str(e)
                log.warning("enrichment provider %r failed (non-blocking): %s", provider_name, e)

        injury_n = mark_no_injury_source_available(con)
        log.info("injury_data_status rows created: %d", injury_n)

        for table in (
            "players", "player_field_values", "ea_fc26_attributes", "market_value_history",
            "transfers", "appearances", "injury_data_status", "identity_matches", "review_queue",
            "player_images", "player_wikidata_links",
        ):
            report.db_row_counts_after[table] = con.execute(f"select count(*) from {table}").fetchone()[0]

        report.success = len(report.provider_errors) == 0
    finally:
        con.close()

    report.finished_at = datetime.now(timezone.utc).isoformat()
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Football Intelligence ingestion pipeline")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--providers", type=str, default=None, help="comma-separated, e.g. ea_fc26,transfermarkt_dataset")
    parser.add_argument("--transfermarkt-dataset-version", type=str, default=None)
    parser.add_argument("--continue-on-error", action="store_true", help="do not abort on provider failure (default: abort)")
    parser.add_argument("--report-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    config = PipelineConfig.from_env()
    if args.db_path:
        config.db_path = args.db_path
    if args.providers:
        config.enabled_providers = tuple(p.strip() for p in args.providers.split(","))
    if args.transfermarkt_dataset_version:
        config.transfermarkt_dataset_version = args.transfermarkt_dataset_version
    if args.continue_on_error:
        config.stop_on_provider_failure = False
    if args.report_dir:
        config.report_dir = args.report_dir

    report = run_pipeline(config)
    report.print_summary()
    saved_path = report.save(config.report_dir)
    log.info("report saved to %s", saved_path)

    return 0 if report.success else 1


if __name__ == "__main__":
    sys.exit(main())
