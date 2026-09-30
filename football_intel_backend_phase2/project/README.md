# Football Player Intelligence Platform — Backend

## What this project is

A data ingestion, identity-matching, and enrichment backend for a
Football Player Intelligence Platform. It combines EA FC26 player
attribute data (real, full dataset), a CC0-licensed transfermarkt-
derived market-value dataset, and Wikidata (identity/image enrichment)
into one unified, source-tagged DuckDB database — with a scored,
confidence-thresholded, non-destructive identity-matching engine tying
the sources together.

**This repository is the source of truth for the project.** Do not rely
on prior chat/conversation context when this repository's contents are
available — see `HANDOFF.md`.

## Current status: Phase 2.6 complete

Phases 0 through 2.6 (audit → architecture → ingestion foundation →
persistence → orchestration → performance/scaling → review queue →
coverage reporting → Wikidata enrichment) are **fully implemented and
tested**. See `CURRENT_STATUS.md` for the exact breakdown and
`HANDOFF.md` for what a new contributor/agent needs to know first.

**Not built yet, and not claimed to be built**: no API layer, no
frontend/UI, no ML model integration. This is a data/backend layer
only. See `NEXT_PHASE.md`.

## What Phase 1 and Phase 2 completed

- **Phase 1**: DB schema + migrations, provider abstraction, EA FC26 and
  transfermarkt_dataset providers, validator, normalizer, identity
  matching engine (scoring, blocking, thresholds), in-memory review
  queue.
- **Phase 2.1**: Database loaders for all normalized data types.
- **Phase 2.2**: Pipeline orchestration (`scripts/run_ingestion.py`),
  configuration, ingestion reporting.
- **Phase 2.3**: Real performance fix (DuckDB bulk-insert, 185x
  measured speedup) + multi-key identity-matching blocking.
- **Phase 2.4**: Persistent (DB-backed) review queue.
- **Phase 2.5**: Coverage / data-quality reporting.
- **Phase 2.6**: Wikidata enrichment — provider, normalizer, loader,
  matching-engine reuse, non-blocking orchestrator integration, conflict
  protection, final validation.

## What Phase 2.6 specifically contains

- `ingestion/providers/wikidata.py` — fixture-based today (sample data,
  clearly labeled real vs. illustrative — see
  `data/raw/wikidata/sample/README.md`); architected for a live SPARQL
  endpoint later with no interface changes needed.
- `matching/wikidata_enrichment.py` — links `players` rows to Wikidata
  QIDs by reusing the exact same matching engine used for EA↔
  transfermarkt matching (no separate/looser logic).
- `db/migrations/0006_player_wikidata_links.sql` — a dedicated
  INSERT-only link table + `v_players` view, because DuckDB cannot
  `UPDATE` an indexed column (`players.wikidata_id`) once child rows
  reference that player — see `DATABASE_SCHEMA.md` for the full
  explanation.
- Wikidata enrichment is **non-blocking**: its failure is logged and
  reported, but never aborts the core EA/transfermarkt pipeline or
  flips the overall run to failed.

## Environment setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
Requires Python 3.11+ (developed and tested on 3.12). No external
services, API keys, or network access are required to run migrations,
tests, or the ingestion pipeline against the included sample/fixture
data.

## Running migrations

```bash
python3 db/migrate.py
```
Idempotent — safe to run repeatedly. Creates
`data/processed/unified/football_intel.duckdb` (gitignored /
not included in this export — it's a generated artifact, not source)
and applies any migration in `db/migrations/*.sql` not yet recorded in
the `schema_migrations` table.

## Running tests

```bash
python3 -m pytest tests/
```
Expected: **190 passed**. No network access needed — all provider tests
run against local files (the real EA FC26 CSV, included in this export
at `data/raw/ea_fc26/fc26_merged_clean.csv`, and the labeled sample
fixtures under `data/raw/transfermarkt_dataset/` and
`data/raw/wikidata/`).

## Running the ingestion pipeline

```bash
python3 scripts/run_ingestion.py
```
Runs migrations, fetches/validates/normalizes/matches/persists all
three sources, enriches via Wikidata (non-blocking), and writes a JSON
report to `data/processed/unified/reports/`. See `INGESTION_PIPELINE.md`
for CLI flags and configuration options.

Coverage/data-quality report (separate, read-only, run on demand):
```bash
python3 scripts/generate_coverage_report.py
```

## Where things are

| What | Where |
|---|---|
| Real EA FC26 data (16,107 players) | `data/raw/ea_fc26/fc26_merged_clean.csv` |
| Transfermarkt sample fixture (labeled real vs. illustrative) | `data/raw/transfermarkt_dataset/sample_2026-07-06/` |
| Wikidata sample fixture (labeled real vs. illustrative) | `data/raw/wikidata/sample/` |
| Database (generated, not included) | `data/processed/unified/football_intel.duckdb` |
| Migrations | `db/migrations/*.sql` |
| Pipeline entrypoint | `scripts/run_ingestion.py` |
| Coverage report entrypoint | `scripts/generate_coverage_report.py` |
| Tests | `tests/` (mirrors the source layout) |

See `PROJECT_MANIFEST.md` for a complete file-by-file breakdown.

## What has NOT been done yet

No API/service layer, no frontend, no UI, no ML model integration. The
original university ML model was fully audited (Phase 0) but has not
been wired into this pipeline. See `KNOWN_ISSUES.md` for a full,
honest list of current limitations (sample-fixture data volume for two
of three sources, no injury data source approved yet, etc.).

## Recommended next phase

Backend API / service layer, built against the schema and data shapes
already stable and tested here — **before** any frontend/UI or ML
integration work. Full reasoning in `NEXT_PHASE.md`.

## What should NOT be started yet

Do not start: an API layer, ML model integration, a frontend, a UI, or
any website work, until the recommended next phase has been explicitly
scoped and approved. See `HANDOFF.md` for the explicit "DO NOT START"
list.
