# PROJECT_MANIFEST.md

Complete file/folder breakdown of this repository export. 78 files total
(11 root docs + 1 requirements.txt + 1 pyproject.toml + 55 source/test
files + 10 data/fixture files).

## Root

| File | Role |
|---|---|
| `README.md` | Project overview, setup, how to run migrations/tests/pipeline |
| `HANDOFF.md` | **Read first in a new session.** Chat-to-chat continuity: exact state, invariants, known bugs, DO NOT START list |
| `PROJECT_MANIFEST.md` | This file |
| `ARCHITECTURE.md` | Full backend architecture, directory layout, architectural boundaries |
| `CURRENT_STATUS.md` | Phase-by-phase completion status, providers, migrations, limitations |
| `DATA_FLOW.md` | Actual current data flow, core ingestion vs. non-blocking enrichment |
| `DATABASE_SCHEMA.md` | Every table: purpose, columns, constraints, behavior, migration history |
| `INGESTION_PIPELINE.md` | Pipeline entrypoint, config, failure handling, transactions, performance |
| `MATCHING_AND_ENRICHMENT.md` | Blocking strategy, scoring, thresholds, wikidata adapter, no-fabrication rules |
| `KNOWN_ISSUES.md` | Only real, verified limitations — no hypotheticals |
| `NEXT_PHASE.md` | Recommended next phase (API layer) and why, with a concrete endpoint list |
| `TEST_STATUS.md` | What each test file actually exercises, not just pass/fail counts |
| `requirements.txt` | Pinned: duckdb, pandas, pytest |
| `pyproject.toml` | pytest configuration (`pythonpath`, `testpaths`) |

## `config/` — pipeline configuration

| File | Role |
|---|---|
| `settings.py` | `PipelineConfig` dataclass, env var overrides, `ALL_PROVIDERS`, `ENRICHMENT_PROVIDERS` |

## `db/` — schema management

| File | Role |
|---|---|
| `migrate.py` | Idempotent migration runner (tracks applied versions in `schema_migrations`) |
| `migrations/0001_init.sql` | Initial schema — 11 core tables |
| `migrations/0002_review_queue.sql` | `review_queue` table + `identity_matches` grouping columns |
| `migrations/0003_source_player_id_link.sql` | `player_id_in_source` on time-series tables (bug fix) |
| `migrations/0004_allow_null_player_uid_pre_match.sql` | Relax `NOT NULL` (bug fix) |
| `migrations/0005_ea_attributes_dataset_version.sql` | Separate `dataset_version`/`model_version` (bug fix) |
| `migrations/0006_player_wikidata_links.sql` | `player_wikidata_links` table + `v_players` view (bug fix, see `DATABASE_SCHEMA.md`) |

## `ingestion/` — fetch, validate, normalize, load

| File | Role |
|---|---|
| `providers/base.py` | `Provider` ABC, `RawRecord`, `StaticDatasetProvider`, `APIProvider` |
| `providers/ea_fc26.py` | Reads the real `fc26_merged_clean.csv` (16,107 players), read-only |
| `providers/transfermarkt_dataset.py` | Reads a static CC0-dump-shaped local dump (currently the sample fixture); zero network calls |
| `providers/wikidata.py` | Reads a local fixture today; architected for live SPARQL later (see its docstring for the exact query shape) |
| `validator.py` | Schema/range checks per `(source, record_type)`; pure function, never mutates input |
| `normalizer.py` | Raw fields → canonical dataclasses (`IdentityFieldValue`, `EAAttributesRecord`, `MarketValueRecord`, `TransferRecord`, `AppearanceRecord`, `ImageRecord`); enforces the value-field separation rule |
| `loader.py` | DB writes: batch loaders (`batch_load_*`, real bulk insert), per-record variants, `backfill_player_uid()` |
| `injury_loader.py` | The one honest writer for `injury_data_status` — always `NO_SOURCE_AVAILABLE`, never fabricated |

## `matching/` — identity resolution and enrichment

| File | Role |
|---|---|
| `schema.py` | `IdentityInput`, `MatchThresholds`, `MatchCandidate`, `MatchResult` |
| `identity.py` | Scoring (`score_pair`), classification, blocking (4 keys), `match_one`/`match_all`, `from_ea_record`/`from_transfermarkt_record`/`from_wikidata_record` adapters, `build_identity_input_for_player` |
| `loader.py` | `identity_matches` persistence (real bulk insert), `create_player_records()` (non-destructive) |
| `review_queue.py` | In-memory review queue (Phase 1.5) |
| `review_persistence.py` | DB-backed review queue (Phase 2.4): `persist_review_queue`, `list_pending`, `resolve`, `promote_review_decision` |
| `wikidata_enrichment.py` | `enrich_players_with_wikidata()` — reuses the identity engine unchanged; conflict-safe, non-fabricating |

## `reporting/` — read-only data quality

| File | Role |
|---|---|
| `coverage_report.py` | `build_coverage_report()` — matching stats, field completeness, lineage coverage, injury status, market-value coverage (kept separate), missing-data summary |

## `api/` — read-only API (`getHealth`, `getDataFreshness`, `searchPlayers`, `getPlayer`)

Layers (see `ARCHITECTURE_API.md`): `app.py` (factory), `config.py`, `errors.py`; `domain/` (enums, models, `normalize`,
`source_registry`, `link_rules`, `player_index`, `search`, `player_id`; stdlib only); `readmodels/definitions.py` (the ONLY SQL text);
`repositories/` (read-only DuckDB, RM1/RM2/RM3, freshness, batch player details); `services/` (startup integrity, health, freshness, player summary, search, player);
`schemas/` (Pydantic, mirrors `api_contract/openapi.json`); `routers/` (thin HTTP). Never imports `ingestion/`, `matching/`, pandas or ML libraries.
Run: `uvicorn api.app:create_app --factory`.

## `scripts/` — entrypoints

| File | Role |
|---|---|
| `run_ingestion.py` | **THE pipeline entrypoint.** `run_pipeline(config)`, `PROVIDER_REGISTRY`, core-vs-enrichment split, `IngestionReport` |
| `generate_coverage_report.py` | CLI for the coverage report |
| `benchmark_loaders.py` | Standalone old-vs-new loader performance comparison (documents the Phase 2.3 185x fix) |

## `tests/` — 527 tests, mirrors the source layout

| Path | Covers |
|---|---|
| `test_providers/` | `base.py`, `ea_fc26.py` (real data), `transfermarkt_dataset.py`, `wikidata.py` |
| `test_validator/` | `validator.py` |
| `test_normalizer/` | `normalizer.py`, `loader.py`, `injury_loader.py` |
| `test_matching/` | `identity.py`, `loader.py`, `review_queue.py`, `review_persistence.py`, `wikidata_enrichment.py`, `wikidata` matching adapters |
| `test_pipeline/` | `run_ingestion.py` (full smoke test, failure handling, idempotency, performance regression, wikidata orchestration) |
| `test_reporting/` | `coverage_report.py` (synthetic + real-data smoke test) |
| `fixtures/small_ea_fc26.csv` | 50-row EA fixture (includes 5 real known players) for fast pipeline tests |

## `data/` — raw inputs only (no generated/processed data included)

| Path | Role |
|---|---|
| `raw/ea_fc26/fc26_merged_clean.csv` | **Real, full dataset.** 16,107 players. Verified in Phase 0 to be byte-for-byte reproducible from the original 4 raw EA/FC26 source files. |
| `raw/transfermarkt_dataset/sample_2026-07-06/` | **Sample fixture, labeled.** Real player identifiers (transfermarkt_id, name, nationality, position, club); illustrative market values/fees/appearance stats. See its own `README.md`. |
| `raw/wikidata/sample/players.csv` | **Sample fixture, labeled.** One fully real+verified row (Haaland, QID Q28967995); four illustrative-QID rows with real name/DOB/nationality facts. See its own `README.md`. |
| `processed/unified/` | Empty (`.gitkeep` only) — this is where `football_intel.duckdb` and generated reports live once you run migrations/pipeline. Not included in this export; it's a generated artifact, not source. |
| `mappings/` | Empty (`.gitkeep` only) — reserved, currently unused (the `identity_matches` table absorbed this role). |

## What was deliberately excluded from this export

- `__pycache__/`, `*.pyc`, `.pytest_cache/` — build artifacts, regenerate automatically
- `*.duckdb` — the database itself is generated by `db/migrate.py` +
  `scripts/run_ingestion.py`, not source
- `data/processed/unified/reports/*.json` — generated ingestion/coverage
  reports, regenerate on demand
- Any `.env`, credentials, tokens, or secrets — none exist in this
  project (no live API keys are used anywhere; the wikidata/transfermarkt
  providers are fixture-based, not authenticated API calls)
